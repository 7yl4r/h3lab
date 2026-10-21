"""Does the seasonal model have enough harmonics for a single year?

Two things came out of the power measurement on the real 2017 data:

  * the detector flags 4.5 % of cells on the real series but only 0.8 % on
    the shuffled-residual null, which claims a 1 % false-positive rate; and
  * the estimated magnitude of an injected +0.10 step comes out at +0.205.

Both have the same possible cause: the seasonal cycle of a single year is
not a two-harmonic sinusoid, so the residual misfit is *ordered* -- it looks
like a step, and the scan finds it. More harmonics fit the cycle better and
should shrink that misfit.

This runs the detector with 2, 3 and 4 harmonics on the same subsample and
reports, for each: the flag rate on the real series, the flag rate on the
shuffled-residual null (the calibrated false-positive rate), and the power
and magnitude bias for an injected +0.20 step.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from h3lab import bins                                      # noqa: E402
from h3lab.change import _design_matrix, detect_breaks      # noqa: E402
from h3lab.composite import load_composite                  # noqa: E402
from h3lab.features import _ratio                           # noqa: E402

CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cache_regional")
N_CELLS = 25000
STEP_ROW = 12
DELTA = 0.20


def load_series(res=8):
    bl = bins.bins_for_year(2017)
    present = [b for b in bl
               if os.path.exists(os.path.join(CACHE, f"bin{b}_res{res}.npz"))]
    rows = []
    for b in present:
        _, refl, sup = load_composite(os.path.join(CACHE, f"bin{b}_res{res}.npz"))
        rows.append(_ratio(refl["nir"], refl["red"]))
    return present, np.vstack(rows)


def shuffled_null(Y, used_bins, n_harmonics=2, seed=7):
    """The fitted series with its residuals shuffled: no step, real noise."""
    rng = np.random.default_rng(seed)
    n = Y.shape[0]
    t_all = np.array([(bins.bin_centre(b) - bins.EPOCH).days for b in used_bins],
                      dtype=float)
    finite = np.isfinite(Y)
    masks = (finite.astype(np.int64) * (1 << np.arange(n))[:, None]).sum(axis=0)
    out = np.full_like(Y, np.nan)
    for mask in np.unique(masks):
        cells = np.flatnonzero(masks == mask)
        rows = np.flatnonzero((mask & (1 << np.arange(n))) != 0)
        if rows.size < 10:
            continue
        t = t_all[rows]
        Yc = Y[rows][:, cells]
        X = _design_matrix(t, n_harmonics, True)
        beta = np.linalg.pinv(X.T @ X) @ (X.T @ Yc)
        resid = Yc - X @ beta
        for j in range(cells.size):
            out[rows, cells[j]] = X @ beta[:, j] + rng.permutation(resid[:, j])
    return out


def main():
    used, Y = load_series()
    rng = np.random.default_rng(11)
    n_cells = Y.shape[1]
    keep = rng.choice(n_cells, size=min(N_CELLS, n_cells), replace=False)
    Y = Y[:, keep]
    n = Y.shape[0]
    print(f"{n} periods, {Y.shape[1]:,} sampled cells")

    n_obs = np.isfinite(Y).sum(axis=0)
    print(f"observations per cell: median {np.median(n_obs):.0f}, "
          f"fully covered {int((n_obs == n).sum()):,}")

    Ys = Y.copy()
    Ys[STEP_ROW:, :] += DELTA

    for nh in (2, 3):
        print(f"\n=== {nh} harmonics ===")
        Yshuf = shuffled_null(Y, used, n_harmonics=nh)
        for label, series in (("real", Y), ("null", Yshuf), ("step", Ys)):
            r = detect_breaks(series, used, n_harmonics=nh)
            flagged = r["break_bin"] >= 0
            at_right = r["break_bin"] == used[STEP_ROW]
            line = (f"   {label:<5s} flagged {100 * flagged.mean():5.2f}%  "
                    f"at the right bin {100 * at_right.mean():5.2f}%")
            if label == "step":
                m = r["break_magnitude"][at_right]
                if np.isfinite(m).any():
                    line += (f"   magnitude median {np.nanmedian(m):+.3f} "
                             f"(true {DELTA:+.2f})")
            print(line, flush=True)


if __name__ == "__main__":
    main()
