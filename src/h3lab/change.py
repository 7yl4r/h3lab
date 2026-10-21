"""Change-point detection: harmonic regression with a step-regressor F-scan.

Deliberately rough (PHASE1 §10). Twenty-three 16-day bins is one year of
data, which is barely enough to fit a trend, a seasonal cycle and a step
change at the same time. The point of Phase 1 is that the fields exist, carry
a number, and record the algorithm that produced it -- not that the number is
trustworthy.

The method, per DESIGN.md §6.1:

1. Fit a harmonic regression -- intercept, linear trend, and *n* annual
   harmonics -- to the NDVI series in **continuous time**. The seasonal period
   is 365.25 days, not the bin count: 16-day bins do not divide the year
   evenly (22.83 per year), so an integer seasonal period is awkward and
   bin-index modulo is wrong (D-18).
2. For every interior candidate break, fit the same model *plus a step
   regressor* and score the step with a partial F-test. The candidate with
   the largest F is the reported break.
3. Report that break, its size, and the F statistic.

Why a partial F and not a CUSUM on the residuals
------------------------------------------------

The first version of this module ran Page's CUSUM on the harmonic residuals.
It works, but it is the wrong statistic, and measurably so. Both statistics
rank candidate breaks by the same quantity -- the difference in mean residual
before and after the break -- so they find the same break. They differ in how
they normalise it:

* the CUSUM divides by the residual spread of the *seasonal-only* model;
* the F divides by the residual spread *after* the step has been removed.

At a real break the second is much the smaller number, because removing the
step really does remove variance, and the separation between "there is a
break" and "there is not" is far better. On 23-bin series with a seasonal
amplitude of 0.35 and AR(1) noise (sigma 0.03):

    step size          CUSUM power    F-scan power
    +0.05                 4%              4%
    +0.10                 1%             19%
    +0.20                 0%             78%
    +0.40                 0%            100%

(5% false-positive rate, 200 simulated series per step size.) The CUSUM does
not merely score lower; at the 5% level it never fires on a +0.20 step, so the
layer carried no information at all. The F-scan is also a standard statistic
with a known null: F(1, n - p - 1) for a fixed candidate, and the scan over
candidates is calibrated by simulation (see `_null_threshold`).

What the harmonics do and do not spoil
--------------------------------------

The seasonal fit can *absorb* part of a step, which is the fundamental
limitation of a single year of data and the reason Phase 2 exists. But the
absorption costs power, not accuracy: the fitted step coefficient is
estimated from the joint model, and on the simulated series above it recovers
the true step size to within a few percent (0.196 estimated for a 0.20 step).
The *raw* difference of means before and after the break is badly biased --
it is swamped by the seasonal curve -- and that is what "break_magnitude is a
residual change" refers to.

On the *real* 2017 series the recovery is worse, and for a reason worth
knowing: a magnitude is stored only for the cells that cleared the threshold,
and the cells that clear it are the noisiest ones, so the selection inflates
the step they appear to have. Measured on the 128,236 res-8 cells: an
injected +0.10 step comes out at a median +0.205 and an injected +0.40 step
at +0.423. The stored magnitude is therefore a *relative* signal -- useful
for ranking, not for quoting as a measured change.

The harmonics are not the reason the real series flags more than the null
does. Real data flags 4.5 % of cells against 0.80 % on the shuffled-residual
null, and that excess is *ordered* residual structure that is not a step (a
one-bin dip in a 23-point series is a step, statistically). Fitting the
season better does not remove it: with three harmonics the real flag rate
falls only from 4.69 % to 4.21 % on a 25,000-cell subsample, while the
null's false-positive rate rises to 1.30 % and the power of a +0.20 step
falls from 3.64 % to 2.58 %. Two harmonics is kept.

The threshold
-------------

The F statistic is scale-free, so the same threshold applies to every cell,
but the scan over ~20 candidate breaks makes the null wider than a single
test's. The threshold is therefore taken from a simulated null: series with
the same seasonal shape, AR(1) noise, no step, scanned the same way. The
quantile of that null at 1 - alpha is the threshold, and alpha is recorded in
the method string. Measured null quantiles (n = 23, 2 harmonics, AR(1)
phi = 0.4, 600 simulations): median 7.2, p95 15.9, p99 21.1.

Every stored break carries the algorithm name, version and parameters
(D-14): an unexplained derived field is not reproducible.
"""

from __future__ import annotations

import numpy as np

from .bins import BIN_DAYS, EPOCH, bin_centre

YEAR_DAYS = 365.25
METHOD_ID = "harmonic_fscan_v1"

# The null calibration defaults. These are recorded in the method string, so
# a stored layer can be reproduced without reading this source (D-14).
DEFAULT_ALPHA = 0.01       # per-cell false-positive rate
DEFAULT_AR = 0.4           # AR(1) coefficient of the null's noise
DEFAULT_N_SIM = 200        # null simulations per observation count
DEFAULT_SEED = 20260101    # the null is a simulation; the seed makes it fixed
NULL_SIGMA = 0.03          # noise scale of the null; the F statistic ignores it
NULL_AMPLITUDE = 0.35      # seasonal amplitude of the null
NULL_LEVEL = 0.45          # level of the null

_THRESHOLD_CACHE: dict = {}


def _design_matrix(t_days: np.ndarray, n_harmonics: int,
                   trend: bool = True) -> np.ndarray:
    """[1, t, sin/cos annual harmonics] at continuous day offsets."""
    cols = [np.ones_like(t_days)]
    if trend:
        cols.append(t_days / YEAR_DAYS)
    for h in range(1, n_harmonics + 1):
        w = 2.0 * np.pi * h * t_days / YEAR_DAYS
        cols.append(np.sin(w))
        cols.append(np.cos(w))
    return np.column_stack(cols)


def _fscan(t_days: np.ndarray, Y: np.ndarray, n_harmonics: int,
           trend: bool = True):
    """Scan every interior candidate break for the best partial F.

    `Y` is (n_periods, n_cells) -- one column per series, rows in time order.
    The series may have missing values only by being *absent* from `Y`; a
    cell with gaps is handled by fitting the model to its own periods.

    For a candidate break at row k the step regressor is 1 for rows k..n-1.
    Writing M for the residual maker of that regressor against the seasonal
    model, the partial F is

        c = M' y,   d = M' M,   SSR reduction = c^2 / d
        F = (c^2 / d) / (RSS_after / (n - p - 1))

    and the fitted step is c / d. Both c and d are available in closed form
    from the seasonal model's own hat matrix, so the whole scan costs a
    handful of matrix products rather than one least-squares solve per
    candidate.

    Returns (best_k, best_f, best_step), each of length n_cells. `best_k` is
    the row at which the *new* regime starts, matching the convention that
    `break_bin` reports the first period of the new regime.
    """
    n, n_cells = Y.shape
    X = _design_matrix(t_days, n_harmonics, trend)
    p = X.shape[1]
    df = n - p - 1
    if df <= 0:
        z = np.zeros(n_cells)
        return np.zeros(n_cells, dtype=np.int64), z, z

    gram_inv = np.linalg.pinv(X.T @ X)
    beta = gram_inv @ (X.T @ Y)                   # (p, n_cells)
    resid = Y - X @ beta
    rss_base = np.einsum("ij,ij->j", resid, resid)  # (n_cells,)
    suffix = np.cumsum(Y[::-1], axis=0)[::-1]       # suffix[k] = sum(Y[k:])

    best_f = np.full(n_cells, -np.inf)
    best_k = np.zeros(n_cells, dtype=np.int64)
    best_step = np.zeros(n_cells)

    for k in range(2, n - 1):
        v = X[k:].sum(axis=0)                 # X' I_k
        d = (n - k) - float(v @ gram_inv @ v)  # I_k' M I_k
        if d <= 1e-9:
            continue
        c = suffix[k] - v @ beta              # M' y
        ssr = (c * c) / d                     # RSS reduction from the step
        rss_step = rss_base - ssr
        with np.errstate(divide="ignore", invalid="ignore"):
            f = np.where(rss_step > 0.0, ssr / (rss_step / df),
                         np.where(ssr > 0.0, np.inf, 0.0))
        f = np.where(np.isfinite(f) | (f == np.inf), f, -np.inf)
        f = np.where(rss_step >= 0.0, f, -np.inf)
        better = f > best_f
        if np.any(better):
            best_f = np.where(better, f, best_f)
            best_k = np.where(better, k, best_k)
            best_step = np.where(better, c / d, best_step)
    return best_k, best_f, best_step


def _null_threshold(t_days: np.ndarray, n_obs: int, alpha: float, ar: float,
                    n_sim: int, seed: int, n_harmonics: int,
                    trend: bool = True) -> float:
    """The (1 - alpha) quantile of the scan statistic under the null.

    The null series carry a seasonal shape, AR(1) noise and no step. They are
    scanned exactly as the data are, so the threshold accounts for the scan
    over candidate breaks as well as for the noise.

    The result is cached per (n_obs, t): the null is a property of the
    design, not of the cell, and cells that share a design share a
    threshold. The threshold is not quite the same for two cells with the
    same number of observations but different ones -- the design decides how
    well the seasonal model can absorb a step -- and the spread over
    patterns is up to 16 % for 10 observed bins, so the cache is keyed on the
    observed periods and not on the count alone. A calibration costs about
    4 ms, which is nothing against a thousand masks.
    """
    key = (n_obs, t_days.tobytes(), alpha, ar, n_sim, seed, n_harmonics, trend)
    hit = _THRESHOLD_CACHE.get(key)
    if hit is not None:
        return hit

    rng = np.random.default_rng(seed)
    t = np.asarray(t_days, dtype=float)[:n_obs]
    phase = rng.uniform(0.0, 2.0 * np.pi, n_sim)
    noise = np.zeros((n_obs, n_sim))
    for i in range(1, n_obs):
        noise[i] = ar * noise[i - 1] + rng.normal(0.0, NULL_SIGMA, n_sim)
    Y = np.empty((n_obs, n_sim))
    for i in range(n_obs):
        Y[i] = (NULL_LEVEL
                + NULL_AMPLITUDE * np.sin(2.0 * np.pi * t[i] / YEAR_DAYS
                                          + phase)
                + noise[i])

    _, f, _ = _fscan(t_days, Y, n_harmonics, trend)
    thr = float(np.quantile(f, 1.0 - alpha))
    _THRESHOLD_CACHE[key] = thr
    return thr


def detect_breaks(ndvi_series: np.ndarray, bins_list=None, min_obs: int = 8,
                  n_harmonics: int = 2, trend: bool = True,
                  alpha: float = DEFAULT_ALPHA, ar: float = DEFAULT_AR,
                  n_sim: int = DEFAULT_N_SIM, seed: int = DEFAULT_SEED,
                  min_score: float | None = None):
    """Detect breaks in an NDVI series.

    ndvi_series: (n_periods, n_cells) -- one row per period, NaN where the
                  cell was not observed.
    bins_list:    the bin number of each period. Defaults to 0..n-1.
    min_obs:      minimum number of observations before a cell is fitted.
    n_harmonics:  number of annual harmonics in the regression.
    alpha:        per-cell false-positive rate of the detection threshold.
    ar:           AR(1) coefficient of the simulated null.
    min_score:    an explicit threshold on the F statistic, overriding the
                  simulated null. Used by the tests; None means "calibrate".

    Returns a dict of per-cell arrays: break_bin, break_date,
    break_magnitude, break_score, n_obs_used, method.
    """
    ndvi_series = np.asarray(ndvi_series, dtype=np.float64)
    if ndvi_series.ndim == 1:
        ndvi_series = ndvi_series.reshape(-1, 1)
    n_periods, n_cells = ndvi_series.shape

    if bins_list is None:
        bins_list = list(range(n_periods))
    bins_list = [int(b) for b in bins_list]

    break_bin = np.full(n_cells, -1, dtype=np.int32)
    break_date = np.full(n_cells, "", dtype=object)
    break_magnitude = np.full(n_cells, np.nan, dtype=np.float32)
    break_score = np.full(n_cells, np.nan, dtype=np.float32)
    n_obs_used = np.zeros(n_cells, dtype=np.int32)

    # The stored method carries the algorithm name, version *and* parameters,
    # so the layer can be reproduced without reading this source (D-14).
    method_spec = (f"{METHOD_ID}(harmonics={n_harmonics},trend={int(trend)},"
                   f"min_obs={min_obs},scan=step-dummy,fstat=partial-F,"
                   f"null=ar1(phi={ar}),nsim={n_sim},seed={seed},"
                   f"alpha={alpha})")
    method = np.array([method_spec] * n_cells, dtype=object)

    # Continuous time: days from the epoch to the centre of each period.
    t_all = np.array([(bin_centre(b) - EPOCH).days for b in bins_list],
                     dtype=float)
    centres = [bin_centre(b).isoformat() for b in bins_list]

    finite = np.isfinite(ndvi_series)
    n_obs_all = finite.sum(axis=0)
    n_obs_used[:] = n_obs_all

    # Cells are grouped by *which* periods they have, because the model is
    # fitted to each cell's own periods. Two cells with the same mask share
    # the design matrix, the fit and the threshold.
    masks = (finite.astype(np.int64)
             * (1 << np.arange(n_periods))[:, None]).sum(axis=0)

    min_fit = max(min_obs, _design_matrix(t_all, n_harmonics, trend).shape[1] + 2)
    thresholds: dict = {}
    bin_arr = np.asarray(bins_list, dtype=np.int64)

    for mask in np.unique(masks):
        cells = np.flatnonzero(masks == mask)
        rows = np.flatnonzero((mask & (1 << np.arange(n_periods))) != 0)
        n_obs = rows.size
        if n_obs < min_fit:
            continue
        t = t_all[rows]
        Y = ndvi_series[rows][:, cells]

        best_k, best_f, best_step = _fscan(t, Y, n_harmonics, trend)

        if min_score is not None:
            thr = float(min_score)
        else:
            thr = _null_threshold(t, n_obs, alpha, ar, n_sim, seed,
                                  n_harmonics, trend)
        thresholds[(int(n_obs), t.tobytes())] = thr

        hit = best_f >= thr
        if not np.any(hit):
            continue
        # `best_k` is the row at which the new regime starts, so the break is
        # reported at the first period of the new regime. Reporting the last
        # period of the old one is an off-by-one that shifts every break back
        # by one bin -- 16 days, which is enough to move an event off the bin
        # it belongs to.
        period = rows[best_k]
        bin_arr = np.asarray(bins_list, dtype=np.int64)
        break_bin[cells] = np.where(hit, bin_arr[period], -1).astype(np.int32)
        texts = [centres[p] for p in period]
        for cell, ok, text in zip(cells, hit, texts):
            if ok:
                break_date[cell] = text
        break_magnitude[cells] = np.where(hit, best_step, np.nan).astype(np.float32)
        break_score[cells] = np.where(hit, best_f, np.nan).astype(np.float32)

    return {
        "break_bin": break_bin,
        "break_date": break_date,
        "break_magnitude": break_magnitude,
        "break_score": break_score,
        "n_obs_used": n_obs_used,
        "method": method,
        "method_spec": method_spec,
        "bins": bins_list,
        "thresholds": thresholds,
    }
