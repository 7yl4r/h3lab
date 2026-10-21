"""Export the regional build: res-7 overview + res-8 region shards + manifest.

Reuses `binary.py`'s quantised block format and extends it with the
change-layer fields.

Two levels, per D-21:

* one **overview** bundle holding every res-7 cell over the whole bbox, which
  is what the viewer loads first (and what the sub-3 s cold-load budget is
  measured against);
* one **region shard** per H3 res-4 parent, holding the res-8 cells inside
  it, loaded on demand as the viewport moves.

Note on the region count: PHASE1 §7 says "37 regions", which is the number of
res-4 cells whose hexagon lies *entirely* inside the bbox. The res-8 grid is
built by the same whole-hexagon rule against the bbox, so its cells reach into
res-4 parents that the bbox clips -- 72 of them. Grouping by parent (which is
what "one shard per H3 res-4 parent" means) therefore yields 72 shards, and
the alternative, dropping every res-8 cell whose parent is clipped, would
throw away 31 % of the area the mosaic was just computed for.
"""

from __future__ import annotations

import json
import os
from datetime import date

import numpy as np

from .binary import write_block
from .config import BAND_INFO, BANDS, INDICES, INDEX_SPECS, REGION_RES
from .features import compute_indices
from .shard import compute_content_hash, group_cells_by_region


PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROUND = {"band": 4, "index": 4, "other": 4}
SCHEMA_VERSION = 2


def _clean(arr: np.ndarray, nd: int) -> list:
    a = np.asarray(arr, dtype=np.float64)
    a = np.where(np.isfinite(a), np.round(a, nd), None)
    return a.tolist()


def _periods(bins_list) -> list:
    from . import bins

    periods = []
    for b in bins_list:
        centre = bins.bin_centre(b)
        periods.append({
            "bin": int(b),
            "date": centre.isoformat(),
            "start": bins.bin_start(b).isoformat(),
            "end": bins.bin_end(b).isoformat(),
            "year": centre.year,
            "month": centre.month,
        })
    return periods


def export_regional(cfg, grids, composites_by_bin, bins_list,
                     change_by_res=None, gz: bool = True):
    """Write the overview bundle, the region shards and the manifest."""
    out_dir = os.path.join(PROJECT_DIR, cfg.data_dir, "regional")
    os.makedirs(out_dir, exist_ok=True)
    export_shards(cfg, grids, composites_by_bin, bins_list, out_dir, gz=gz,
                  change_by_res=change_by_res)
    return write_manifest(cfg, grids, bins_list, out_dir, gz=gz)


def export_shards(cfg, grids, composites_by_bin, bins_list, out_dir,
                  gz: bool = True, change_by_res=None):
    """Write the bundles: the res-7 overview and the res-8 region shards.

    Returns {"overview": {...} | None, "regions": [...]}. The manifest is a
    separate stage (`write_manifest`), so the bundles can be rebuilt without
    rewriting the index, and the index can be rebuilt from the bundles alone.
    """
    periods = _periods(bins_list)

    overview = None
    if 7 in grids:
        overview = export_overview(cfg, grids[7], composites_by_bin,
                                   periods, out_dir, gz)

    regions = []
    if 8 in grids:
        change = (change_by_res or {}).get(8)
        regions = export_region_shards(cfg, grids[8], composites_by_bin,
                                       periods, out_dir, gz, change=change)
    return {"overview": overview, "regions": regions}


def write_manifest(cfg, grids, bins_list, out_dir, gz: bool = True):
    """Write the manifest from the bundles that are on disk.

    The manifest is derived, not remembered: every entry is read back out of
    the file it describes -- its descriptor for the shape, its bytes for the
    size, its content for the hash. A manifest that disagreed with the
    bundles would be worse than no manifest at all.
    """
    from . import bins
    from .binary import read_descriptor

    periods = _periods(bins_list)
    suffix = ".bin.gz" if gz else ".bin"

    coarse = min(grids) if grids else None
    manifest = {
        "name": getattr(cfg, "aoi_name", "South Florida Regional"),
        "bbox": list(getattr(cfg, "aoi_bbox", [])),
        "crs": cfg.analysis_crs,
        "schema_version": SCHEMA_VERSION,
        "epoch": str(bins.EPOCH),
        "bin_days": bins.BIN_DAYS,
        "bands": list(BANDS),
        "band_info": {k: dict(BAND_INFO[k]) for k in BANDS if k in BAND_INFO},
        "indices": list(INDICES),
        "index_specs": cfg.index_specs,
        "periods": periods,
        "region_res": REGION_RES,
        "overview": None,
        "regions": [],
        # The same annotation the pilot carries, so the regional timeline can
        # mark Irma (10 Sep 2017) on the bin it falls in.
        "events": cfg.events,
        "source": "Landsat Collection 2 Level-2 surface reflectance (NASA/USGS)",
        "via": "Microsoft Planetary Computer STAC",
        "note": (
            "Surface reflectance and spectral indices only. No land-cover "
            "classification is applied anywhere in this pipeline."
        ),
    }
    if coarse is not None:
        manifest["aoiAreaKm2"] = round(grids[coarse]._aoi_area, 2)

    if 7 in grids:
        path = os.path.join(out_dir, f"overview_res7{suffix}")
        if os.path.exists(path):
            desc = read_descriptor(path)
            manifest["overview"] = {
                "file": os.path.basename(path),
                "res": 7,
                "nCells": int(desc["nCells"]),
                "nPeriods": int(desc["nPeriods"]),
                "bytes": int(os.path.getsize(path)),
                "hash": _hash_of(path),
            }

    if 8 in grids:
        grid = grids[8]
        groups = group_cells_by_region(grid, REGION_RES)
        for parent in sorted(groups):
            path = os.path.join(out_dir, f"region_{parent}_res8{suffix}")
            if not os.path.exists(path):
                continue
            desc = read_descriptor(path)
            sub_grid = grid.subset(np.array(groups[parent]))
            lon, lat = sub_grid.centroids_lonlat[:, 0], sub_grid.centroids_lonlat[:, 1]
            manifest["regions"].append({
                "region": parent,
                "res": 8,
                "nCells": int(desc["nCells"]),
                "nPeriods": int(desc["nPeriods"]),
                "file": os.path.basename(path),
                "bytes": int(os.path.getsize(path)),
                "hash": _hash_of(path),
                "lon": round(float(lon.mean()), 4),
                "lat": round(float(lat.mean()), 4),
                "bbox": [round(float(lon.min()), 4), round(float(lat.min()), 4),
                         round(float(lon.max()), 4), round(float(lat.max()), 4)],
            })

    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        # Sorted keys and no separators: the manifest must be byte-identical
        # across rebuilds of the same data.
        json.dump(manifest, fh, sort_keys=True, separators=(",", ":"))

    return manifest


def build_block_from_reduced(grid, periods, composites_res, change_data=None):
    """Build the block dict in the binary format's terms."""
    n_cells = len(grid)
    block = {
        "periods": periods,
        "nCells": n_cells,
        "ids": [str(c) for c in grid.cells],
        "lon": np.round(grid.centroids_lonlat[:, 0], 6).tolist(),
        "lat": np.round(grid.centroids_lonlat[:, 1], 6).tolist(),
        "areaKm2": np.round(grid.areas_km2, 6).tolist(),
        "band": {}, "index": {},
        "observed": [], "validFraction": [], "nObs": [],
    }

    for reduced in composites_res:
        refl = reduced["refl"]
        valid = reduced.get("observed", np.ones(n_cells, dtype=bool))
        feats = compute_indices(refl)
        for b in BANDS:
            block["band"].setdefault(b, []).append(_clean(refl[b], ROUND["band"]))
        for k in INDICES:
            block["index"].setdefault(k, []).append(_clean(feats[k], ROUND["index"]))
        block["observed"].append([bool(v) for v in valid])
        vf = reduced.get("valid_fraction", np.ones(n_cells, dtype=np.float32))
        block["validFraction"].append(_clean(vf, 3))
        nobs = reduced.get("n_obs", np.ones(n_cells, dtype=np.int32))
        block["nObs"].append([int(v) for v in nobs])

    block["_bands"] = list(BANDS)
    block["_indices"] = list(INDICES)

    nverts, flat = [], []
    for cell in grid.cells:
        import h3

        ring = h3.cell_to_boundary(str(cell))
        nverts.append(len(ring))
        for lat, lng in ring:
            flat.append(lng)
            flat.append(lat)
    block["_nVerts"] = nverts
    block["_geomXY"] = flat

    if change_data is not None:
        block["change"] = _change_block(change_data)

    return block


def _change_block(change_data: dict) -> dict:
    """Turn the detector's output into the binary writer's change fields."""
    from .bins import EPOCH

    methods = sorted({str(m) for m in change_data["method"]})
    method_index = {m: i for i, m in enumerate(methods)}
    method_idx = np.array([method_index[str(m)] for m in change_data["method"]],
                           dtype=np.int64)

    break_date = change_data.get("break_date")
    if break_date is None or len(break_date) == 0:
        break_days = np.full(len(change_data["break_bin"]), -1, dtype=np.int64)
    else:
        break_days = np.array(
            [(date.fromisoformat(str(d)) - EPOCH).days if str(d) else -1
             for d in break_date], dtype=np.int64)

    return {
        "break_bin": [int(x) for x in change_data["break_bin"]],
        "break_date": break_days,
        "break_magnitude": _round4(change_data["break_magnitude"]),
        "break_score": _round4(change_data["break_score"]),
        "n_obs_used": [int(x) for x in change_data["n_obs_used"]],
        "method": method_idx,
        "methods": methods,
    }


def _round4(values) -> np.ndarray:
    """Per-cell float array rounded to 4 dp (NaN preserved)."""
    arr = np.asarray(values, dtype=np.float64).ravel()
    return np.round(arr, 4)


def export_overview(cfg, grid, composites_by_bin, periods, out_dir, gz):
    """The single res-7 bundle covering the whole bbox."""
    pairs = _composites_for(composites_by_bin, periods, 7)
    if not pairs:
        return None
    used_periods = [p for p, _ in pairs]
    comps = [r for _, r in pairs]
    block = build_block_from_reduced(grid, used_periods, comps)
    _stamp_epoch(block)
    info = write_block(block, os.path.join(out_dir, "overview_res7.bin"), gz=gz)
    return {
        "file": os.path.basename(info["path"]),
        "res": 7,
        "nCells": len(grid),
        "nPeriods": len(comps),
        "bytes": int(os.path.getsize(info["path"])),
        "hash": _hash_of(info["path"]),
    }


def _stamp_epoch(block: dict):
    """Carry the epoch and the bin length in the descriptor (PHASE1 §6.5)."""
    from . import bins

    block["epoch"] = str(bins.EPOCH)
    block["binDays"] = bins.BIN_DAYS


def export_region_shards(cfg, grid, composites_by_bin, periods, out_dir, gz,
                         change=None):
    """One shard per H3 res-4 parent, carrying that region's res-8 cells."""
    groups = group_cells_by_region(grid, REGION_RES)
    shards = []
    for parent in sorted(groups):
        indices = groups[parent]
        sub_grid = grid.subset(np.array(indices))
        pairs = _composites_for(composites_by_bin, periods, 8, indices)
        if not pairs:
            continue
        used_periods = [p for p, _ in pairs]
        comps = [r for _, r in pairs]
        sub_change = None
        if change is not None:
            sub_change = _subset_change(change, indices)
        block = build_block_from_reduced(sub_grid, used_periods, comps,
                                         change_data=sub_change)
        _stamp_epoch(block)
        stem = os.path.join(out_dir, f"region_{parent}_res8.bin")
        info = write_block(block, stem, gz=gz)
        lon, lat = sub_grid.centroids_lonlat[:, 0], sub_grid.centroids_lonlat[:, 1]
        shards.append({
            "region": parent,
            "res": 8,
            "nCells": len(sub_grid),
            "nPeriods": len(comps),
            "file": os.path.basename(info["path"]),
            "bytes": int(os.path.getsize(info["path"])),
            "hash": _hash_of(info["path"]),
            "lon": round(float(lon.mean()), 4),
            "lat": round(float(lat.mean()), 4),
            "bbox": [round(float(lon.min()), 4), round(float(lat.min()), 4),
                     round(float(lon.max()), 4), round(float(lat.max()), 4)],
        })
    return shards


def _composites_for(composites_by_bin, periods, res, indices=None):
    """The composite series for one resolution, in period order.

    Returns (period, composite) pairs rather than composites alone. A period
    with no composite is skipped, and if it were skipped silently the caller's
    period list would slide out of step with the data it describes -- the
    series would be labelled with the wrong dates. Pairing them keeps the two
    together.
    """
    out = []
    for p in periods:
        b = p["bin"]
        for (bin_num, r, reduced) in composites_by_bin:
            if bin_num == b and r == res:
                out.append((p, subset_reduced(reduced, indices)
                            if indices is not None else reduced))
                break
    return out


def _subset_change(change: dict, indices) -> dict:
    idx = np.asarray(indices)
    out = {}
    for key in ("break_bin", "break_date", "break_magnitude", "break_score",
                "n_obs_used", "method"):
        if key in change:
            out[key] = np.asarray(change[key])[idx]
    return out


def subset_reduced(reduced, indices):
    idx = np.array(indices)
    new_reduced = {}
    new_reduced["refl"] = {b: reduced["refl"][b][idx] for b in reduced["refl"]}
    new_reduced["observed"] = reduced["observed"][idx]
    new_reduced["valid_fraction"] = reduced.get(
        "valid_fraction", np.ones(len(idx), dtype=np.float32))[idx]
    new_reduced["n_obs"] = reduced.get(
        "n_obs", np.ones(len(idx), dtype=np.int32))[idx]
    return new_reduced


def _hash_of(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return compute_content_hash(fh.read())
    except OSError:
        return ""
