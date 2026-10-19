"""Command-line runner for the hexagon time-series build.

    python -m h3lab.run --years 2016:2019 --resolutions 8,9,10
    python -m h3lab.run --years 2016:2019 --resolutions 8,9,10 --export

Stages are cached per composite so an interrupted run resumes rather than
re-reading every scene. Delete the cache directory to force a clean rebuild.
"""

from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
from shapely.geometry import box

from .composite import CompositeAccumulator, load_composite, save_composite
from .config import BANDS, Config
from .features import compute_indices
from .grid import analysis_grid, build_grid, build_label_raster
from .ingest import aggregate_scene, find_scenes, read_scene, scene_monthly_summary

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, "..", ".."))
CACHE = os.path.join(PROJECT, "data", "cache")


def parse_years(spec: str) -> list[int]:
    if ":" in spec:
        a, b = spec.split(":")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",")]


def build(cfg: Config, years: list[int], resolutions: list[int], force=False):
    aoi = box(*cfg.aoi_bbox)
    print(f"AOI  {cfg.aoi_name}  bbox={cfg.aoi_bbox}")
    print(f"CRS  {cfg.analysis_crs}   years={years[0]}-{years[-1]}   resolutions={resolutions}")

    print("\n[1/5] building H3 grids")
    all_grids = build_grid(cfg.aoi_bbox, tuple(cfg.resolutions), cfg.analysis_crs, cfg.pixel_size)
    grids = {r: all_grids[r] for r in resolutions}
    target = analysis_grid(cfg.aoi_bbox, cfg.analysis_crs, cfg.pixel_size)
    transform, shape_, _ = target
    labels = {r: build_label_raster(grids[r], target)[0] for r in resolutions}
    n_cells = {r: len(grids[r]) for r in resolutions}

    composites = []   # (year, season, res, reduced)
    t_start = time.time()

    for season in cfg.seasons:
        for year in years:
            paths = {
                r: os.path.join(CACHE, f"res{r}_{season}_{year}.npz") for r in resolutions
            }
            if all(os.path.exists(p) for p in paths.values()) and not force:
                for r in resolutions:
                    m, refl, sup = load_composite(paths[r])
                    composites.append((year, season, r, {"refl": refl, **sup}))
                print(f"[3/5] {season} {year}: cached")
                continue

            print(f"[2/5] searching {season} {year} ...", flush=True)
            scenes = find_scenes(cfg, year, season, aoi)
            info = scene_monthly_summary(scenes)
            print(f"[3/5] {season} {year}: {info['n_scenes']} scenes "
                  f"months={sorted(info['months'])} median_cloud={info['cloud_median']}")
            if not scenes:
                print("      !! no usable scenes, composite skipped")
                continue

            accs = {r: CompositeAccumulator(n_cells[r], cfg.composite_stat) for r in resolutions}
            t0 = time.time()
            for i, sc in enumerate(scenes, 1):
                try:
                    arr = read_scene(sc, transform, shape_, cfg.analysis_crs)
                except Exception as exc:  # noqa: BLE001
                    print(f"      !! {sc.item_id}: {exc}")
                    continue
                if arr is None:
                    continue
                agg = aggregate_scene(arr, labels, n_cells)
                for r in resolutions:
                    accs[r].add(r, agg[r])
                if i % 5 == 0 or i == len(scenes):
                    print(f"      {i}/{len(scenes)} scenes ({time.time()-t0:.0f}s)", flush=True)

            for r in resolutions:
                reduced = accs[r].reduce(r, cfg.min_valid_fraction)
                if reduced is None:
                    continue
                save_composite(paths[r], r, year, season, reduced)
                obs = int(reduced["observed"].sum())
                print(f"      res {r}: {obs:,}/{n_cells[r]:,} cells observed "
                      f"(median {np.median(reduced['n_obs']):.0f} obs/cell)")
                composites.append((year, season, r, reduced))

    print(f"\n[4/5] {len(composites)} composites in {time.time()-t_start:.0f}s")
    return grids, composites, labels, target


def export(cfg: Config, grids, composites, resolutions, gz: bool = True):
    from .export import export_bundle

    return export_bundle(cfg, grids, composites, resolutions, gz=gz)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", default="2016:2019",
                    help="year range '2016:2019' or comma list '2016,2017,2018'")
    ap.add_argument("--resolutions", default="8,9,10")
    ap.add_argument("--force", action="store_true", help="ignore the composite cache")
    ap.add_argument("--export", action="store_true", help="write the viewer JSON bundle")
    ap.add_argument("--no-gzip", action="store_true",
                    help="write uncompressed .bin blocks (larger, but no DecompressionStream)")
    args = ap.parse_args()

    cfg = Config()
    years = parse_years(args.years)
    resolutions = [int(r) for r in args.resolutions.split(",") if r]

    grids, composites, labels, target = build(cfg, years, resolutions, force=args.force)

    if args.export:
        stats = export(cfg, grids, composites, resolutions, gz=not args.no_gzip)
        stem = os.path.splitext(cfg.export_filename)[0]
        out_dir = os.path.join(PROJECT, cfg.data_dir)
        total = 0
        suffix = ".bin.gz" if not args.no_gzip else ".bin"
        for r in resolutions:
            p = os.path.join(out_dir, f"{stem}_res{r}{suffix}")
            if os.path.exists(p):
                total += os.path.getsize(p)
        kind = "gzipped binary" if not args.no_gzip else "uncompressed binary"
        print(f"\n[5/5] wrote {out_dir}/{stem}_meta.json + one {kind} block "
              f"per resolution ({total/1e6:.1f} MB served total)")
        for k, v in stats.items():
            print(f"      {k}: {v}")
    else:
        print("\n[5/5] skipped export (pass --export)")


if __name__ == "__main__":
    main()