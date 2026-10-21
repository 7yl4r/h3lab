"""Per-bin 30 m mosaic builder.

One mosaic per 16-day bin: the per-pixel temporal median of every scene that
covers the pixel, on one canonical 30 m grid over the whole bbox.

Storage follows the Phase 1 plan (DESIGN/PHASE1 §6.2): a tiled,
deflate-compressed GeoTIFF holding the raw uint16 digital numbers, so the
cache is lossless and the surface-reflectance scaling (SR_SCALE / SR_OFFSET) is
applied later, at H3 sampling time, exactly where the pilot applies it.

Three things here are worth knowing about.

**Remote reads are re-signed on demand.** Planetary Computer SAS tokens expire
about 45 minutes after they are issued. A regional build reads for hours, so a
URL captured when the scene was enumerated is dead long before the build
finishes, and every read fails with a 403 that a bare `except` swallows. Every
read therefore goes through `stac.SIGNER`, which refreshes the token well
inside its lifetime.

**Memory is bounded two ways.** A scene is read into an array the size of its
own footprint rather than the whole 97 M px grid, and the median is taken
block-wise over only the scenes that actually cover that block. Peak usage is
about 1 GB, comfortably inside the 32 GB the plan asks the code to support.

**The median is the standard one.** For an even number of observations it is
the mean of the two central values; for an odd number it is the central value.
Deterministic, and the same for every cell.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

from .config import ANALYSIS_CRS, BANDS, PIXEL_SIZE
from .grid import analysis_grid
from .stac import SIGNER


GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    GDAL_HTTP_MULTIRANGE="YES",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    VSI_CACHE="TRUE",
    VSI_CACHE_SIZE="134217728",
    GDAL_INGESTED_BYTES_AT_OPEN="32768",
    # A request that hangs is a stalled build, not a slow one. GDAL's default
    # is to wait forever, and one hung range request was observed to freeze a
    # worker for 13 minutes with no I/O at all. A timeout turns that into a
    # failed read, which the retry below turns into a fresh request.
    GDAL_HTTP_TIMEOUT="180",
    GDAL_HTTP_MAX_RETRY="2",
    GDAL_HTTP_RETRY_DELAY="2",
)

# QA_PIXEL flags to mask unconditionally. Same set the pilot uses: fill,
# dilated cloud, cirrus, cloud and cloud shadow, plus genuine snow and
# cloud-contaminated ice. The water bit is deliberately left alone -- open
# water is a class this project measures, not something to discard.
QA_BAD_MASK = (
    (1 << 0)   # fill
    | (1 << 1)   # dilated cloud
    | (1 << 2)   # cirrus
    | (1 << 3)   # cloud
    | (1 << 4)   # cloud shadow
)
QA_SNOWICE_SHIFT = 5
QA_SNOWICE_MASK = 0b11
QA_SNOWICE_BAD = (0b01, 0b11)

BLOCK = 2048  # block edge in pixels for the block-wise median

# How long an opened remote dataset may be reused before it is reopened with a
# fresh token. Planetary Computer tokens live about 45 minutes; half that is a
# safe refresh point.
REOPEN_AFTER = 1200.0


def build_analysis_grid(aoi_bbox, crs=ANALYSIS_CRS, pixel_size=PIXEL_SIZE):
    """Canonical analysis grid - shared with the grid module."""
    return analysis_grid(aoi_bbox, crs, pixel_size)


# --------------------------------------------------------------------------
# Scene reading
# --------------------------------------------------------------------------
class _SceneReader:
    """One scene, opened once and read through a WarpedVRT onto the grid.

    The reader keeps its datasets open for the life of the bin and reopens
    them when the signed URL they were opened with has aged out.
    """

    def __init__(self, scene, transform, shape, crs):
        self.scene = scene
        self.transform = transform
        self.shape = shape
        self.crs = crs
        self.grid_window = None    # (col0, row0, col1, row1) on the grid
        self._vrt_cache: dict = {}
        self._opened_at = 0.0
        self._qa_mask = None       # bool array over the scene's grid window

    # -- lifecycle ---------------------------------------------------------
    def _open(self, key, href):
        src = rasterio.open(SIGNER.sign(href), **GDAL_ENV)
        vrt = WarpedVRT(
            src,
            crs=CRS.from_string(self.crs),
            transform=self.transform,
            width=self.shape[1],
            height=self.shape[0],
            resampling=Resampling.nearest,
            src_nodata=src.nodata if src.nodata is not None else 0,
            nodata=0,
        )
        self._vrt_cache[key] = (src, vrt)
        self._opened_at = time.time()

    def close(self):
        for src, vrt in self._vrt_cache.values():
            for obj in (vrt, src):
                try:
                    obj.close()
                except Exception:
                    pass
        self._vrt_cache.clear()

    def _vrt(self, key, href):
        """The warped dataset for `key`, reopening it if the token has aged."""
        entry = self._vrt_cache.get(key)
        if entry is not None and time.time() - self._opened_at < REOPEN_AFTER:
            return entry[1]
        if entry is not None:
            self.close()
        self._open(key, href)
        return self._vrt_cache[key][1]

    # -- reads -------------------------------------------------------------
    def read_band(self, band: str, col0: int, row0: int, col1: int, row1: int):
        """Raw uint16 DN for the given grid window, or None if the read fails.

        Zero means fill or "outside this scene"; it is the nodata sentinel.
        """
        href = self.scene.assets.get(band)
        if href is None:
            return None
        window = Window(col0, row0, col1 - col0, row1 - row0)
        for attempt in range(2):
            try:
                vrt = self._vrt(f"b:{band}", href)
                return vrt.read(1, window=window)
            except Exception as exc:  # noqa: BLE001
                self.close()
                if attempt == 1:
                    raise RuntimeError(
                        f"read failed {self.scene.item_id} {band}: {exc}"
                    ) from exc
        return None

    def read_qa_mask(self):
        """Boolean 'this pixel is usable' mask over the scene's grid window.

        Read once per scene and reused for every band: the QA flags do not
        depend on the band, and the QA raster is a sixteenth of the size of a
        reflectance band.
        """
        if self._qa_mask is not None:
            return self._qa_mask
        href = self.scene.qa_href
        if href is None:
            return None
        col0, row0, col1, row1 = self.grid_window
        window = Window(col0, row0, col1 - col0, row1 - row0)
        for attempt in range(2):
            try:
                vrt = self._vrt("qa", href)
                qa = vrt.read(1, window=window).astype(np.uint32)
                break
            except Exception as exc:  # noqa: BLE001
                self.close()
                if attempt == 1:
                    raise RuntimeError(
                        f"read failed {self.scene.item_id} qa_pixel: {exc}"
                    ) from exc
        bad = (qa & QA_BAD_MASK) != 0
        snowice = (qa >> QA_SNOWICE_SHIFT) & QA_SNOWICE_MASK
        bad = bad | np.isin(snowice, QA_SNOWICE_BAD)
        self._qa_mask = ~bad
        return self._qa_mask


def _grid_window(scene, transform, shape, crs):
    """Bounding window of a scene's footprint on the analysis grid.

    Returns (col0, row0, col1, row1) or None when the scene misses the grid.
    """
    left, bottom, right, top = scene.bounds
    if scene.crs == crs:
        minx, maxx, miny, maxy = left, right, bottom, top
    else:
        from pyproj import Transformer

        tr = Transformer.from_crs(scene.crs, crs, always_xy=True)
        xs, ys = tr.transform([left, right, left, right],
                              [bottom, bottom, top, top])
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)

    inv = ~transform
    # The transform is north-up, so row 0 is the *top* edge: the scene's
    # maximum y is its top row and its minimum y is its bottom row.
    c0, r_top = inv * (minx, maxy)
    c1, r_bottom = inv * (maxx, miny)
    col0 = max(int(np.floor(c0)), 0)
    row0 = max(int(np.floor(r_top)), 0)
    col1 = min(int(np.ceil(c1)), shape[1])
    row1 = min(int(np.ceil(r_bottom)), shape[0])
    if col1 <= col0 or row1 <= row0:
        return None
    return (col0, row0, col1, row1)


# --------------------------------------------------------------------------
# Median
# --------------------------------------------------------------------------
def _median_stack(stack: np.ndarray) -> np.ndarray:
    """Per-pixel median over the last axis, ignoring NaN.

    `stack` is (bh, bw, n) float32 with NaN for missing observations. The
    median of the k valid values is the central value for odd k and the mean
    of the two central values for even k -- the ordinary definition, and
    deterministic.
    """
    finite = np.isfinite(stack)
    k = np.count_nonzero(finite, axis=-1)
    srt = np.sort(stack, axis=-1)          # NaN sorts to the end
    lo_idx = np.maximum((k - 1) // 2, 0)
    hi_idx = k // 2
    lo = np.take_along_axis(srt, lo_idx[..., None], axis=-1)[..., 0]
    hi = np.take_along_axis(srt, hi_idx[..., None], axis=-1)[..., 0]
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------
def _open_out(path, transform, w, h, dtype, nodata=0):
    profile = {
        "driver": "GTiff",
        "height": h,
        "width": w,
        "count": 1,
        "dtype": dtype,
        "crs": ANALYSIS_CRS,
        "transform": transform,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "deflate",
        "predictor": 2,
        "zlevel": 6,
    }
    return rasterio.open(path, "w", **profile)


def _read_footprint(reader, band: str):
    """A scene's whole footprint on the grid, as raw uint16 DN.

    Returns (col0, col1, rows, cols, array) or None when the scene does not
    reach this band.
    """
    col0, row0, col1, row1 = reader.grid_window
    raw = reader.read_band(band, col0, row0, col1, row1)
    if raw is None:
        return None
    return col0, col1, raw


def build_mosaic_bin(cfg, bin_num: int, scenes, out_dir: str, block: int = BLOCK,
                     workers: int = 12, log=print):
    """Build the per-bin mosaic. Returns a dict of output paths.

    For each band: every scene is read into its own footprint, QA-masked, and
    the per-pixel temporal median is written out as raw DN. A single n-obs
    raster counts, per pixel, how many scenes saw it at all.

    The reads of different scenes are independent and dominate the runtime, so
    they run on a thread pool: rasterio releases the GIL inside the read, and
    the source COGs are on a network that a single request stream leaves idle.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    transform, (h, w), _ = analysis_grid(cfg.aoi_bbox, cfg.analysis_crs,
                                         cfg.pixel_size)

    readers = []
    for scene in scenes:
        reader = _SceneReader(scene, transform, (h, w), cfg.analysis_crs)
        win = _grid_window(scene, transform, (h, w), cfg.analysis_crs)
        if win is None:
            continue
        reader.grid_window = win
        readers.append(reader)

    if not readers:
        raise ValueError(f"bin {bin_num}: no scene intersects the grid")

    # n-obs: how many scenes saw each pixel. The QA screen is band
    # independent, so one count serves every band. Scene footprints overlap
    # only at their edges, so a pixel is normally seen by one path/row's worth
    # of acquisitions.
    nobs = np.zeros((h, w), dtype=np.uint8)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        qa_masks = list(ex.map(lambda r: r.read_qa_mask(), readers))
    for reader, mask in zip(readers, qa_masks):
        col0, row0, col1, row1 = reader.grid_window
        nobs[row0:row1, col0:col1] += mask.astype(np.uint8)

    paths = {}
    try:
        for band in BANDS:
            out_path = out_dir / f"mosaic_bin{bin_num}_{band}.tif"
            # Write to a temporary name and rename it into place. A build that
            # is interrupted mid-write leaves a truncated raster behind
            # otherwise, and the next run -- which decides what to reuse from
            # what is on disk -- would take that truncated file for a cache
            # hit and build a mosaic with a hole in it.
            tmp_path = out_dir / f".mosaic_bin{bin_num}_{band}.part.tif"
            dst = _open_out(tmp_path, transform, w, h, "uint16")
            t_read = t_cpu = 0.0
            try:
                t0 = time.time()
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    parts = list(ex.map(lambda r: _read_footprint(r, band),
                                        readers))
                t_read = time.time() - t0
                t0 = time.time()
                for row0 in range(0, h, block):
                    row1 = min(row0 + block, h)
                    stack = np.full((row1 - row0, w, len(readers)), np.nan,
                                    dtype=np.float32)
                    for i, part in enumerate(parts):
                        if part is None:
                            continue
                        col0, col1, raw = part
                        gcol0, grow0, gcol1, grow1 = readers[i].grid_window
                        ir0 = max(row0, grow0)
                        ir1 = min(row1, grow1)
                        if ir0 >= ir1:
                            continue
                        sub = raw[ir0 - grow0:ir1 - grow0, :].astype(np.float32)
                        sub[sub <= 0] = np.nan          # fill / outside scene
                        mask = qa_masks[i][ir0 - grow0:ir1 - grow0, :]
                        sub[~mask] = np.nan
                        stack[ir0 - row0:ir1 - row0, col0:col1, i] = sub
                        del sub
                    med = _median_stack(stack)
                    out = np.where(np.isfinite(med), med, 0.0)
                    dst.write(np.rint(out).astype(np.uint16), 1,
                              window=Window(0, row0, w, row1 - row0))
                    del stack, med, out
                t_cpu = time.time() - t0
            finally:
                dst.close()
            os.replace(tmp_path, out_path)
            paths[band] = str(out_path)
            if log is not None:
                log(f"    {band}: read {t_read:.0f}s, median+write {t_cpu:.0f}s")

        nobs_path = out_dir / f"nobs_bin{bin_num}.tif"
        nobs_tmp = out_dir / f".nobs_bin{bin_num}.part.tif"
        dst = _open_out(nobs_tmp, transform, w, h, "uint8")
        try:
            dst.write(nobs, 1)
        finally:
            dst.close()
        os.replace(nobs_tmp, nobs_path)
        paths["nobs"] = str(nobs_path)
    finally:
        for reader in readers:
            reader.close()

    return paths


def read_mosaic_band(path: str) -> np.ndarray:
    """Read a mosaic band as float32 DN, with nodata replaced by NaN."""
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
    arr[arr <= 0] = np.nan
    return arr


def read_mosaic_nobs(path: str) -> np.ndarray:
    """Read a mosaic n-obs raster as float32, with nodata replaced by NaN."""
    with rasterio.open(path) as src:
        arr = src.read(1).astype(np.float32)
    arr[arr <= 0] = np.nan
    return arr
