"""Change-detector power, conditioned on how much of the year a cell saw.

`validate_change.py` reports the headline power: a step of +0.2 NDVI injected
at period 12 is found at the right bin for 3.6 % of the res-8 cells. That
number is diluted by coverage -- a cell that was not observed after the
injected step cannot show it -- so the useful measurement is power *given*
the number of observations.

The detector also needs a noise scale to be compared against, so the
residual scatter of the real series is measured here too.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from h3lab import bins                                      # noqa: E402
from h3lab.change import detect_breaks                       # noqa: E402
from h3lab.composite import load_composite                   # noqa: E402
from h3lab.features import _ratio                            # noqa: E402

CACHE = os.path.join(os.path.dirname(__file__), "..", "..", "data", "cache_regional")
STEP_ROW = 12


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
    used, Y = load_series()
    n, n_cells = Y.shape
    n_obs = np.isfinite(Y).sum(axis=0)
    print(f"{n} periods, {n_cells:,} cells")
    print(f"observations per cell: median {np.median(n_obs):.0f}, "
          f"p10 {np.percentile(n_obs, 10):.0f}, p90 {np.percentile(n_obs, 90):.0f}")
    print(f"cells observed in all {n} periods: {(n_obs == n).sum():,}")

    # how much of the step a cell can actually see
    post = np.isfinite(Y[STEP_ROW:, :]).sum(axis=0)
    print(f"cells with >= 1 observation after the step row: "
          f"{(post > 0).sum():,} ({(post > 0).mean():.1%})")

    # the real noise: first differences of consecutive observed bins
    d = np.diff(Y, axis=0)
    sd = np.nanstd(d, axis=0)
    print(f"first-difference sd: median {np.nanmedian(sd):.4f}, "
          f"p90 {np.nanpercentile(sd, 90):.4f}")

    for delta in (0.1, 0.2, 0.4):
        Ys = Y.copy()
        Ys[STEP_ROW:, :] += delta
        r = detect_breaks(Ys, used)
        at_right = r["break_bin"] == used[STEP_ROW]
        print(f"\nstep +{delta:.1f} at period {STEP_ROW} (bin {used[STEP_ROW]}):")
        print(f"   flagged anywhere: {(r['break_bin'] >= 0).sum():,} "
              f"({(r['break_bin'] >= 0).mean():.1%})")
        for lo in (0, 12, 16, 18, 20, 22, 23):
            sel = n_obs >= lo
            if not sel.any():
                continue
            print(f"   n_obs >= {lo:2d}: {int(sel.sum()):>7,} cells, "
                  f"{100 * at_right[sel].mean():5.1f}% found at the right bin, "
                  f"{100 * (r['break_bin'][sel] >= 0).mean():5.1f}% flagged")
        sel = post > 0
        print(f"   observed after the step: {100 * at_right[sel].mean():.1f}% "
              f"at the right bin")
        # the estimated magnitude where it was found
        m = r["break_magnitude"][at_right]
        if np.isfinite(m).any():
            print(f"   estimated magnitude at the right bin: "
                  f"median {np.nanmedian(m):+.3f} (true {delta:+.3f})")


if __name__ == "__main__":
    main()
