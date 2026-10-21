"""STAC scene enumeration for regional builds.

Regional extent (87,000 km²) means no Landsat scene covers the whole AOI,
so we use intersection-based acceptance (D-24), not the 95% coverage test
from the pilot.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import rasterio

from .config import (
    BAND_ASSETS,
    BANDS,
    MAX_PIXEL_CLOUD,
    MAX_SCENE_CLOUD,
    QA_PIXEL_ASSETS,
    STAC_ENDPOINT,
    COLLECTION,
    PLATFORMS,
)

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
    assets: dict  # band name -> href
    qa_href: str
    bounds: tuple  # true raster bounds in scene CRS
    crs: str
    coverage: float  # fraction of AOI covered

    def to_dict(self) -> dict:
        """Plain-data form, for the cached scene manifest.

        The datetime is written as an ISO string rather than a timestamp so
        the cached manifest is byte-identical across rebuilds (D-21's
        reproducibility requirement) and readable without a tz library.
        """
        return {
            "item_id": self.item_id,
            "datetime": self.datetime.isoformat() if self.datetime else None,
            "platform": self.platform,
            "cloud_cover": self.cloud_cover,
            "wrs_path": self.wrs_path,
            "wrs_row": self.wrs_row,
            "assets": dict(self.assets),
            "qa_href": self.qa_href,
            "bounds": list(self.bounds),
            "crs": self.crs,
            "coverage": self.coverage,
        }

    @staticmethod
    def from_dict(d: dict) -> "Scene":
        dt = d.get("datetime")
        if dt:
            dt = datetime.fromisoformat(dt)
        return Scene(
            item_id=d["item_id"],
            datetime=dt,
            platform=d["platform"],
            cloud_cover=float(d["cloud_cover"]),
            wrs_path=d["wrs_path"],
            wrs_row=d["wrs_row"],
            assets=dict(d["assets"]),
            qa_href=d["qa_href"],
            bounds=tuple(d["bounds"]),
            crs=d["crs"],
            coverage=float(d["coverage"]),
        )


class UrlSigner:
    """Re-signs Planetary Computer asset URLs on demand.

    Planetary Computer hands out SAS tokens that expire about 45 minutes after
    they are issued. A regional build reads for hours, so a URL captured when
    the scene was enumerated is dead long before the build finishes -- and
    because the failure is a plain 403 on a range request, it is easy to
    mistake for "no data" and carry on. Every read therefore goes through
    this cache, which refreshes the token well inside its lifetime.

    The URLs stored on a `Scene` are the *unsigned* blob URLs; the token is
    added on the way out.
    """

    TTL = 1200.0  # seconds; Planetary Computer tokens live about 45 minutes

    def __init__(self, ttl: float | None = None):
        self.ttl = self.TTL if ttl is None else ttl
        self._cache: dict = {}

    def sign(self, url: str) -> str:
        now = time.time()
        hit = self._cache.get(url)
        if hit is not None and now - hit[0] < self.ttl:
            return hit[1]
        signed = url
        try:
            import planetary_computer as pc

            signed = pc.sign_url(url)
        except Exception:  # noqa: BLE001 - signing is best effort
            pass
        self._cache[url] = (now, signed)
        return signed


SIGNER = UrlSigner()


def unsigned_url(href: str) -> str:
    """Strip the SAS query string from a Planetary Computer asset URL.

    The blob path is what `planetary_computer.sign_url` needs, and keeping the
    unsigned form in the scene record means no expiring token is ever written
    into a cache file.
    """
    if ".blob.core.windows.net" not in href:
        return href
    return href.split("?", 1)[0]


def _client():
    import planetary_computer as pc
    from pystac_client import Client

    return Client.open(STAC_ENDPOINT, modifier=pc.sign_inplace)


def _asset_for(item, names):
    for n in names:
        if n in item.assets:
            return item.assets[n].href
    return None


def _scene_intersects_aoi(item, aoi_poly_4326) -> Scene | None:
    """Open scene header and test true intersection with AOI.
    Uses intersection (not >=95% coverage) for regional extent (D-24).
    """
    from shapely.geometry import box, Polygon

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
    inter = scene_poly.intersection(aoi_in_scene)
    if inter.is_empty:
        return None
    frac = inter.area / aoi_in_scene.area
    if frac <= 0.0:
        return None

    assets = {}
    for band, names in BAND_ASSETS.items():
        href = _asset_for(item, names)
        if href is None:
            return None
        assets[band] = unsigned_url(href)
    qa_href = _asset_for(item, QA_PIXEL_ASSETS)
    if qa_href is None:
        return None
    # Unsigned like every other asset. `pc.sign_url` returns an
    # already-signed URL unchanged, so a signed QA href in the cache would
    # expire with its token and the rebuild would fail with a 403 on the QA
    # read rather than on the reflectance read that the token cache does
    # refresh.
    qa_href = unsigned_url(qa_href)

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


def find_scenes_for_bin(cfg, bin_start_date, bin_end_date, aoi_poly_4326,
                        platforms=None) -> list[Scene]:
    """Enumerate all scenes intersecting AOI in a bin (no 12-scene cap)."""
    if platforms is None:
        platforms = PLATFORMS

    start = bin_start_date.strftime("%Y-%m-%dT00:00:00Z")
    end = bin_end_date.strftime("%Y-%m-%dT23:59:59Z")

    cat = _client()
    search = cat.search(
        collections=[cfg.collection],
        bbox=list(cfg.aoi_bbox) if hasattr(cfg, "aoi_bbox") else None,
        datetime=f"{start}/{end}",
        query={"eo:cloud_cover": {"lte": MAX_SCENE_CLOUD}},
    )
    scenes = []
    for item in search.items():
        plat = item.properties.get("platform", "")
        # Filter platforms if specified
        if platforms:
            plat_lower = plat.lower().replace(" ", "-")
            ok_plat = False
            for p in platforms:
                if p in plat_lower or plat_lower in p:
                    ok_plat = True
                    break
            if not ok_plat:
                continue
        s = _scene_intersects_aoi(item, aoi_poly_4326)
        if s is not None:
            scenes.append(s)

    # Deterministic sort
    scenes.sort(key=lambda s: (s.cloud_cover, s.datetime.isoformat() if s.datetime else "", s.item_id))
    return scenes


def find_scenes_2017(cfg, aoi_poly_4326) -> dict:
    """Find scenes for all bins in 2017. Returns dict bin->list of scenes."""
    from . import bins

    result = {}
    for b in bins.bins_for_year(2017):
        bstart = bins.bin_start(b)
        bend = bins.bin_end(b)
        scenes = find_scenes_for_bin(cfg, bstart, bend, aoi_poly_4326)
        if scenes:
            result[b] = scenes
    return result
