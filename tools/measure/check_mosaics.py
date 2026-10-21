"""Integrity check over every built mosaic.

A file being present is not evidence that it is usable: a build that died
mid-write leaves a truncated GeoTIFF behind, and a zero-byte file still has a
name. Each output is opened, checked against the analysis grid's shape, and
its last block is read.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import rasterio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from h3lab import bins                              # noqa: E402
from h3lab.config import Config, REGIONAL_AOI_BBOX  # noqa: E402
from h3lab.mosaic import analysis_grid             # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CACHE = os.path.join(ROOT, "data", "mosaic_cache")


def main():
    cfg = Config()
    object.__setattr__(cfg, "aoi_bbox", REGIONAL_AOI_BBOX)
    _, (h, w), _ = analysis_grid(cfg.aoi_bbox, cfg.analysis_crs, cfg.pixel_size)
    print(f"grid {w} x {h}")
    bl = bins.bins_for_year(2017)
    bad = []
    for b in bl:
        d = os.path.join(CACHE, f"bin{b}")
        files = {band: os.path.join(d, f"mosaic_bin{b}_{band}.tif")
                 for band in cfg.bands}
        files["nobs"] = os.path.join(d, f"nobs_bin{b}.tif")
        missing = [k for k, p in files.items() if not os.path.exists(p)]
        if missing:
            print(f"bin {b}: MISSING {missing}")
            bad.append(b)
            continue
        parts = []
        ok = True
        for k, p in files.items():
            try:
                with rasterio.open(p) as src:
                    if src.width != w or src.height != h:
                        raise ValueError(f"shape {src.width}x{src.height}")
                    arr = src.read(1, window=rasterio.windows.Window(w - 1, h - 1, 1, 1))
            except Exception as e:  # noqa: BLE001
                print(f"bin {b}: {k} UNUSABLE ({e})")
                ok = False
                continue
            if k == "nobs":
                nz = int((arr > 0).sum())
                parts.append(f"nobs>0 in {nz} px")
            else:
                mx = int(arr.max())
                if mx <= 0:
                    print(f"bin {b}: {k} ALL ZERO")
                    ok = False
                parts.append(f"{k} max {mx}")
        if ok:
            print(f"bin {b}: ok  " + ", ".join(parts))
        else:
            bad.append(b)
    print(f"\nbins checked: {len(bl)}, bad: {len(bad)} -> {bad}")


if __name__ == "__main__":
    main()
