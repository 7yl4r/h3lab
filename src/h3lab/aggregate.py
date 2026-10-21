"""Generalise aggregation to bin an arbitrary raster stack (mosaic).

The pilot aggregated one *scene* into hexagons and took the temporal median
across scenes afterwards. The regional build composites first: the mosaic is
already a per-pixel temporal median, and this module samples that mosaic into
hexagons. The order matters and is reversed relative to the pilot -- see
DESIGN.md §3.4.

The mosaic cache stores raw uint16 digital numbers, so the surface-reflectance
scaling (SR_SCALE / SR_OFFSET) is applied here, at sampling time, which is
where the pilot applies it too.
"""

from __future__ import annotations

import numpy as np

from .config import BANDS, SR_OFFSET, SR_SCALE


def to_reflectance(dn: np.ndarray) -> np.ndarray:
    """Raw digital numbers -> surface reflectance."""
    return dn.astype(np.float32) * np.float32(SR_SCALE) + np.float32(SR_OFFSET)


def aggregate_mosaic(mosaic_bands: dict, labels: np.ndarray, n_cells: int,
                     nobs: np.ndarray | None = None,
                     scale: bool = True) -> dict:
    """Bin mosaic band arrays into hex cells.

    mosaic_bands: {band: 2D array of digital numbers, NaN where missing}
    labels:       2D label raster (0 = no cell, else cell index + 1)
    nobs:         optional 2D per-pixel count of contributing scenes
    scale:        apply the surface-reflectance transform (default on)

    A pixel counts as observed only if it is valid in *every* band, so a
    partially observed pixel cannot contribute a half-empty spectrum to a cell
    mean. `validFraction` is the observed fraction of the cell's own pixels.
    """
    valid = None
    for b in BANDS:
        if b in mosaic_bands:
            arr = mosaic_bands[b]
            if valid is None:
                valid = np.isfinite(arr)
            else:
                valid = valid & np.isfinite(arr)

    if valid is None:
        valid = np.ones(labels.shape, dtype=bool)

    valid_flat = valid.ravel()
    lab = labels.ravel()
    lab_valid = lab[valid_flat]
    counts = np.bincount(lab_valid, minlength=n_cells + 1)[1:]

    # The denominator for "fraction of this cell that was actually observed"
    # must be the number of pixels the cell owns, not the size of the whole
    # raster.
    n_total = np.bincount(lab, minlength=n_cells + 1)[1:]

    bands_out = {}
    for band in BANDS:
        if band not in mosaic_bands:
            bands_out[band] = np.full(n_cells, np.nan, dtype=np.float32)
            continue
        vals = mosaic_bands[band].ravel()[valid_flat]
        if scale:
            vals = to_reflectance(vals)
        sums = np.bincount(lab_valid, weights=vals.astype(np.float64),
                           minlength=n_cells + 1)[1:]
        with np.errstate(invalid="ignore", divide="ignore"):
            bands_out[band] = np.where(counts > 0, sums / np.maximum(counts, 1),
                                       np.nan).astype(np.float32)

    if nobs is None:
        n_obs = np.ones(n_cells, dtype=np.int32)
    else:
        # nObs is now "contributing scenes per pixel" (DESIGN.md §3.4): the
        # mean number of scenes that saw the cell's observed pixels.
        # A pixel with no scene behind it reads back as NaN; it contributes
        # zero, and must not poison the sum.
        n_flat = np.nan_to_num(nobs.ravel()[valid_flat], nan=0.0)
        n_sum = np.bincount(lab_valid, weights=n_flat.astype(np.float64),
                            minlength=n_cells + 1)[1:]
        with np.errstate(invalid="ignore", divide="ignore"):
            n_obs = np.where(counts > 0, n_sum / np.maximum(counts, 1), 0)
        n_obs = np.rint(n_obs).astype(np.int32)

    return {
        "bands": bands_out,
        "n_valid": counts.astype(np.int32),
        "n_total": n_total.astype(np.int32),
        "n_obs": n_obs,
    }
