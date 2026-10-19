"""Landsat Collection 2 Level-2 ingestion and per-hexagon aggregation.

Scene selection
---------------
The `geometry` in these STAC records is a coarse regional envelope for the
acquisition's swath, not the scene footprint: for a south-west Florida scene
it spans roughly 22-36 N, some 1500 km tall. Selecting scenes by STAC geometry
therefore returns whatever else happened to be imaged that day, and filtering
it by "covers the AOI" silently mixes in scenes from other paths and rows.

This module ignores STAC geometry entirely. It opens the scene's raster
header, reads the true footprint and CRS, and intersects that with the AOI in
the scene's own CRS. Only scenes that genuinely cover the AOI are aggregated.

Per-pixel QA
------------
Landsat Collection 2 QA_PIXEL bits 0-6 (fill, dilated cloud, cirrus, cloud,
cloud shadow, snow/ice) are masked out. Bit 7 ("Water") is deliberately left
alone: open water is a class we want to measure, not something to discard.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT

from .config import (
    BAND_ASSETS,
    BANDS,
    MAX_PIXEL_CLOUD,
    MAX_SCENE_CLOUD,
    MAX_SCENES_PER_COMPOSITE,
    QA_PIXEL_ASSETS,
    SR_OFFSET,
    SR_SCALE,
    STAC_ENDPOINT,
)

# QA_PIXEL flags to mask unconditionally.
QA_BAD_MASK = (
    (1 << 0)   # fill
    | (1 << 1)   # dilated cloud
    | (1 << 2)   # cirrus
    | (1 << 3)   # cloud
    | (1 << 4)   # cloud shadow
)

# Snow/ice occupies a two-bit field at bits 5-6 with values:
#   00 no, 01 snow, 10 water ice, 11 cloud-contaminated ice.
# Bit 6 on its own is set in ~98% of pixels over south-west Florida, including
# scenes with under 1% cloud, so it is a routine artefact of this scene type
# rather than an ice signal. Masking on it throws away the entire AOI. Only
# genuine snow (01) and cloud-contaminated ice (11) are treated as bad.
QA_SNOWICE_SHIFT = 5
QA_SNOWICE_MASK = 0b11
QA_SNOWICE_BAD = {0b01, 0b11}

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    GDAL_HTTP_MULTIRANGE="YES",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    VSI_CACHE="TRUE",
    VSI_CACHE_SIZE="134217728",
    GDAL_INGESTED_BYTES_AT_OPEN="32768",
)


@dataclass
class Scene:
    item_id: str
    datetime: object
    platform: str
    cloud_cover: float
    wrs_path: str
    wrs_row: str
    assets: dict          # band name -> href
    qa_href: str
    bounds: tuple         # true raster bounds in scene CRS
    crs: str
    coverage: float       # fraction of AOI covered by this scene


def _client():
    import planetary_computer as pc
    from pystac_client import Client

    return Client.open(STAC_ENDPOINT, modifier=pc.sign_inplace)


def _asset_for(item, names):
    for n in names:
        if n in item.assets:
            return item.assets[n].href
    return None


def _scene_covers_aoi(item, aoi_poly_4326) -> Scene | None:
    """Open the scene header and test true coverage of the AOI.

    `aoi_poly_4326` is the AOI polygon in EPSG:4326 (lon/lat). It is reprojected
    into the scene's own CRS before intersecting, because a lon/lat AOI and a
    projected scene footprint are otherwise compared in the wrong coordinate
    space and every scene is silently rejected.
    """
    from shapely.geometry import Polygon, box

    red = _asset_for(item, ("red",))
    if red is None:
        return None
    try:
        with rasterio.open(red, **GDAL_ENV) as src:
            bounds = src.bounds
            crs = str(src.crs)
    except Exception:
        return None

    from pyproj import Transformer

    try:
        tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        ring = aoi_poly_4326.exterior
        n = 120
        pts = [ring.interpolate(i / n, normalized=True) for i in range(n)]
        xs, ys = tr.transform([p.x for p in pts], [p.y for p in pts])
    except Exception:
        return None

    scene_poly = box(bounds.left, bounds.bottom, bounds.right, bounds.top)
    aoi_in_scene = Polygon(zip(xs, ys))
    if aoi_in_scene.is_empty or aoi_in_scene.area <= 0:
        return None
    frac = scene_poly.intersection(aoi_in_scene).area / aoi_in_scene.area
    if frac < 0.95:
        return None

    assets = {}
    for band, names in BAND_ASSETS.items():
        href = _asset_for(item, names)
        if href is None:
            return None
        assets[band] = href
    qa_href = _asset_for(item, QA_PIXEL_ASSETS)
    if qa_href is None:
        return None

    return Scene(
        item_id=item.id,
        datetime=item.datetime,
        platform=item.properties.get("platform", "?"),
        cloud_cover=float(item.properties.get("eo:cloud_cover") or 0.0),
        wrs_path=str(item.properties.get("landsat:wrs_path", "?")),
        wrs_row=str(item.properties.get("landsat:wrs_row", "?")),
        assets=assets,
        qa_href=qa_href,
        bounds=tuple(bounds),
        crs=crs,
        coverage=float(frac),
    )


def find_scenes(cfg, year: int, season: str, aoi_poly_4326) -> list[Scene]:
    """All Landsat C2 L2 scenes genuinely covering the AOI in one season."""
    (m1, d1), (m2, d2) = cfg.seasons[season]
    # A season can wrap the new year (dry season runs Nov-Feb).
    if m1 > m2:
        start = f"{year - 1}-{m1:02d}-{d1:02d}T00:00:00Z"
        end = f"{year}-{m2:02d}-{d2:02d}T23:59:59Z"
    else:
        start = f"{year}-{m1:02d}-{d1:02d}T00:00:00Z"
        end = f"{year}-{m2:02d}-{d2:02d}T23:59:59Z"

    cat = _client()
    search = cat.search(
        collections=[cfg.collection],
        bbox=list(cfg.aoi_bbox),
        datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lte": MAX_SCENE_CLOUD}},
    )
    scenes = []
    for item in search.items():
        s = _scene_covers_aoi(item, aoi_poly_4326)
        if s is not None:
            scenes.append(s)

    # Prefer low cloud, then newest, and cap the count. Median over many
    # near-duplicate acquisitions adds little for a composite.
    scenes.sort(key=lambda s: (s.cloud_cover, -s.datetime.timestamp()))
    return scenes[:cfg.max_scenes]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
def read_scene(scene: Scene, target_transform, target_shape, target_crs) -> dict | None:
    """Read one scene onto the analysis grid.

    Returns a dict with float32 surface reflectance per band plus a boolean
    `valid` mask, or None if the read produced nothing usable.
    """
    from rasterio.crs import CRS

    crs = CRS.from_string(target_crs)
    out = {}
    valid = None
    for band, href in scene.assets.items():
        try:
            with rasterio.open(href, **GDAL_ENV) as src:
                with WarpedVRT(
                    src,
                    crs=crs,
                    transform=target_transform,
                    width=target_shape[1],
                    height=target_shape[0],
                    resampling=rasterio.enums.Resampling.nearest,
                    src_nodata=src.nodata if src.nodata is not None else 0,
                    nodata=0,
                ) as vrt:
                    arr = vrt.read(1)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"read failed {scene.item_id} {band}: {exc}") from exc
        out[band] = arr.astype(np.float32) * np.float32(SR_SCALE) + np.float32(SR_OFFSET)
        valid = np.isfinite(arr) & (arr > 0) if valid is None else (valid & np.isfinite(arr) & (arr > 0))

    try:
        with rasterio.open(scene.qa_href, **GDAL_ENV) as src:
            with WarpedVRT(
                src,
                crs=crs,
                transform=target_transform,
                width=target_shape[1],
                height=target_shape[0],
                resampling=rasterio.enums.Resampling.nearest,
                src_nodata=src.nodata if src.nodata is not None else 0,
                nodata=0,
            ) as vrt:
                qa = vrt.read(1).astype(np.uint32)
    except Exception:
        return None

    bad = (qa & QA_BAD_MASK) != 0
    snowice = (qa >> QA_SNOWICE_SHIFT) & QA_SNOWICE_MASK
    bad = bad | np.isin(snowice, list(QA_SNOWICE_BAD))

    # Cloud confidence (bits 8-9) is deliberately not used as an extra screen:
    # the cloud and cloud-shadow flags already carry that information, and
    # adding a confidence threshold here measurably discards otherwise clear
    # pixels in this scene type.

    out["valid"] = valid & ~bad
    return out


# --------------------------------------------------------------------------
# Aggregation into hexagons
# --------------------------------------------------------------------------
def aggregate_scene(scene_arrays: dict, label_rasters: dict[int, np.ndarray],
                    n_cells: dict[int, int]) -> dict:
    """Bin one scene's arrays into every hexagon resolution at once.

    Returns {resolution: {band: mean, "n_valid": n, "n_total": n, "n_obs": 1}}.
    Only the mean is kept per observation; the temporal median is taken later,
    across observations, which is the more robust order.
    """
    valid = scene_arrays["valid"]
    valid_flat = valid.ravel()

    res_out = {}
    for res, labels in label_rasters.items():
        n = n_cells[res]
        lab = labels.ravel()
        lab_valid = lab[valid_flat]
        counts = np.bincount(lab_valid, minlength=n + 1)
        counts = counts[1:]  # drop label 0

        # Denominator for "fraction of this cell that was actually observed"
        # must be the number of pixels the cell owns, not the size of the
        # whole raster. Using the raster size drives the ratio to ~0.0003 for
        # every cell and marks the entire AOI unobserved.
        n_total = np.bincount(lab, minlength=n + 1)[1:]

        bands = {}
        for band in BANDS:
            vals = scene_arrays[band].ravel()[valid_flat]
            sums = np.bincount(lab_valid, weights=vals.astype(np.float64), minlength=n + 1)[1:]
            with np.errstate(invalid="ignore", divide="ignore"):
                bands[band] = np.where(counts > 0, sums / np.maximum(counts, 1), np.nan).astype(np.float32)

        res_out[res] = {
            "bands": bands,
            "n_valid": counts.astype(np.int32),
            "n_total": n_total.astype(np.int32),
            "n_obs": np.ones(n, dtype=np.int32),
        }
    return res_out


def scene_monthly_summary(scenes: list[Scene]) -> dict:
    import collections

    return {
        "n_scenes": len(scenes),
        "months": dict(collections.Counter(s.datetime.strftime("%m") for s in scenes)),
        "paths": sorted({f"{s.wrs_path}/{s.wrs_row}" for s in scenes}),
        "cloud_median": float(np.median([s.cloud_cover for s in scenes])) if scenes else None,
    }