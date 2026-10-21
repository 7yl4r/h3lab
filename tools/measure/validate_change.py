"""Validate the change layer against the real 2017 res-8 NDVI series.

Three questions:
  1. What does the detector actually report on the real data?
  2. Is the false-positive rate on real data what the threshold claims? The
     null is the *fitted* series with its own residuals shuffled, so it keeps
     the real noise, the real seasonal shape and the real observation
     pattern, and has no step in it.
  3. Does it find a step that is really there?
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from h3lab import bins                                      # noqa: E402
from h3lab.change import _design_matrix, _fscan, detect_breaks  # noqa: E402
from h3lab.composite import load_composite                 # noqa: E402
from h3lab.features import _ratio                           # noqa: E402

CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cache_regional")


def load_series(res=8):
    bl = bins.bins_for_year(2017)
    present = [b for b in bl
               if os.path.exists(os.path.join(CACHE, f"bin{b}_res{res}.npz"))]
    rows = []
    for b in present:
        _, refl, sup = load_composite(os.path.join(CACHE, f"bin{b}_res{res}.npz"))
        rows.append(_ratio(refl["nir"], refl["red"]))
    return present, np.vstack(rows)


def main():
    used_bins, Y = load_series()
    n, n_cells = Y.shape
    print(f"{n} periods, {n_cells:,} cells")

    r = detect_breaks(Y, used_bins)
    flagged = r["break_bin"] >= 0
    n_flag = int(flagged.sum())
    print(f"\n1. what it reports")
    print(f"   cells with a break: {n_flag:,} ({100 * n_flag / n_cells:.2f}%)")
    sc = r["break_score"][np.isfinite(r["break_score"])]
    if sc.size:
        print(f"   score: median {np.median(sc):.1f}, p90 {np.percentile(sc, 90):.1f}, "
              f"max {sc.max():.1f}")
    mag = r["break_magnitude"][np.isfinite(r["break_magnitude"])]
    if mag.size:
        print(f"   magnitude: median {np.median(mag):+.3f}, "
              f"p10 {np.percentile(mag, 10):+.3f}, p90 {np.percentile(mag, 90):+.3f}")
    bb = r["break_bin"][flagged]
    vals, counts = np.unique(bb, return_counts=True)
    print("   break bins:")
    for v, c in zip(vals, counts):
        print(f"     bin {v} ({bins.bin_centre(int(v))}): {c:,}")

    # -- 2. the real null: the fitted series with its residuals shuffled ----
    print(f"\n2. false positives on the real null")
    rng = np.random.default_rng(7)
    t_all = np.array([(bins.bin_centre(b) - bins.EPOCH).days for b in used_bins],
                      dtype=float)
    finite = np.isfinite(Y)
    masks = (finite.astype(np.int64) * (1 << np.arange(n))[:, None]).sum(axis=0)
    # per-cell: fit the seasonal model on the cell's own periods, then
    # shuffle the residuals to destroy any step while keeping the noise.
    shuffled = np.full_like(Y, np.nan)
    for mask in np.unique(masks):
        cells = np.flatnonzero(masks == mask)
        rows = np.flatnonzero((mask & (1 << np.arange(n))) != 0)
        if rows.size < 10:
            continue
        t = t_all[rows]
        Yc = Y[rows][:, cells]
        X = _design_matrix(t, 2, True)
        beta = np.linalg.pinv(X.T @ X) @ (X.T @ Yc)
        resid = Yc - X @ beta
        for j in range(cells.size):
            shuffled[rows, cells[j]] = X @ beta[:, j] + rng.permutation(resid[:, j])
    r2 = detect_breaks(shuffled, used_bins)
    n2 = int((r2["break_bin"] >= 0).sum())
    print(f"   cells with a break in the shuffled null: {n2:,} "
          f"({100 * n2 / n_cells:.2f}%) -- the threshold claims 1%")

    # -- 3. power: inject a step of a known size --------------------------
    print(f"\n3. power against an injected step")
    for delta in (0.1, 0.2, 0.4):
        Ys = Y.copy()
        Ys[12:, :] += delta
        r3 = detect_breaks(Ys, used_bins)
        n3 = int((r3["break_bin"] >= 0).sum())
        right = int((r3["break_bin"] == used_bins[12]).sum())
        print(f"   +{delta:.1f} at period 12: {n3:,} flagged "
              f"({100 * n3 / n_cells:.1f}%), {right:,} at the right bin")


if __name__ == "__main__":
    main()
