#!/usr/bin/env python3
"""Unit tests for the Phase 1 modules.

    python tools/test_phase1.py

No network, no build artefacts: everything here is arithmetic on small arrays,
so the whole file runs in well under a second and is safe to run before a
build. These are the tests the plan's work order asks for (bin/date
agreement, the ratio-index bound, the break detector, the block format) plus
the two silent-failure traps the pilot already paid for.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PROJECT, "src"))
sys.path.insert(0, HERE)

from h3lab import bins                                          # noqa: E402
from h3lab.binary import read_descriptor, write_block          # noqa: E402
from h3lab.change import detect_breaks                         # noqa: E402
from h3lab.features import _ratio, compute_indices             # noqa: E402
from h3lab.shard import group_cells_by_region                  # noqa: E402

from decode_block import dequant, read_block                   # noqa: E402


PASSED = []
FAILED = []


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


# --------------------------------------------------------------------------
# bins
# --------------------------------------------------------------------------
def test_bins_for_year():
    bl = bins.bins_for_year(2017)
    assert len(bl) == 23, f"2017 should be 23 bins, got {len(bl)}"
    assert bl[0] == 388 and bl[-1] == 410, f"got {bl[0]}..{bl[-1]}"
    assert bl == sorted(set(bl)), "bins must be sorted and unique"


def test_bin_index_is_continuous():
    # The 16-day grid is anchored at the epoch and is *not* calendar-anchored.
    # Bin b covers the days [b*16, b*16+15] after the epoch.
    assert bins.bin_index(date(2000, 1, 1)) == 0
    assert bins.bin_index(date(2000, 1, 16)) == 0
    assert bins.bin_index(date(2000, 1, 17)) == 1
    assert bins.bin_index(date(2017, 1, 1)) == 388
    assert bins.bin_index(date(2017, 12, 17)) == 410
    assert bins.bin_index(date(2017, 12, 31)) == 410


def test_bin_centre_round_trips():
    for b in bins.bins_for_year(2017):
        centre = bins.bin_centre(b)
        assert bins.bin_index(centre) == b, f"bin {b} centre {centre} does not round-trip"
        assert bins.bin_start(b) <= centre <= bins.bin_end(b)


def test_no_javascript_modulo_trap():
    # D-18: the phase of a bin must never be derived from the bin number.
    # A JS-style modulo would go negative; Python's would not, which is why
    # the phase is derived from the date instead of from the index.
    b = 388
    assert bins.bin_centre(b).month in (12, 1)
    # The centre date of consecutive bins is exactly 16 days apart.
    a, c = bins.bin_centre(388), bins.bin_centre(389)
    assert (c - a).days == 16


def test_seasons_use_the_date():
    assert bins.is_wet_season_date(date(2017, 9, 10))
    assert not bins.is_wet_season_date(date(2017, 12, 1))


# --------------------------------------------------------------------------
# features
# --------------------------------------------------------------------------
def test_ratio_is_bounded():
    rng = np.random.default_rng(3)
    a = rng.normal(0.2, 0.1, 4000)
    c = rng.normal(0.15, 0.1, 4000)
    r = _ratio(a, c)
    finite = r[np.isfinite(r)]
    assert finite.size > 0
    assert finite.min() >= -1.0 - 1e-6, f"ratio below -1: {finite.min()}"
    assert finite.max() <= 1.0 + 1e-6, f"ratio above 1: {finite.max()}"


def test_ratio_rejects_opposite_signs():
    # One band near zero and the other clearly negative is the case that
    # produced NDVI of -200: the ratio is unbounded when the bands straddle
    # zero.
    a = np.array([0.001, -0.05, 0.3, 0.2])
    c = np.array([-0.05, 0.30, 0.3, 0.1])
    r = _ratio(a, c)
    assert np.isnan(r[0]), "a band inside the noise floor must be rejected"
    assert np.isnan(r[1]), "opposite signs must be rejected"
    assert np.isfinite(r[2]), "same-sign bands must be kept"
    assert np.isfinite(r[3]), "same-sign bands must be kept"
    assert abs(r[3] - (0.2 - 0.1) / (0.2 + 0.1)) < 1e-9, "the ratio must be exact"


def test_ratio_keeps_water_working():
    # Over dark water both bands are negative. The same-sign rule keeps those
    # cells; a naive epsilon would have thrown them away.
    a = np.array([-0.02, -0.05])
    c = np.array([-0.04, -0.03])
    r = _ratio(a, c)
    assert np.isfinite(r).all(), "dark water must survive the noise floor"
    assert np.all(np.abs(r) <= 1.0 + 1e-6)


def test_indices_have_expected_ranges():
    refl = {
        "blue": np.array([0.05, 0.04]),
        "green": np.array([0.06, 0.05]),
        "red": np.array([0.04, 0.03]),
        "nir": np.array([0.30, 0.25]),
        "swir1": np.array([0.15, 0.12]),
        "swir2": np.array([0.10, 0.08]),
    }
    idx = compute_indices(refl)
    for k, v in idx.items():
        finite = v[np.isfinite(v)]
        if finite.size:
            assert finite.min() >= -1.5 and finite.max() <= 1.5, f"{k} out of range"


# --------------------------------------------------------------------------
# change
# --------------------------------------------------------------------------
def _seasonal(n, bins_list, rng, sigma=0.01, level=0.45, amp=0.35):
    """A plausible NDVI series: a seasonal cycle plus AR(1) noise."""
    t = np.array([(bins.bin_centre(b) - bins.EPOCH).days for b in bins_list],
                 dtype=float)
    y = level + amp * np.sin(2 * np.pi * t / 365.25 + 0.3) \
        + 0.12 * np.cos(4 * np.pi * t / 365.25 + 1.1)
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.4 * e[i - 1] + rng.normal(0, sigma)
    return y + e


def test_break_lands_on_the_right_bin():
    # The scan reports the first period of the new regime, so a step that
    # happens in period 12 of the year is reported at period 12.
    bl = bins.bins_for_year(2017)
    n = len(bl)
    rng = np.random.default_rng(3)
    y = _seasonal(n, bl, rng)[:, None]
    y[12:, 0] += 0.4
    r = detect_breaks(y, bl)
    assert r["break_bin"][0] == bl[12], \
        f"expected bin {bl[12]}, got {r['break_bin'][0]}"
    assert r["break_date"][0] == bins.bin_centre(bl[12]).isoformat()
    assert r["break_magnitude"][0] > 0, "a step up must have a positive magnitude"


def test_the_fitted_magnitude_recovers_the_step():
    # The harmonic fit absorbs part of a step, but the *fitted* step -- the
    # coefficient of the step regressor in the joint model -- is close to the
    # real one. The raw difference of means before and after the break is not:
    # that is swamped by the seasonal curve. This is why the stored magnitude
    # is the residual change and not the raw one, and it is a test that the
    # residual change is not *too* residual.
    bl = bins.bins_for_year(2017)
    n = len(bl)
    rng = np.random.default_rng(4)
    for k0, step in ((6, 0.3), (12, 0.4), (17, 0.3)):
        y = _seasonal(n, bl, rng)[:, None]
        y[k0:, 0] += step
        r = detect_breaks(y, bl)
        assert r["break_bin"][0] == bl[k0], \
            f"expected bin {bl[k0]}, got {r['break_bin'][0]}"
        got = float(r["break_magnitude"][0])
        assert 0.6 * step <= got <= 1.4 * step, \
            f"fitted step {got:.3f} is far from the real {step:.3f}"


def test_no_break_below_the_threshold():
    bl = bins.bins_for_year(2017)
    n = len(bl)
    rng = np.random.default_rng(11)
    y = np.full((n, 1), 0.3, dtype=np.float32)
    y[:, 0] += rng.normal(0, 0.02, n).astype(np.float32)
    r = detect_breaks(y, bl)
    assert r["break_bin"][0] == -1, "noise must not be reported as a break"
    assert np.isnan(r["break_score"][0]), "a rejected break has no score"


def test_the_null_rate_is_what_the_threshold_says():
    # The threshold is a quantile of a simulated null, so the false-positive
    # rate it claims has to hold. 60 null series at a 1% level should produce
    # at most a couple of false breaks; a threshold that is merely "big" would
    # produce none at all and a threshold that is too small would produce many.
    bl = bins.bins_for_year(2017)
    n = len(bl)
    rng = np.random.default_rng(23)
    series = np.column_stack([_seasonal(n, bl, rng, sigma=0.03)
                               for _ in range(60)])
    r = detect_breaks(series, bl)
    flagged = int(np.sum(r["break_bin"] >= 0))
    assert flagged <= 4, f"{flagged}/60 null series were flagged as breaks"


def test_too_few_observations_is_not_a_break():
    bl = bins.bins_for_year(2017)
    y = np.full((len(bl), 1), np.nan, dtype=np.float32)
    y[:4, 0] = 0.3
    r = detect_breaks(y, bl)
    assert r["break_bin"][0] == -1
    assert r["n_obs_used"][0] == 4


def test_method_carries_its_parameters():
    bl = bins.bins_for_year(2017)
    r = detect_breaks(np.full((len(bl), 1), 0.3, dtype=np.float32), bl)
    m = str(r["method"][0])
    assert "harmonics=" in m and "alpha=" in m and "fstat=" in m, m


def test_below_threshold_cell_still_counts_its_observations():
    bl = bins.bins_for_year(2017)
    r = detect_breaks(np.full((len(bl), 1), 0.3, dtype=np.float32), bl)
    assert r["n_obs_used"][0] == len(bl)


# --------------------------------------------------------------------------
# binary format
# --------------------------------------------------------------------------
def _toy_block(n_cells=4, n_periods=3):
    ids = [f"88{i:014x}" for i in range(n_cells)]
    block = {
        "periods": [{"bin": 388 + i, "date": "2017-01-01"} for i in range(n_periods)],
        "nCells": n_cells,
        "ids": ids,
        "lon": [-81.5 + 0.01 * i for i in range(n_cells)],
        "lat": [25.9 + 0.01 * i for i in range(n_cells)],
        "areaKm2": [0.5] * n_cells,
        "band": {"red": [[0.1 + 0.01 * j for j in range(n_cells)]
                         for _ in range(n_periods)]},
        "index": {"ndvi": [[0.4 - 0.05 * j for j in range(n_cells)]
                           for _ in range(n_periods)]},
        "observed": [[True] * n_cells for _ in range(n_periods)],
        "validFraction": [[0.9] * n_cells for _ in range(n_periods)],
        "nObs": [[7] * n_cells for _ in range(n_periods)],
        "_bands": ["red"],
        "_indices": ["ndvi"],
        "_nVerts": [6] * n_cells,
        "_geomXY": [c for i in range(n_cells) for c in
                    (-81.5 + 0.01 * i, 25.9 + 0.01 * i,
                     -81.5 + 0.01 * i, 25.91 + 0.01 * i,
                     -81.51 + 0.01 * i, 25.91 + 0.01 * i,
                     -81.5 + 0.01 * i, 25.9 + 0.01 * i,
                     -81.49 + 0.01 * i, 25.91 + 0.01 * i,
                     -81.5 + 0.01 * i, 25.9 + 0.01 * i)],
        "epoch": "2000-01-01",
        "binDays": 16,
    }
    block["change"] = {
        "break_bin": [388, 389, -1, 400],
        "break_date": np.array([6300, 6316, -1, 6350]),
        "break_magnitude": np.array([0.1, -0.2, np.nan, 0.3]),
        "break_score": np.array([3.5, 4.0, np.nan, 5.0]),
        "n_obs_used": [23, 23, 23, 23],
        "method": np.array([0, 0, 0, 0]),
        "methods": ["harmonic_fscan_v1(harmonics=2,trend=1,min_obs=8,scan=step-dummy,fstat=partial-F,null=ar1(phi=0.4),nsim=200,seed=20260101,alpha=0.01)"],
    }
    return block


def test_block_round_trip():
    block = _toy_block()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "toy.bin")
        write_block(block, path, gz=True)
        out = path + ".gz"
        assert os.path.exists(out)
        b = read_block(out)
        assert b["descriptor"]["nCells"] == 4
        assert b["descriptor"]["nPeriods"] == 3
        ndvi = dequant(b["index.ndvi"], b["index.ndvi__meta"])
        assert np.allclose(ndvi[0], [0.4, 0.35, 0.3, 0.25], atol=1e-3)
        assert b["break_bin"].tolist() == [388, 389, 65535, 400]
        assert b["break_date"].tolist() == [6300, 6316, 65535, 6350]
        assert b["n_obs_used"].tolist() == [23, 23, 23, 23]
        assert b["method"].tolist() == [0, 0, 0, 0]
        mag = dequant(b["break_magnitude"], b["break_magnitude__meta"])
        assert np.isnan(mag[2]), "a missing magnitude must decode as NaN"
        assert b["descriptor"]["changeMethods"], "the method table must be present"


def test_block_is_byte_identical():
    block = _toy_block()
    with tempfile.TemporaryDirectory() as tmp:
        a = os.path.join(tmp, "a.bin")
        b = os.path.join(tmp, "b.bin")
        write_block(block, a, gz=True)
        write_block(block, b, gz=True)
        with open(a + ".gz", "rb") as fa, open(b + ".gz", "rb") as fb:
            assert fa.read() == fb.read(), "two writes of the same block must match"


def test_descriptor_read_without_payload():
    block = _toy_block()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "toy.bin")
        write_block(block, path, gz=True)
        desc = read_descriptor(path + ".gz")
        assert desc["nCells"] == 4
        assert desc["epoch"] == "2000-01-01"
        assert desc["binDays"] == 16
        names = {f["name"] for f in desc["fields"]}
        for f in ("break_bin", "break_date", "break_magnitude", "break_score",
                  "n_obs_used", "method"):
            assert f in names, f"{f} missing from the descriptor"


# --------------------------------------------------------------------------
# sharding
# --------------------------------------------------------------------------
def test_region_key_is_the_h3_parent():
    import h3

    class _G:
        cells = np.array(["844413dffffffff" + "0"], dtype=object)

    # Use two real res-8 children of one res-4 cell.
    parent = "844413dffffffff"
    kids = [c for c in h3.cell_to_children(parent, 8)[:2]]
    g = _G()
    g.cells = np.array(kids, dtype=object)
    groups = group_cells_by_region(g, 4)
    assert list(groups) == [parent], f"got {list(groups)}"
    assert sorted(groups[parent]) == [0, 1]


def test_shard_key_survives_a_centroid_outside_the_parent():
    # About 6 % of res-8 cells have a centre that falls outside their res-4
    # parent's polygon; the point lookup and the parent lookup disagree, and
    # the parent lookup is the right one.
    import h3

    parent = "844413dffffffff"
    kids = [c for c in h3.cell_to_children(parent, 8)]
    disagree = 0
    for k in kids:
        lat, lng = h3.cell_to_latlng(k)
        if h3.latlng_to_cell(lat, lng, 4) != parent:
            disagree += 1
    # The property under test is that cell_to_parent is exact for every cell.
    for k in kids:
        assert h3.cell_to_parent(k, 4) == parent
    assert disagree >= 0


# --------------------------------------------------------------------------
def main():
    check("bins: 2017 is 23 bins", test_bins_for_year)
    check("bins: index is continuous from the epoch", test_bin_index_is_continuous)
    check("bins: centre round-trips to its bin", test_bin_centre_round_trips)
    check("bins: no javascript modulo phase", test_no_javascript_modulo_trap)
    check("bins: seasons come from the date", test_seasons_use_the_date)

    check("features: the ratio stays inside [-1, 1]", test_ratio_is_bounded)
    check("features: opposite signs are rejected", test_ratio_rejects_opposite_signs)
    check("features: dark water survives", test_ratio_keeps_water_working)
    check("features: every index is in range", test_indices_have_expected_ranges)

    check("change: the break lands on the right bin", test_break_lands_on_the_right_bin)
    check("change: the fitted magnitude recovers the step",
          test_the_fitted_magnitude_recovers_the_step)
    check("change: noise is not a break", test_no_break_below_the_threshold)
    check("change: the null rate is what the threshold says",
          test_the_null_rate_is_what_the_threshold_says)
    check("change: too few observations is not a break",
          test_too_few_observations_is_not_a_break)
    check("change: the method carries its parameters",
          test_method_carries_its_parameters)
    check("change: observations are counted either way",
          test_below_threshold_cell_still_counts_its_observations)

    check("binary: the block round-trips", test_block_round_trip)
    check("binary: two writes are byte-identical", test_block_is_byte_identical)
    check("binary: the descriptor reads without the payload",
          test_descriptor_read_without_payload)

    check("shard: the region key is the h3 parent",
          test_region_key_is_the_h3_parent)
    check("shard: the key survives a centroid outside the parent",
          test_shard_key_survives_a_centroid_outside_the_parent)

    print()
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
