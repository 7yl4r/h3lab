#!/usr/bin/env python3
"""Unit tests for the per-cell assessment and the fields it adds to a block.

    python tools/test_analysis.py

Everything here is arithmetic on synthetic records, so it runs without a build
and without the raster cache. The point is to pin the properties the layers
claim: a known slope and season come back, a planted spike is the largest
anomaly, a short record is not fitted, and stability is scale-invariant.
"""

from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PROJECT, "src"))

from h3lab.analysis import (                                    # noqa: E402
    MIN_FIT_OBS, SPECS, analyse, fit_series, stability_fields,
)
from h3lab.binary import MISSING, read_block, write_block       # noqa: E402

PASSED = []
FAILED = []

YEARS = list(range(2013, 2026))
PERIODS = [{"year": y, "season": s} for y in YEARS for s in ("wet", "dry")]
YEAR = np.array([p["year"] for p in PERIODS], dtype=float)
WET = np.array([p["season"] == "wet" for p in PERIODS], dtype=float)
BANDS = ("blue", "green", "red", "nir", "swir1", "swir2")


def check(name, fn):
    try:
        fn()
    except AssertionError as exc:
        FAILED.append((name, str(exc)))
        print(f"[FAIL] {name}: {exc}")
    except Exception as exc:                                    # noqa: BLE001
        FAILED.append((name, repr(exc)))
        print(f"[FAIL] {name}: {exc!r}")
    else:
        PASSED.append(name)
        print(f"[ok]   {name}")


def _fit(values):
    return fit_series(np.asarray(values, dtype=float)[:, None], PERIODS)


# --------------------------------------------------------------------------
# the decomposition
# --------------------------------------------------------------------------
def test_trend_and_swing_recovered():
    y = 0.30 + 0.01 * (YEAR - 2019) + 0.20 * WET
    r = _fit(y)
    assert abs(r["trend"][0] - 0.01) < 1e-9, r["trend"]
    assert abs(r["amp"][0] - 0.10) < 1e-9, r["amp"]
    assert r["phase"][0] == 8, f"wet peak should be month 8, got {r['phase']}"
    assert abs(r["r2"][0] - 1.0) < 1e-9, r["r2"]


def test_dry_peak_is_january():
    y = 0.30 + 0.20 * (1 - WET)
    r = _fit(y)
    assert r["phase"][0] == 1, f"dry peak should be month 1, got {r['phase']}"
    assert abs(r["amp"][0] - 0.10) < 1e-9, r["amp"]


def test_planted_spike_is_the_largest_anomaly():
    rng = np.random.default_rng(1)
    y = 0.30 + 0.01 * (YEAR - 2019) + 0.20 * WET + rng.normal(0, 0.01, YEAR.size)
    at = 10
    y[at] += 0.25
    r = _fit(y)
    assert r["anom_p"][0] == at, f"anomaly at {r['anom_p']}, planted at {at}"
    assert r["anom"][0] > 3.0, f"planted spike only scored {r['anom']}"


def test_gaps_do_not_bias_the_fit():
    rng = np.random.default_rng(2)
    y = 0.30 + 0.01 * (YEAR - 2019) + 0.20 * WET + rng.normal(0, 0.01, YEAR.size)
    y[::3] = np.nan
    r = _fit(y)
    assert abs(r["trend"][0] - 0.01) < 2e-3, r["trend"]
    assert abs(r["amp"][0] - 0.10) < 2e-2, r["amp"]
    assert r["phase"][0] == 8, r["phase"]


def test_a_short_record_is_not_fitted():
    y = np.full(YEAR.size, np.nan)
    y[: MIN_FIT_OBS - 1] = 0.3
    r = _fit(y)
    assert np.isnan(r["trend"][0]), "thin record must not produce a trend"
    assert r["anom_p"][0] == -1, "thin record must not name an anomaly"


def test_trend_significance_separates_signal_from_noise():
    rng = np.random.default_rng(3)
    signal = 0.30 + 0.01 * (YEAR - 2019) + rng.normal(0, 0.01, YEAR.size)
    flat = 0.30 + rng.normal(0, 0.01, YEAR.size)
    a, b = _fit(signal), _fit(flat)
    assert a["trendz"][0] > 4, f"real trend should be significant, z={a['trendz']}"
    assert abs(b["trendz"][0]) < 3, f"noise should not be significant, z={b['trendz']}"
    assert a["r2"][0] > b["r2"][0], "the signal should explain more than the noise"


def test_flat_noise_has_low_r2():
    rng = np.random.default_rng(4)
    r = _fit(rng.normal(0.3, 0.02, YEAR.size))
    assert r["r2"][0] < 0.3, f"noise fit should explain little, r2={r['r2']}"


# --------------------------------------------------------------------------
# stability
# --------------------------------------------------------------------------
def _bands(values):
    """Six bands with an identical value per composite, shaped as (P, C)."""
    a = np.asarray(values, dtype=float)
    return {b: a.copy() for b in BANDS}


def test_churn_is_the_mean_consecutive_step():
    st = stability_fields({"ndvi": np.array([[0.1], [0.2], [0.3], [0.4]])},
                          _bands([[0.2], [0.2], [0.2], [0.2]]),
                          np.ones((4, 1), dtype=bool))
    assert abs(st["stab.churn"][0] - 0.1) < 1e-12, st["stab.churn"]


def test_churn_pairs_across_gaps():
    seen = np.array([[1], [0], [1], [1]], dtype=bool)
    st = stability_fields({"ndvi": np.array([[0.1], [9.9], [0.3], [0.4]])},
                          _bands([[0.2], [0.2], [0.2], [0.2]]), seen)
    # pairs are (0,2) and (2,3): (0.2 + 0.1) / 2, never the unobserved 9.9
    assert abs(st["stab.churn"][0] - 0.15) < 1e-12, st["stab.churn"]


def test_angle_ignores_brightness_but_not_shape():
    base = np.array([0.10, 0.12, 0.08, 0.30, 0.15, 0.10])
    doubled = base * 2.0
    bands = {b: np.array([[base[i]], [doubled[i]]]) for i, b in enumerate(BANDS)}
    st = stability_fields({"ndvi": np.array([[0.4], [0.4]])}, bands,
                          np.ones((2, 1), dtype=bool))
    assert abs(st["stab.angle"][0]) < 1e-9, "scaling the spectrum must not rotate it"

    darker_red = base.copy()
    darker_red[2] *= 0.2
    bands2 = {b: np.array([[base[i]], [darker_red[i]]]) for i, b in enumerate(BANDS)}
    st2 = stability_fields({"ndvi": np.array([[0.4], [0.4]])}, bands2,
                           np.ones((2, 1), dtype=bool))
    assert st2["stab.angle"][0] > 1.0, "a changed shape must produce an angle"


def test_dark_spectra_are_skipped_for_angle():
    dark = np.full(6, 0.01)                     # summed reflectance 0.06 < 0.10
    bands = {b: np.array([[dark[i]], [dark[i]]]) for i, b in enumerate(BANDS)}
    st = stability_fields({"ndvi": np.array([[0.4], [0.4]])}, bands,
                          np.ones((2, 1), dtype=bool))
    assert np.isnan(st["stab.angle"][0]), "dark water must not get an angle"
    assert np.isfinite(st["stab.churn"][0]), "NDVI churn does not need brightness"


# --------------------------------------------------------------------------
# block fields
# --------------------------------------------------------------------------
def _toy_block(with_band=False):
    n_cells, n_periods = 3, 26
    block = {
        "periods": PERIODS[:n_periods],
        "nCells": n_cells,
        "ids": [f"88{i:014x}" for i in range(n_cells)],
        "lon": [-81.5 + 0.01 * i for i in range(n_cells)],
        "lat": [25.9 + 0.01 * i for i in range(n_cells)],
        "areaKm2": [0.5] * n_cells,
        "index": {"ndvi": [[0.4 - 0.01 * j for j in range(n_cells)]
                           for _ in range(n_periods)]},
        "observed": [[True] * n_cells for _ in range(n_periods)],
        "validFraction": [[0.9] * n_cells for _ in range(n_periods)],
        "nObs": [[7] * n_cells for _ in range(n_periods)],
        "_indices": ["ndvi"],
        "_nVerts": [6] * n_cells,
        "_geomXY": [c for i in range(n_cells) for c in
                    (-81.5 + 0.01 * i, 25.9 + 0.01 * i,
                     -81.5 + 0.01 * i, 25.91 + 0.01 * i,
                     -81.51 + 0.01 * i, 25.91 + 0.01 * i,
                     -81.5 + 0.01 * i, 25.9 + 0.01 * i,
                     -81.49 + 0.01 * i, 25.91 + 0.01 * i,
                     -81.5 + 0.01 * i, 25.9 + 0.01 * i)],
    }
    if with_band:
        block["band"] = {"red": [[0.1 + 0.01 * j for j in range(n_cells)]
                                 for _ in range(n_periods)]}
        block["_bands"] = ["red"]
    ndvi = np.array(block["index"]["ndvi"])
    block["cell"], block["cellraw"] = analyse(
        block["periods"], {"ndvi": ndvi},
        np.array(block["observed"], dtype=bool),
    )
    return block


def test_analyse_names_its_fields():
    cell, cellraw = _toy_block()["cell"], _toy_block()["cellraw"]
    for spec in SPECS:
        assert f"{spec['key']}.ndvi" in cell, spec["key"]
    assert list(cellraw) == ["anomp.ndvi"]
    assert cell["trend.ndvi"].shape == (3,)


def test_cell_fields_round_trip():
    block = _toy_block()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "toy.bin")
        info = write_block(block, path, gz=True)
        desc, raw = read_block(info["path"])

        assert desc["bands"] == [], "the pilot block should carry no bands"
        assert "trend.ndvi" in desc["cells"]
        assert "anomp.ndvi" in desc["cellraws"]

        f = {x["name"]: x for x in desc["fields"]}
        q = raw["cell.trend.ndvi"].astype(float)
        got = np.where(q == MISSING, np.nan, f["cell.trend.ndvi"]["min"] +
                       q * f["cell.trend.ndvi"]["scale"])
        assert np.allclose(got, block["cell"]["trend.ndvi"], atol=1e-5)

        # raw integer fields keep their sentinel exactly
        assert raw["cellraw.anomp.ndvi"].tolist() == [
            int(v) if v >= 0 else MISSING for v in block["cellraw"]["anomp.ndvi"]
        ]


def test_band_group_is_still_optional():
    block = _toy_block(with_band=True)
    with tempfile.TemporaryDirectory() as tmp:
        info = write_block(block, os.path.join(tmp, "toy.bin"), gz=True)
        desc, raw = read_block(info["path"])
        assert desc["bands"] == ["red"], "a regional block must keep its bands"
        assert "band.red" in raw
        assert "cell.trend.ndvi" in raw


def main():
    check("analysis: trend and seasonal swing are recovered",
          test_trend_and_swing_recovered)
    check("analysis: a dry-season peak is January", test_dry_peak_is_january)
    check("analysis: a planted spike is the largest anomaly",
          test_planted_spike_is_the_largest_anomaly)
    check("analysis: gaps do not bias the fit", test_gaps_do_not_bias_the_fit)
    check("analysis: a short record is not fitted",
          test_a_short_record_is_not_fitted)
    check("analysis: significance separates signal from noise",
          test_trend_significance_separates_signal_from_noise)
    check("analysis: flat noise has low R2", test_flat_noise_has_low_r2)

    check("stability: churn is the mean consecutive step",
          test_churn_is_the_mean_consecutive_step)
    check("stability: churn pairs across unobserved composites",
          test_churn_pairs_across_gaps)
    check("stability: angle ignores brightness, not shape",
          test_angle_ignores_brightness_but_not_shape)
    check("stability: dark spectra are skipped for angle",
          test_dark_spectra_are_skipped_for_angle)

    check("binary: assessment fields are named", test_analyse_names_its_fields)
    check("binary: cell fields round-trip", test_cell_fields_round_trip)
    check("binary: the band group is still optional",
          test_band_group_is_still_optional)

    print()
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
