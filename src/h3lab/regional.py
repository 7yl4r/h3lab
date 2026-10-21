"""Staged regional pipeline (Phase 1).

    python -m h3lab.run --regional --year 2017 --resolutions 7,8
    python -m h3lab.run --regional --year 2017 --stage change,shard,index

The stages run in a fixed order and each one leaves its result on disk, so an
interrupted build resumes where it stopped and any single stage can be re-run
on its own:

    stac    -> <cache>/scenes.json
    mosaic  -> <mosaic_cache>/bin<N>/mosaic_bin<N>_<band>.tif
               <mosaic_cache>/bin<N>/nobs_bin<N>.tif
    h3      -> <cache>/bin<N>_res<R>.npz
    change  -> <cache>/change_res<R>.npz
    shard   -> <out>/overview_res7.bin.gz, <out>/region_<parent>_res8.bin.gz
    index   -> <out>/manifest.json

Splitting the build this way is not bookkeeping for its own sake. The mosaic
stage is the only one that touches the network: it reads every scene of every
bin, which for a year of South Florida is tens of gigabytes and several hours.
Everything after it is arithmetic over files already on disk, and can be
re-run in seconds. Keeping the network stage separate means a rebuild after a
code change costs minutes instead of hours, and it makes the expensive stage
the one that is explicitly cacheable.

The order of the stages is also the order of the physics:

    stac    which scenes exist, and are they allowed to count
    mosaic  per-pixel temporal median within a 16-day bin (the pixels)
    h3      spatial mean of the mosaic into H3 cells (the cells)
    change  break detection over the res-8 NDVI series
    shard   the block format, split by H3 res-4 parent
    index   the manifest that tells the viewer what exists

Mosaic-first is deliberate and is the one place this pipeline reverses the
pilot's order (DESIGN.md §3.4): the temporal median has to be taken per
*pixel*, before the pixels are pooled into cells, otherwise a cell's mean is
contaminated by whichever single acquisition happened to be clear.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date as _date
from datetime import timedelta

import numpy as np
import rasterio
from shapely.geometry import box

from . import bins
from . import change as change_mod
from . import mosaic
from . import stac
from .aggregate import aggregate_mosaic
from .composite import load_composite, save_composite
from .config import Config, PLATFORMS, REGIONAL_AOI_BBOX, REGIONAL_AOI_NAME
from .export_region import export_shards, write_manifest
from .features import _ratio
from .grid import analysis_grid, build_grid_for, build_label_raster


PROJECT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE = os.path.join(PROJECT, "data", "cache_regional")
MOSAIC_CACHE = os.path.join(PROJECT, "data", "mosaic_cache")
OUT_DIR = os.path.join(PROJECT, "data", "regional")

STAGES = ("stac", "mosaic", "h3", "change", "shard", "index")
STAGE_ORDER = {name: i for i, name in enumerate(STAGES)}


def _log(msg: str):
    print(msg, flush=True)


class RegionalBuild:
    """The regional pipeline, one method per stage.

    Every stage reads what it needs from the disk cache rather than from the
    stage before it, so stages can be run in isolation and in any order that
    respects the dependencies.
    """

    def __init__(self, cfg: Config | None = None, year: int = 2017,
                 resolutions=(7, 8), bins_list=None, cache_dir: str = CACHE,
                 mosaic_dir: str = MOSAIC_CACHE, out_dir: str = OUT_DIR,
                 force: bool = False, gz: bool = True, workers: int = 12,
                 log=_log):
        self.cfg = cfg if cfg is not None else Config()
        # Config is a frozen dataclass; the regional build overrides the AOI.
        object.__setattr__(self.cfg, "aoi_bbox", REGIONAL_AOI_BBOX)
        object.__setattr__(self.cfg, "aoi_name", REGIONAL_AOI_NAME)
        self.year = int(year)
        self.resolutions = [int(r) for r in resolutions]
        self.bins_list = list(bins_list) if bins_list else bins.bins_for_year(year)
        self.cache_dir = cache_dir
        self.mosaic_dir = mosaic_dir
        self.out_dir = out_dir
        self.force = force
        self.gz = gz
        self.workers = int(workers)
        self.log = log
        self._grids = None
        self._labels = None
        self._n_cells = None

    # -- paths --------------------------------------------------------------
    @property
    def scenes_path(self) -> str:
        return os.path.join(self.cache_dir, "scenes.json")

    def mosaic_dir_for(self, b: int) -> str:
        return os.path.join(self.mosaic_dir, f"bin{b}")

    def composite_path(self, b: int, r: int) -> str:
        return os.path.join(self.cache_dir, f"bin{b}_res{r}.npz")

    def change_path(self, r: int) -> str:
        return os.path.join(self.cache_dir, f"change_res{r}.npz")

    # -- grids --------------------------------------------------------------
    def grids(self):
        """H3 grids and label rasters for every requested resolution."""
        if self._grids is None:
            self.log("[grid] building H3 grids")
            all_grids = build_grid_for(self.cfg.aoi_bbox, tuple(self.resolutions),
                                       self.cfg.analysis_crs, self.cfg.pixel_size)
            grids = {r: all_grids[r] for r in self.resolutions}
            target = analysis_grid(self.cfg.aoi_bbox, self.cfg.analysis_crs,
                                   self.cfg.pixel_size)
            labels = {r: build_label_raster(grids[r], target)[0]
                      for r in self.resolutions}
            self._grids = grids
            self._labels = labels
            self._n_cells = {r: len(grids[r]) for r in self.resolutions}
        return self._grids, self._labels, self._n_cells

    # -- stage: stac --------------------------------------------------------
    def stage_stac(self) -> dict:
        """Enumerate the scenes of every bin and cache the list.

        The cached manifest holds *unsigned* asset URLs (stac.unsigned_url), so
        it stays valid across the several hours a regional build takes: the
        SAS token is added at read time by stac.SIGNER.
        """
        self.log("[stac] enumerating scenes per bin")
        scenes_by_bin = {}
        for b in self.bins_list:
            scenes = stac.find_scenes_for_bin(
                self.cfg, bins.bin_start(b), bins.bin_end(b),
                box(*self.cfg.aoi_bbox), platforms=PLATFORMS,
            )
            if scenes:
                scenes_by_bin[b] = scenes
                self.log(f"  bin {b} ({bins.bin_start(b)}): {len(scenes)} scenes")
        self.log(f"  bins with scenes: {len(scenes_by_bin)}/{len(self.bins_list)}")

        payload = {
            "version": 1,
            "year": self.year,
            "bbox": list(self.cfg.aoi_bbox),
            "platforms": list(PLATFORMS),
            "bins": {str(b): [s.to_dict() for s in v]
                     for b, v in scenes_by_bin.items()},
        }
        os.makedirs(self.cache_dir, exist_ok=True)
        with open(self.scenes_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True, separators=(",", ":"))
        return scenes_by_bin

    def load_scenes(self) -> dict:
        """The cached scene manifest, or {} when there is none."""
        if not os.path.exists(self.scenes_path):
            return {}
        with open(self.scenes_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        return {int(b): [stac.Scene.from_dict(d) for d in v]
                for b, v in raw.get("bins", {}).items()}

    # -- stage: mosaic ------------------------------------------------------
    def stage_mosaic(self) -> dict:
        """Build the per-bin 30 m mosaics. This is the network-bound stage."""
        scenes_by_bin = self.load_scenes()
        if not scenes_by_bin:
            raise RuntimeError(
                f"no scene cache at {self.scenes_path}; run the stac stage first")

        self.log("[mosaic] building per-bin 30 m mosaics")
        out = {}
        for b in self.bins_list:
            scenes = scenes_by_bin.get(b)
            if not scenes:
                self.log(f"  bin {b}: no scenes, skipping")
                continue
            dest = self.mosaic_dir_for(b)
            paths = self._cached_mosaic(dest, b)
            if paths is not None and not self.force:
                self.log(f"  bin {b}: mosaic cached")
                out[b] = paths
                continue
            t0 = time.time()
            paths = mosaic.build_mosaic_bin(self.cfg, b, scenes, dest,
                                             workers=self.workers, log=self.log)
            self.log(f"  bin {b}: mosaic in {time.time() - t0:.0f}s")
            out[b] = paths
        return out

    def _cached_mosaic(self, dest: str, b: int):
        """Output paths of an already-built mosaic for this bin, or None.

        A file being present is not evidence that it finished. A build that
        was interrupted leaves a truncated raster behind, and treating that
        as a cache hit produces a mosaic with a hole in it. Each output is
        opened and checked against the grid's shape instead.
        """
        paths = {band: os.path.join(dest, f"mosaic_bin{b}_{band}.tif")
                 for band in self.cfg.bands}
        paths["nobs"] = os.path.join(dest, f"nobs_bin{b}.tif")
        if not all(os.path.exists(p) for p in paths.values()):
            return None
        _, (h, w), _ = analysis_grid(self.cfg.aoi_bbox, self.cfg.analysis_crs,
                                     self.cfg.pixel_size)
        for p in paths.values():
            try:
                with rasterio.open(p) as src:
                    if src.width != w or src.height != h:
                        return None
                    # A truncated GeoTIFF still has a complete header, so the
                    # shape check alone would pass it. Reading the last block
                    # proves the data is really there.
                    src.read(1, window=rasterio.windows.Window(w - 1, h - 1, 1, 1))
            except Exception:  # noqa: BLE001 -- any failure means "not usable"
                return None
        return paths

    # -- stage: h3 ----------------------------------------------------------
    def stage_h3(self) -> list:
        """Sample every mosaic into H3 cells, one npz per (bin, resolution)."""
        grids, labels, n_cells = self.grids()
        scenes_by_bin = self.load_scenes()
        self.log("[h3] aggregating mosaics into cells")

        composites_by_bin = []
        for b in self.bins_list:
            if b not in scenes_by_bin:
                continue
            cache_paths = {r: self.composite_path(b, r) for r in self.resolutions}
            if all(os.path.exists(p) for p in cache_paths.values()) and not self.force:
                for r in self.resolutions:
                    _, refl, sup = load_composite(cache_paths[r])
                    composites_by_bin.append((b, r, {"refl": refl, **sup}))
                self.log(f"  bin {b}: cached")
                continue

            dest = self.mosaic_dir_for(b)
            paths = self._cached_mosaic(dest, b)
            if paths is None:
                raise RuntimeError(
                    f"bin {b}: no mosaic in {dest}; run the mosaic stage first")

            built = self._sample_bin(b, paths, labels, n_cells)
            for (bin_num, r, reduced) in built:
                save_composite(cache_paths[r], r, self.year, f"bin{bin_num}",
                               reduced)
            composites_by_bin.extend(built)
            self.log(f"  bin {b}: {len(built)} resolutions aggregated")
        return composites_by_bin

    def _sample_bin(self, b: int, paths: dict, labels: dict, n_cells: dict):
        """Read a bin's mosaic once and bin it into every resolution.

        The raster is the same for every resolution; only the label image
        differs. Reading the six 97 Mpx bands once per resolution instead of
        once in total would multiply the I/O by the number of resolutions for
        no gain.
        """
        mosaic_bands = {}
        for band in self.cfg.bands:
            p = paths.get(band)
            if p and os.path.exists(p):
                mosaic_bands[band] = mosaic.read_mosaic_band(p)
        nobs_px = None
        if paths.get("nobs") and os.path.exists(paths["nobs"]):
            nobs_px = mosaic.read_mosaic_nobs(paths["nobs"])

        out = []
        for r in self.resolutions:
            agg = aggregate_mosaic(mosaic_bands, labels[r], n_cells[r],
                                   nobs=nobs_px)
            valid_fraction = (agg["n_valid"].astype(np.float32)
                              / np.maximum(agg["n_total"].astype(np.float32), 1))
            observed = ((valid_fraction >= self.cfg.min_valid_fraction)
                        & (agg["n_obs"] > 0))
            for band in self.cfg.bands:
                agg["bands"][band] = np.where(observed, agg["bands"][band], np.nan)
            reduced = {
                "refl": agg["bands"],
                "valid_fraction": valid_fraction.astype(np.float32),
                "n_obs": agg["n_obs"],
                "observed": observed,
            }
            out.append((b, r, reduced))
        return out

    def load_composites(self) -> list:
        """Every cached composite, as (bin, res, reduced) triples."""
        out = []
        for b in self.bins_list:
            for r in self.resolutions:
                p = self.composite_path(b, r)
                if not os.path.exists(p):
                    continue
                _, refl, sup = load_composite(p)
                out.append((b, r, {"refl": refl, **sup}))
        return out

    # -- stage: change ------------------------------------------------------
    @property
    def primary_res(self) -> int:
        """The resolution the change layer is computed on (res 8)."""
        return 8 if 8 in self.resolutions else max(self.resolutions)

    def stage_change(self) -> dict:
        """Detect breaks in the primary resolution's NDVI series."""
        primary = self.primary_res
        self.log(f"[change] detecting breaks on res {primary} NDVI")

        composites = self.load_composites()
        if not composites:
            raise RuntimeError(
                f"no composite cache in {self.cache_dir}; run the h3 stage first")

        by_bin = {b: r for (b, r, _) in composites}
        used_bins = [b for b in self.bins_list if b in by_bin]
        n_cells = len(self.grids()[0][primary])

        ndvi_series = np.full((len(used_bins), n_cells), np.nan, dtype=np.float32)
        for pi, b in enumerate(used_bins):
            for (bin_num, r, reduced) in composites:
                if bin_num == b and r == primary:
                    ndvi_series[pi, :] = _ratio(reduced["refl"]["nir"],
                                                reduced["refl"]["red"])
                    break

        breaks = change_mod.detect_breaks(ndvi_series, used_bins)
        n_breaks = int(np.sum(breaks["break_bin"] >= 0))
        self.log(f"  breaks in {n_breaks}/{n_cells} cells "
                 f"({100.0 * n_breaks / max(n_cells, 1):.1f}%)")

        self._save_change(primary, breaks)
        return {primary: breaks}

    def _save_change(self, res: int, breaks: dict):
        """Persist the change layer.

        `break_date` is stored as days since the epoch, not as a string: the
        descriptor carries the epoch, so the date is recoverable, and an
        integer survives a round trip through the block format unchanged.
        """
        break_date = breaks.get("break_date")
        if break_date is None or len(break_date) == 0:
            break_days = np.full(len(breaks["break_bin"]), -1, dtype=np.int32)
        else:
            break_days = np.array(
                [(_date.fromisoformat(str(d)) - bins.EPOCH).days if str(d) else -1
                 for d in break_date], dtype=np.int32)

        method = np.array([str(m) for m in breaks["method"]])
        os.makedirs(self.cache_dir, exist_ok=True)
        np.savez_compressed(
            self.change_path(res),
            break_bin=breaks["break_bin"].astype(np.int32),
            break_date_days=break_days,
            break_magnitude=breaks["break_magnitude"].astype(np.float32),
            break_score=breaks["break_score"].astype(np.float32),
            n_obs_used=breaks["n_obs_used"].astype(np.int32),
            method=method,
            method_spec=np.array([str(breaks.get("method_spec", ""))]),
            bins=np.array([int(b) for b in breaks.get("bins", [])], dtype=np.int32),
        )

    def load_change(self, res: int):
        """The cached change layer for `res`, or None."""
        path = self.change_path(res)
        if not os.path.exists(path):
            return None
        z = np.load(path, allow_pickle=False)
        days = z["break_date_days"]
        break_date = np.array(
            [(bins.EPOCH + timedelta(days=int(d))).isoformat() if int(d) >= 0 else ""
             for d in days], dtype=object)
        return {
            "break_bin": z["break_bin"],
            "break_date": break_date,
            "break_magnitude": z["break_magnitude"],
            "break_score": z["break_score"],
            "n_obs_used": z["n_obs_used"],
            "method": z["method"],
            "method_spec": str(z["method_spec"][0]) if "method_spec" in z else "",
        }

    # -- stage: shard -------------------------------------------------------
    def stage_shard(self) -> dict:
        """Write the overview bundle and the per-region res-8 shards."""
        grids, _, _ = self.grids()
        composites = self.load_composites()
        if not composites:
            raise RuntimeError(
                f"no composite cache in {self.cache_dir}; run the h3 stage first")
        change = self.load_change(self.primary_res)
        if change is None:
            self.log(f"  no change layer for res {self.primary_res}; "
                     f"shards will carry no change fields")
            change_by_res = {}
        else:
            change_by_res = {self.primary_res: change}

        os.makedirs(self.out_dir, exist_ok=True)
        self.log("[shard] writing bundles")
        info = export_shards(self.cfg, grids, composites, self.bins_list,
                             self.out_dir, gz=self.gz, change_by_res=change_by_res)
        self.log(f"  overview: {info['overview']}")
        self.log(f"  regions: {len(info['regions'])} shards")
        return info

    # -- stage: index -------------------------------------------------------
    def stage_index(self) -> dict:
        """Write the manifest that tells the viewer what exists."""
        grids, _, _ = self.grids()
        os.makedirs(self.out_dir, exist_ok=True)
        self.log("[index] writing manifest")
        manifest = write_manifest(self.cfg, grids, self.bins_list, self.out_dir,
                                  gz=self.gz)
        self.log(f"  manifest: {os.path.join(self.out_dir, 'manifest.json')}")
        return manifest

    # -- driver -------------------------------------------------------------
    def run(self, stages=None, gz: bool | None = None) -> dict:
        """Run the requested stages in order."""
        if gz is not None:
            self.gz = gz
        if stages is None:
            stages = list(STAGES)
        stages = [s for s in stages if s in STAGE_ORDER]
        if not stages:
            raise ValueError(f"no known stages in {stages}")

        result: dict = {}
        for name in STAGES:
            if name not in stages:
                continue
            result[name] = getattr(self, f"stage_{name}")()
        return result


def build_regional(year: int = 2017, resolutions=(7, 8), stages=None,
                   force: bool = False, gz: bool = True, cfg=None,
                   bins_list=None, workers: int = 12) -> dict:
    """Run the regional pipeline. Returns a summary dict."""
    build = RegionalBuild(cfg=cfg, year=year, resolutions=resolutions,
                          bins_list=bins_list, force=force, gz=gz,
                          workers=workers)
    result = build.run(stages=stages)
    return {
        "stages": list(result),
        "out_dir": build.out_dir,
        "cache_dir": build.cache_dir,
    }
