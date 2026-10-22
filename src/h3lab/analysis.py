"""Per-cell assessment of the whole record.

The viewer used to derive its change layers from the per-band reflectance it
carried. Reflectance is expensive to ship (six bands x 26 composites x every
cell) and the number of bands only grows with each new sensor, while most of
what the map is asked -- *is this cell settling or moving?* -- is a property
of the record, not of any one composite. So the assessment is computed once,
here, and exported as a handful of scalars per cell.

seasonal decomposition
    For each index the model is

        y = a + b (year - mean year) + s * wet

    fitted by least squares with missing composites dropped cell by cell.
    ``trend`` is b (index units per year), ``amp`` is |s|/2 (half the fitted
    wet-dry swing), ``phase`` is which composite the swing peaks in, ``r2``
    is the share of the record the trend and season explain, and the largest
    residual is the one composite that least fits the cell's own habit --
    the closest thing to a dateable event.

    Why not a free harmonic ``c cos(2 pi t) + d sin(2 pi t)``? With two
    composites a year the harmonic's columns are two-valued: they depend only
    on the season, not on the year. Together with an intercept they span the
    same space as the wet/dry indicator, so a trend plus a free annual
    harmonic is rank-deficient -- the within-year part of the trend and the
    season are the same direction, and a fit that does not say so splits the
    difference arbitrarily. The model above is the identifiable form of the
    same fit. It also means the phase cannot resolve a date: the sampling
    lands on two seasons, so "peak month" is the centre of the season the
    peak falls in (August for wet, January for dry), not a fitted day.

stability
    ``angle`` and ``churn`` are the two metrics the viewer already exposed,
    moved to export time so the 6-band vectors no longer have to ship. Their
    definitions are unchanged -- see :func:`stability_fields`.

Cells with fewer than ``MIN_FIT_OBS`` usable composites are left missing
rather than fitted to noise.
"""

from __future__ import annotations

import numpy as np

# A level, a slope and a seasonal offset: below eight composites the residual
# scale has too few degrees of freedom to say anything.
MIN_FIT_OBS = 8

# Summed reflectance across the six bands below which a spectral angle is
# meaningless. Collection 2 scaling drives open water near zero, where the
# denominator in cos(angle) collapses and tiny differences swing the angle
# wildly. Matches ANGLE_MIN_BRIGHTNESS in the viewer.
ANGLE_MIN_BRIGHTNESS = 0.10

# Month at the centre of each composite's window: wet is May-Oct (centre
# ~1 Aug); dry is Nov-Feb (centre ~1 Jan, and belongs to the `year` it is
# labelled with because find_scenes wraps it to year-1..year).
SEASON_PEAK_MONTH = {"wet": 8, "dry": 1}

# What the viewer needs to build the layer controls. The ranges are display
# spans in index units: indices are all roughly -1..1, so one span per metric
# keeps resolutions comparable and stops a single outlier setting the ramp.
# They are not fitted per block.
SPECS = [
    {"key": "trend", "label": "trend", "unit": "/yr", "range": [-0.015, 0.015],
     "ramp": "diff", "rank": "asc", "rankLabel": "steepest decline",
     "desc": "Least-squares slope of the index through the record, in index "
             "units per year. Negative is browning/loss, positive is "
             "greening/recovery."},
    {"key": "trendz", "label": "trend significance", "unit": "\u03c3",
     "range": [-4, 4], "ramp": "diff", "rank": "asc",
     "rankLabel": "steepest significant decline",
     "desc": "Trend divided by its own standard error. A large number means "
             "the slope is well constrained by the record, not merely large."},
    {"key": "amp", "label": "seasonal amplitude", "unit": "", "range": [0, 0.25],
     "ramp": "veg", "rank": "desc", "rankLabel": "largest seasonal swing",
     "desc": "Half the fitted wet-to-dry swing. A cell with a big number has "
             "a strong seasonal cycle; a flat one has none."},
    {"key": "phase", "label": "seasonal peak", "unit": "", "range": [1, 12],
     "ramp": "phase", "cyclic": True, "rank": "desc",
     "rankLabel": "latest seasonal peak",
     "desc": "Season the fitted swing peaks in, drawn as a month: August for "
             "a wet-season peak, January for a dry-season one. The composites "
             "are six months apart, so a precise date is not resolvable."},
    {"key": "r2", "label": "seasonal fit R\u00b2", "unit": "", "range": [0, 1],
     "ramp": "frac", "rank": "asc", "rankLabel": "least predictable record",
     "desc": "Share of the record explained by trend plus season. Low values "
             "are erratic cells: disturbance, regime change, or too few good "
             "composites."},
    {"key": "anom", "label": "largest anomaly", "unit": "\u03c3",
     "range": [0, 4], "ramp": "fire", "rank": "desc",
     "rankLabel": "largest single anomaly",
     "desc": "Largest residual from the fitted trend and season, in units of "
             "the cell's own residual scatter. Hover a cell for the composite."},
]


def _design(periods: list[dict]) -> np.ndarray:
    """Rows of [1, year', wet] with the year centred for conditioning."""
    year = np.array([float(p["year"]) for p in periods], dtype=np.float64)
    wet = np.array([1.0 if p["season"] == "wet" else 0.0 for p in periods],
                   dtype=np.float64)
    return np.column_stack([np.ones_like(year), year - year.mean(), wet])


def fit_series(values, periods, observed=None) -> dict:
    """Fit one index through time, per cell.

    ``values`` is (nPeriods, nCells); missing values and periods not marked
    observed are dropped cell by cell. Returns a dict of (nCells,) arrays:
    trend, trendz, amp, phase (month), r2, anom (|z|), anom_p (period index,
    -1 when none).
    """
    y = np.asarray(values, dtype=np.float64)
    n_periods, n_cells = y.shape
    X = _design(periods)
    k = X.shape[1]

    finite = np.isfinite(y)
    if observed is not None:
        finite &= np.asarray(observed, dtype=bool)

    # Normal equations accumulated per cell, skipping missing composites.
    # Accumulating in the period loop is fully vectorised across cells and
    # avoids a Python-level solve per cell.
    xtx = np.zeros((n_cells, k, k), dtype=np.float64)
    xty = np.zeros((n_cells, k), dtype=np.float64)
    for p in range(n_periods):
        m = finite[p]
        if not m.any():
            continue
        x = X[p]
        xtx[m] += np.outer(x, x)
        xty[m] += x * y[p][m, None]

    # pinv, not solve: a cell with a short or degenerate record yields a
    # singular normal matrix, and the result is masked out below anyway.
    xtxi = np.linalg.pinv(xtx)
    beta = np.einsum("cij,cj->ci", xtxi, xty)
    fitted = X @ beta.T

    resid = np.where(finite, y - fitted, 0.0)
    ss_res = np.einsum("pc,pc->c", resid, resid)
    n = finite.sum(axis=0)
    mean = np.divide(np.where(finite, y, 0.0).sum(axis=0), n,
                     out=np.full(n_cells, np.nan), where=n > 0)
    ss_tot = np.where(finite, (y - mean) ** 2, 0.0).sum(axis=0)

    df = n - k
    ok = n >= MIN_FIT_OBS
    with np.errstate(invalid="ignore", divide="ignore"):
        sigma2 = np.divide(ss_res, df, out=np.full(n_cells, np.nan), where=df > 0)
        sigma = np.sqrt(np.clip(sigma2, 0.0, None))
        se = np.sqrt(np.clip(sigma2 * xtxi[:, 1, 1], 0.0, None))

        trend = beta[:, 1]
        trendz = np.divide(trend, se, out=np.full(n_cells, np.nan), where=se > 1e-12)
        swing = beta[:, 2]                       # wet minus dry
        amp = np.abs(swing) / 2.0
        peak_month = np.where(swing >= 0.0, SEASON_PEAK_MONTH["wet"],
                              SEASON_PEAK_MONTH["dry"]).astype(np.float64)
        r2 = np.divide(
            ss_res, ss_tot, out=np.full(n_cells, np.nan), where=ss_tot > 1e-12
        )
        r2 = 1.0 - r2

    # The largest departure from the cell's own fitted habit.
    with np.errstate(invalid="ignore", divide="ignore"):
        absz = np.divide(np.abs(resid), sigma[None, :],
                         out=np.full_like(resid, np.inf), where=sigma[None, :] > 1e-12)
    absz = np.where(finite, absz, -np.inf)
    anom_p = np.argmax(absz, axis=0)
    anom = absz[anom_p, np.arange(n_cells)]
    flat = sigma < 1e-12
    anom = np.where(np.isfinite(anom) & ~flat, anom, np.nan)
    anom_p = np.where(np.isfinite(anom), anom_p, -1)

    out = {
        "trend": trend,
        "trendz": trendz,
        "amp": amp,
        "phase": peak_month,
        "r2": r2,
        "anom": anom,
    }
    for key in out:
        out[key] = np.where(ok, out[key], np.nan)
    # A peak with no swing is not a season.
    out["phase"] = np.where(ok & (amp > 1e-6), out["phase"], np.nan)
    out["anom_p"] = np.where(ok, anom_p, -1).astype(np.int64)
    return out


def stability_fields(index_values, band_values, observed) -> dict:
    """Mean consecutive-composite change, per cell.

    churn
        mean |dNDVI| between consecutive observed composites.
    angle
        mean spectral angle, in degrees, between the six-band reflectance
        vectors of consecutive observed composites. Scale-invariant, so it
        responds to the shape of the spectrum rather than its brightness.
        Pairs where any band is missing, or where either summed reflectance
        is below ``ANGLE_MIN_BRIGHTNESS``, are skipped.

    These are the same definitions the viewer used to compute client-side;
    the only change is that they run here so the bands do not have to ship.
    """
    ndvi = np.asarray(index_values["ndvi"], dtype=np.float64)
    bands = np.stack([np.asarray(band_values[b], dtype=np.float64)
                      for b in band_values], axis=0)      # (nBands, P, C)
    obs = np.asarray(observed, dtype=bool)
    n_periods, n_cells = ndvi.shape

    churn = np.full(n_cells, np.nan)
    angle = np.full(n_cells, np.nan)

    for c in range(n_cells):
        seen = np.flatnonzero(obs[:, c])
        if seen.size < 2:
            continue

        acc, cnt = 0.0, 0
        for a, b in zip(seen[:-1], seen[1:]):
            x, y = ndvi[a, c], ndvi[b, c]
            if np.isfinite(x) and np.isfinite(y):
                acc += abs(y - x)
                cnt += 1
        if cnt:
            churn[c] = acc / cnt

        acc, cnt = 0.0, 0
        for a, b in zip(seen[:-1], seen[1:]):
            va = bands[:, a, c]
            vb = bands[:, b, c]
            if not (np.isfinite(va).all() and np.isfinite(vb).all()):
                continue
            if va.sum() < ANGLE_MIN_BRIGHTNESS or vb.sum() < ANGLE_MIN_BRIGHTNESS:
                continue
            denom = np.sqrt(np.dot(va, va) * np.dot(vb, vb))
            if denom < 1e-12:
                continue
            cos = np.clip(np.dot(va, vb) / denom, -1.0, 1.0)
            acc += np.degrees(np.arccos(cos))
            cnt += 1
        if cnt:
            angle[c] = acc / cnt

    return {"stab.angle": angle, "stab.churn": churn}


def analyse(periods, index_values, observed, band_values=None):
    """Assessment fields for one block.

    Returns ``(cell, cellraw)``: quantised float fields keyed ``metric.index``
    and raw integer fields keyed ``name.index``. Both are per cell, so they
    follow the change-layer shape rather than the period-major shape.
    """
    cell: dict[str, np.ndarray] = {}
    cellraw: dict[str, np.ndarray] = {}

    for name, values in index_values.items():
        fit = fit_series(values, periods, observed)
        for metric in ("trend", "trendz", "amp", "phase", "r2", "anom"):
            cell[f"{metric}.{name}"] = fit[metric]
        cellraw[f"anomp.{name}"] = fit["anom_p"]

    if band_values:
        cell.update(stability_fields(index_values, band_values, observed))

    return cell, cellraw
