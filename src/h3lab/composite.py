"""Temporal compositing: turn per-scene hexagon aggregates into a
per-cell, per-composite feature stack.

Compositing order matters. A median is taken *across observations* of the
per-cell band means, rather than across the pixels inside a cell. That keeps
the expensive part (reading pixels, binning into cells) done once per scene and
leaves the temporal robustness where the noise actually is: a single hazy or
partly-cloudy acquisition shifts a cell's mean, but it cannot shift the median
of a dozen of them.
"""

from __future__ import annotations

import json
import os

import numpy as np

from .config import BANDS, MIN_VALID_FRACTION


class CompositeAccumulator:
    """Collect per-observation cell aggregates and reduce them to a median."""

    def __init__(self, n_cells: int, stat: str = "median"):
        self.n = n_cells
        self.stat = stat
        self._obs: dict[str, list[np.ndarray]] = {}

    def add(self, res: int, agg: dict):
        bands = agg["bands"]
        stack = {
            "band": np.stack([bands[b] for b in BANDS]),          # (nb, n)
            "n_valid": agg["n_valid"].astype(np.float64),         # (n,)
            "n_total": agg["n_total"].astype(np.float64),         # (n,)
            "n_obs": agg["n_obs"].astype(np.float64),              # (n,)
        }
        self._obs.setdefault(str(res), []).append(stack)

    @property
    def n_added(self) -> int:
        return max((len(v) for v in self._obs.values()), default=0)

    def reduce(self, res: int, min_valid_fraction: float = MIN_VALID_FRACTION):
        """Reduce to per-cell medians. Returns None if nothing was added."""
        key = str(res)
        if key not in self._obs:
            return None
        obs = self._obs[key]

        def reduce_band(bi):
            arr = np.stack([o["band"][bi] for o in obs])       # (n_obs, n)
            if self.stat == "mean":
                with np.errstate(invalid="ignore"):
                    return np.nanmean(arr, axis=0)
            return np.nanmedian(arr, axis=0)

        refl = {b: reduce_band(i) for i, b in enumerate(BANDS)}

        n_obs = np.sum(np.stack([o["n_obs"] for o in obs]), axis=0)

        # Valid fraction must be formed per observation before reducing.
        # Summing n_valid across observations while taking a max of n_total
        # inflates the numerator by the number of scenes and drives the ratio
        # to ~0, which silently marks every cell unobserved.
        frac_per_obs = np.stack([
            np.divide(o["n_valid"], np.maximum(o["n_total"], 1.0)) for o in obs
        ])
        valid_frac = frac_per_obs.mean(axis=0)

        observed = (valid_frac >= min_valid_fraction) & (n_obs > 0)

        # Blank out cells that failed the support threshold so they cannot leak
        # a plausible-looking value into the viewer's colour ramp.
        for b in BANDS:
            refl[b] = np.where(observed, refl[b], np.nan)

        return {
            "refl": refl,
            "valid_fraction": valid_frac.astype(np.float32),
            "n_obs": n_obs.astype(np.int32),
            "observed": observed,
        }


def save_composite(path: str, res: int, year: int, season: str, reduced: dict):
    """Persist one reduced composite as a compressed npz."""
    payload = {
        f"refl_{b}": reduced["refl"][b] for b in BANDS
    }
    payload["valid_fraction"] = reduced["valid_fraction"]
    payload["n_obs"] = reduced["n_obs"]
    payload["observed"] = reduced["observed"]
    payload["meta"] = np.array([json.dumps({"res": res, "year": year, "season": season})])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    np.savez_compressed(path, **payload)


def load_composite(path: str):
    z = np.load(path, allow_pickle=False)
    meta = json.loads(str(z["meta"][0]))
    refl = {b: z[f"refl_{b}"] for b in BANDS}
    return meta, refl, {
        "valid_fraction": z["valid_fraction"],
        "n_obs": z["n_obs"],
        "observed": z["observed"],
    }