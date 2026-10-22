"""Export the analysed composites as one compact binary bundle for the viewer.

The layout is columnar: for each resolution, an index is stored as a list of
per-composite arrays rather than a list of per-cell objects. That keeps the
payload small enough to load in a browser without millions of tiny objects.
Reflectance is not exported for the pilot: the per-cell assessment in
:mod:`h3lab.analysis` carries what the viewer needs, and six bands per
composite cost six times the bytes for one layer.
"""

from __future__ import annotations

import json
import os

import numpy as np

from .analysis import SPECS as ANALYSIS_SPECS, analyse
from .binary import write_block
from .config import BANDS, INDEX_SPECS, INDICES

# INDICES comes from config; do not shadow it with a local copy here.
ROUND = {"index": 4, "other": 4}

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _clean(arr: np.ndarray, nd: int) -> list:
    """NaN -> None, rounded, as a flat list."""
    a = np.asarray(arr, dtype=np.float64)
    a = np.where(np.isfinite(a), np.round(a, nd), None)
    return a.tolist()


def export_bundle(cfg, grids, composites, resolutions, gz: bool = True) -> dict:
    """Write one metadata file plus one file per resolution.

    A single combined file for 13 years x 2 seasons at three resolutions runs to
    tens of megabytes of JSON, which the browser has to download and parse
    before it can draw anything. Splitting per resolution means opening the
    viewer fetches a small index and then only the block actually on screen.
    """
    from .features import compute_indices

    by_res: dict[int, list] = {r: [] for r in resolutions}
    for year, season, res, reduced in composites:
        if res in by_res:
            by_res[res].append((year, season, res, reduced))

    out_dir = os.path.join(PROJECT_DIR, cfg.data_dir)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(cfg.export_filename)[0]

    meta = {
        "name": cfg.aoi_name,
        "bbox": list(cfg.aoi_bbox),
        "crs": cfg.analysis_crs,
        "aoiAreaKm2": round(grids[resolutions[0]]._aoi_area, 2),
        "indices": list(INDICES),
        "index_specs": cfg.index_specs,
        "analysis": ANALYSIS_SPECS,
        "events": cfg.events,
        "seasons": list(cfg.seasons),
        "source": "Landsat Collection 2 Level-2 surface reflectance (NASA/USGS)",
        "via": "Microsoft Planetary Computer STAC",
        "stem": stem,
        "resolutions": [],
        "note": (
            "Spectral indices and a per-cell assessment of the whole record. "
            "No land-cover classification is applied anywhere in this pipeline."
        ),
    }

    stats = {}
    for res in resolutions:
        items = sorted(by_res[res], key=lambda t: (t[0], list(cfg.seasons).index(t[1])))
        if not items:
            continue
        grid = grids[res]
        periods = [{"year": y, "season": s} for y, s, _, _ in items]
        n_cells = len(grid)

        block = {
            "periods": periods,
            "nCells": n_cells,
            "ids": [str(c) for c in grid.cells],
            "lon": np.round(grid.centroids_lonlat[:, 0], 6).tolist(),
            "lat": np.round(grid.centroids_lonlat[:, 1], 6).tolist(),
            "areaKm2": np.round(grid.areas_km2, 6).tolist(),
            "index": {},
            "observed": [], "validFraction": [], "nObs": [],
        }

        # Raw (unrounded) series for the assessment. The viewer gets four
        # decimals; the fit uses the values it would otherwise never see.
        band_series = {b: [] for b in BANDS}
        index_series = {k: [] for k in INDICES}

        for i, (year, season, _, reduced) in enumerate(items):
            refl = reduced["refl"]
            valid = reduced["observed"]
            feats = compute_indices(refl)
            for b in BANDS:
                band_series[b].append(np.asarray(refl[b], dtype=np.float64))
            for k in INDICES:
                index_series[k].append(np.asarray(feats[k], dtype=np.float64))
                block["index"].setdefault(k, []).append(_clean(feats[k], ROUND["index"]))
            block["observed"].append([bool(v) for v in valid])
            block["validFraction"].append(_clean(reduced["valid_fraction"], 3))
            block["nObs"].append([int(v) for v in reduced["n_obs"]])

        # The assessment is per cell and per index: trend, season, fit and the
        # largest anomaly, plus the two stability metrics that used to be
        # derived in the browser from the band vectors.
        block["cell"], block["cellraw"] = analyse(
            periods,
            {k: np.stack(v) for k, v in index_series.items()},
            np.asarray(block["observed"], dtype=bool),
            {b: np.stack(v) for b, v in band_series.items()},
        )

        block["_indices"] = list(INDICES)

        # Bake the hexagon outlines once, here, rather than letting every
        # browser recompute them with the H3 library on each load.
        import h3 as _h3
        nverts, flat = [], []
        for cell in grid.cells:
            ring = _h3.cell_to_boundary(str(cell))   # [(lat, lng), ...]
            nverts.append(len(ring))
            for lat, lng in ring:
                flat.append(lng); flat.append(lat)
        block["_nVerts"] = nverts
        block["_geomXY"] = flat

        info = write_block(block, os.path.join(out_dir, f"{stem}_res{res}.bin"), gz=gz)
        size = info["served_mb"]
        meta["resolutions"].append({"res": res, "nCells": n_cells,
                                    "nPeriods": len(periods), "mb": size,
                                    "rawMb": info["raw_mb"],
                                    "bin": os.path.basename(info["path"])})
        stats[f"res{res}"] = (f"{n_cells:,} cells x {len(periods)} periods, "
                              f"{size:.1f} MB served ({info['raw_mb']:.1f} MB raw), "
                              f"observed {np.mean([np.mean(o) for o in block['observed']])*100:.0f}% mean")

    meta_path = os.path.join(out_dir, f"{stem}_meta.json")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, separators=(",", ":"), allow_nan=False)
    return stats