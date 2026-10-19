"""Spectral indices computed from composite surface reflectance.

There is no classification here, deliberately. The pipeline reports what the
sensor measured and a small set of well-defined ratios of it. Anything
categorical -- a land-cover label, fractional cover, a dominant endmember --
would be a model built on top, and those belong downstream where they can be
justified against ground truth rather than buried in the ingest path.

Every index is defined once, in `config.INDEX_SPECS`, and implemented once,
here. Those specs travel in the bundle descriptor so the viewer can label its
own controls without duplicating a list of names.
"""

from __future__ import annotations

import numpy as np

from .config import BANDS, INDEX_SPECS, INDICES


# A ratio index is only meaningful when both of its bands carry signal clearly
# above the noise floor. Collection 2 surface reflectance has a -0.2 offset
# and quantises at about 0.0000275, so over dark water one band can land near
# zero or slightly negative while the other does not, and (a-c)/(a+c) then
# returns values far outside the index's own [-1, 1] range -- NDVI of -200 was
# observed this way. A 1e-6 denominator guard is far too permissive: it admits
# denominators of 1e-3, a hundred times the product's precision.
#
# Two conditions, both required:
#   |a + c| > RATIO_EPS  -- the denominator is not near-cancelling, and
#   min(|a|, |c|) > ...  -- neither band is inside the noise floor, which is
#                          what lets one band go negative and break the bound.
#
# Cells failing either are reported as missing rather than as absurd numbers.
RATIO_EPS = 0.01


def _ratio(a: np.ndarray, c: np.ndarray) -> np.ndarray:
    """(a - c) / (a + c), NaN where either band is below the noise floor."""
    num = a - c
    den = a + c
    ok = (np.abs(den) > RATIO_EPS) & (np.abs(a) > RATIO_EPS) & (np.abs(c) > RATIO_EPS)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(ok & np.isfinite(num), num / np.where(ok, den, 1.0), np.nan)


def compute_indices(refl: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """All indices in `config.INDICES` for one composite.

    `refl` maps band name to a 1-D array of per-cell surface reflectance.
    """
    out: dict[str, np.ndarray] = {}
    for name in INDICES:
        spec = INDEX_SPECS[name]
        if spec["kind"] == "ratio":
            a, c = spec["bands"]
            out[name] = _ratio(refl[a], refl[c]).astype(np.float32)
        elif spec["kind"] == "savi":
            a, c = spec["bands"]
            L = float(spec.get("L", 0.2))
            num = (refl[a] - refl[c]) * (1.0 + L)
            den = (refl[a] + refl[c]) + L * (1.0 - refl[c])
            # SAVI's denominator keeps a +L term, so it does not collapse the way
            # a plain ratio does, but guard it anyway for consistency.
            ok = np.abs(den) > RATIO_EPS
            with np.errstate(invalid="ignore", divide="ignore"):
                out[name] = np.where(
                    ok & np.isfinite(num), num / np.where(ok, den, 1.0), np.nan
                ).astype(np.float32)
        else:  # pragma: no cover - guarded by config
            raise ValueError(f"unknown index kind {spec['kind']!r} for {name!r}")
    return out


def valid_indices(refl: dict[str, np.ndarray],
                  idx: dict[str, np.ndarray]) -> np.ndarray:
    """Mask of cells where every band and every index is finite.

    A cell with a usable red band but a degenerate NDVI denominator should not
    be reported as observed, so the support mask is the intersection of the
    two rather than the band mask alone.
    """
    ok = np.isfinite(refl[BANDS[0]])
    for b in BANDS[1:]:
        ok &= np.isfinite(refl[b])
    for k in INDICES:
        ok &= np.isfinite(idx[k])
    return ok


def spectral_angle(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Angle in degrees between two per-cell spectra, band by band.

    `a` and `b` are (nBands, nCells). The angle is scale-invariant, which is
    what makes it a useful stability measure: a cell that brightens uniformly
    moves in brightness but not in shape.
    """
    num = np.sum(a * b, axis=0)
    na = np.sqrt(np.sum(a * a, axis=0))
    nb = np.sqrt(np.sum(b * b, axis=0))
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.where((na > 1e-9) & (nb > 1e-9), num / (na * nb), np.nan)
    return np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))).astype(np.float32)
