#!/usr/bin/env python3
"""Phase 1 exit-criteria verification.

    python tools/verify_phase1.py
    python tools/verify_phase1.py --skip-rebuild

Runs the five tests listed in `docs/PHASE1.md` §11 against the artefacts in
`data/regional/`:

    1  byte-identical rebuild      two shard+index passes produce the same bytes
    2  geometry registration       centroids match an independent h3 computation;
                                   res-8 nests inside res-7; every res-8 cell
                                   lands in the shard its res-4 parent names
    3  seasonality                 the D-18 acceptance check: the regional's
                                   wet/dry NDVI amplitude against the pilot's
                                   at comparable cells
    4  cold load < 3 s             headless mirror of the viewer's load path
    5  change schema               every res-8 shard descriptor carries the
                                   six change fields and the method table

Exit status is 0 when every check passes and 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import warnings

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PROJECT, "src"))

from decode_block import dequant, read_block          # noqa: E402
from h3lab.binary import read_descriptor               # noqa: E402


PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


class Results:
    def __init__(self):
        self.rows = []

    def add(self, name, status, detail=""):
        self.rows.append((name, status, detail))
        print(f"[{status}] {name}" + (f"\n       {detail}" if detail else ""),
              flush=True)

    @property
    def failed(self):
        return any(s == FAIL for _, s, _ in self.rows)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def digest_dir(path):
    """Every file in a directory, by content."""
    out = {}
    for name in sorted(os.listdir(path)):
        p = os.path.join(path, name)
        if os.path.isfile(p):
            out[name] = md5(p)
    return out


# --------------------------------------------------------------------------
# 1. byte-identical rebuild
# --------------------------------------------------------------------------
def check_reproducible(res, out_dir, cache_dir, year, resolutions, bins_list):
    """Rebuild the shards twice into scratch directories and compare bytes."""
    from h3lab.regional import RegionalBuild

    if not os.path.isdir(cache_dir):
        res.add("1 byte-identical rebuild", SKIP, f"no composite cache at {cache_dir}")
        return

    digests = []
    for _ in range(2):
        tmp = tempfile.mkdtemp(prefix="h3lab-verify-")
        try:
            build = RegionalBuild(year=year, resolutions=resolutions,
                                  bins_list=bins_list, cache_dir=cache_dir,
                                  out_dir=tmp, log=lambda m: None)
            build.run(stages=["shard", "index"])
            digests.append(digest_dir(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    a, b = digests
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    differing = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    if only_a or only_b or differing:
        res.add("1 byte-identical rebuild", FAIL,
                f"{len(differing)} differing, {len(only_a)} only-in-first, "
                f"{len(only_b)} only-in-second; first few: "
                f"{differing[:3] + only_a[:3] + only_b[:3]}")
        return
    res.add("1 byte-identical rebuild", PASS,
            f"{len(a)} files byte-identical across two rebuilds")


# --------------------------------------------------------------------------
# 2. geometry registration
# --------------------------------------------------------------------------
def _centroid_round_trips(cell, lon, lat):
    """True when H3's own point lookup maps the stored centroid back to `cell`.

    This is the registration test that means something. It is *not* the same
    as "the centroid lies inside the res-4 parent": H3 builds a child's
    geometry from the parent's, and for about 6 % of res-8 cells the child's
    centre falls a few hundred metres outside the parent's polygon, so
    `latLngToCell(centre, 4)` returns the *neighbouring* res-4 cell even
    though `cellToParent` names the right one. The shard key follows
    `cellToParent`, which is exact.
    """
    import h3

    true_lat, true_lng = h3.cell_to_latlng(cell)
    if abs(true_lng - lon) > 1e-6 or abs(true_lat - lat) > 1e-6:
        return False
    return h3.latlng_to_cell(lat, lon, 8) == cell


def check_geometry(res, out_dir, manifest):
    import h3
    from shapely.geometry import Polygon, box

    bbox = manifest["bbox"]
    minx, miny, maxx, maxy = bbox
    overview = manifest.get("overview")
    if not overview:
        res.add("2 geometry registration", FAIL, "manifest has no overview")
        return

    ov_path = os.path.join(out_dir, overview["file"])
    desc = read_descriptor(ov_path)
    ids7 = desc["ids"]
    lon7 = np.asarray(desc["lon"], dtype=float)
    lat7 = np.asarray(desc["lat"], dtype=float)

    # 2a: the stored centroid equals an independent h3 computation.
    worst = 0.0
    for i, cell in enumerate(ids7):
        lat, lng = h3.cell_to_latlng(cell)
        worst = max(worst, abs(lng - lon7[i]), abs(lat - lat7[i]))
    if worst > 1e-6:
        res.add("2 geometry registration", FAIL,
                f"res-7 centroids differ from h3.cell_to_latlng by up to {worst:.2e} deg")
        return

    # 2b: every res-7 centroid is inside the bbox.
    outside = int(((lon7 < minx) | (lon7 > maxx) | (lat7 < miny) | (lat7 > maxy)).sum())
    if outside:
        res.add("2 geometry registration", FAIL,
                f"{outside} res-7 centroids fall outside the bbox")
        return

    # 2c: every res-8 cell lands in the shard named after its res-4 parent,
    #     and its stored centroid round-trips through H3's point lookup.
    id7 = set(ids7)
    res8 = {}
    bad_centroid = []
    for shard in manifest["regions"]:
        d = read_descriptor(os.path.join(out_dir, shard["file"]))
        slon = np.asarray(d["lon"], dtype=float)
        slat = np.asarray(d["lat"], dtype=float)
        for i, cell in enumerate(d["ids"]):
            res8[cell] = shard["region"]
            if not _centroid_round_trips(cell, slon[i], slat[i]):
                bad_centroid.append(cell)

    bad_region = sum(1 for cell, region in res8.items()
                     if h3.cell_to_parent(cell, 4) != region)
    if bad_centroid or bad_region:
        res.add("2 geometry registration", FAIL,
                f"{len(bad_centroid)} res-8 centroids do not round-trip, "
                f"{bad_region} cells in the wrong region shard")
        return

    # 2d: the res-7 overview nests the res-8 grid. The two grids are built
    #     independently with the same "whole hexagon inside the bbox" rule, so
    #     a res-8 cell near the edge can have a res-7 parent that the rule
    #     clips. Those parents must be edge cells, and they must be few.
    clipped = []
    for cell in res8:
        p7 = h3.cell_to_parent(cell, 7)
        if p7 not in id7 and p7 not in clipped:
            clipped.append(p7)
    bbox_poly = box(*bbox)
    not_contained = 0
    for p7 in clipped:
        ring = h3.cell_to_boundary(p7)
        poly = Polygon([(lng, lat) for lat, lng in ring])
        if bbox_poly.contains(poly):
            not_contained += 1
    if not_contained:
        res.add("2 geometry registration", FAIL,
                f"{not_contained} res-7 parents are inside the bbox but missing "
                f"from the overview")
        return
    frac_clipped = len(clipped) / max(len(id7), 1)
    if frac_clipped > 0.05:
        res.add("2 geometry registration", FAIL,
                f"{len(clipped)} res-7 parents of res-8 cells are missing from "
                f"the overview ({100 * frac_clipped:.1f}% of the overview)")
        return

    res.add("2 geometry registration", PASS,
            f"{len(ids7):,} res-7 and {len(res8):,} res-8 cells; centroids match "
            f"h3 to {worst:.1e} deg and round-trip; res-8 nests in res-7 apart "
            f"from {len(clipped)} edge parents clipped by the bbox "
            f"({100 * frac_clipped:.1f}%)")


# --------------------------------------------------------------------------
# 3. seasonality -- the D-18 acceptance check
# --------------------------------------------------------------------------
#
# The plan does not ask "is wet NDVI above dry NDVI". It asks for a
# comparison (PHASE1 §11, row 3):
#
#     regional 2017 NDVI wet/dry amplitude vs the pilot's at comparable
#     cells (D-18 acceptance check)
#
# and D-18 rule 4 states the failure mode the check exists to catch:
# "If it is damped, compositing is smoothing the cycle." So the test is
# whether the regional build reproduces the pilot's seasonal separation at
# the cells the two products share, on the pilot's own season windows.
#
# The windows have to be matched before anything can be compared. The pilot
# composites per season: wet = 1 May - 31 Oct, dry = 1 Nov - 31 Dec
# (config.SEASONS, and `ingest.find_scenes` uses Nov-Dec of the same year).
# The regional build's bins are 16 days long, so a season is a set of bins,
# selected here by the pilot's calendar.
#
# The comparable cells are the pilot's own res-8 Rookery Bay cells, which
# all fall inside the regional archive; the regional side is read from the
# region shard that contains them.


# The pilot's season windows, in months. Kept as literals rather than
# imported so that the check stays pinned to the *pilot's* calendar even if
# the pilot's configuration changes later.
PILOT_WET_MONTHS = (5, 6, 7, 8, 9, 10)
PILOT_DRY_MONTHS = (11, 12)


def _season_masks(periods):
    """(wet, dry) masks over a period list, using the pilot's calendar.

    The pilot's periods carry `year` and `season`; the regional's carry a
    `date`. Both are mapped onto months so the two products are compared
    over the same part of the year.
    """
    wet = np.zeros(len(periods), dtype=bool)
    dry = np.zeros(len(periods), dtype=bool)
    for i, p in enumerate(periods):
        if "season" in p:
            wet[i] = p["season"] == "wet"
            dry[i] = p["season"] == "dry"
        else:
            month = int(p["date"][5:7])
            wet[i] = month in PILOT_WET_MONTHS
            dry[i] = month in PILOT_DRY_MONTHS
    return wet, dry


def _season_mean(series, mask):
    """Mean over the periods in `mask`, ignoring periods where the cell was
    not observed.

    A cell that was never observed in the selected periods gives an all-NaN
    slice, which numpy warns about; the warning is noise here because the
    caller filters those cells out explicitly.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with np.errstate(invalid="ignore"):
            return np.nanmean(np.where(mask[:, None], series, np.nan), axis=0)


def check_seasonality(res, out_dir, manifest, pilot_path=None):
    """D-18: the regional's seasonal separation must match the pilot's."""
    if pilot_path is None:
        pilot_path = os.path.join(PROJECT, "data", "rookery_bay_h3_res8.bin.gz")
    if not os.path.exists(pilot_path):
        res.add("3 seasonality", SKIP,
                f"pilot bundle {pilot_path} not found; the D-18 check needs it")
        return

    # -- the pilot ----------------------------------------------------------
    pblock = read_block(pilot_path)
    pdesc = pblock["descriptor"]
    pndvi = dequant(pblock["index.ndvi"], pblock["index.ndvi__meta"])
    pwet, pdry = _season_masks(pdesc["periods"])
    year = 2017
    p2017 = np.array([p.get("year") == year for p in pdesc["periods"]])
    if not p2017.any():
        res.add("3 seasonality", SKIP,
                f"the pilot has no {year} periods")
        return
    p_wet = _season_mean(pndvi[p2017], pwet[p2017])
    p_dry = _season_mean(pndvi[p2017], pdry[p2017])

    # -- the regional side: the region shard holding the pilot's cells -----
    pilot_cells = set(pdesc["ids"])
    holder = None
    for shard in manifest.get("regions") or []:
        d = read_descriptor(os.path.join(out_dir, shard["file"]))
        if pilot_cells & set(d["ids"]):
            holder = (shard, d)
            break
    if holder is None:
        res.add("3 seasonality", FAIL,
                "no region shard contains the pilot's cells")
        return
    shard, rdesc = holder
    rblock = read_block(os.path.join(out_dir, shard["file"]))
    rndvi = dequant(rblock["index.ndvi"], rblock["index.ndvi__meta"])
    rwet, rdry = _season_masks(rdesc["periods"])
    if not rwet.any() or not rdry.any():
        res.add("3 seasonality", FAIL,
                f"the regional periods do not split into wet and dry: "
                f"{int(rwet.sum())} wet, {int(rdry.sum())} dry")
        return
    r_wet = _season_mean(rndvi, rwet)
    r_dry = _season_mean(rndvi, rdry)

    # -- the shared cells --------------------------------------------------
    pidx = {c: i for i, c in enumerate(pdesc["ids"])}
    ridx = {c: i for i, c in enumerate(rdesc["ids"])}
    rows = []
    for cell in sorted(pidx):
        if cell not in ridx:
            continue
        i, j = pidx[cell], ridx[cell]
        vals = (p_wet[i], p_dry[i], r_wet[j], r_dry[j])
        if not all(np.isfinite(v) for v in vals):
            continue
        p_amp = float(np.nanmax(pndvi[:, i]) - np.nanmin(pndvi[:, i]))
        r_amp = float(np.nanmax(rndvi[:, j]) - np.nanmin(rndvi[:, j]))
        rows.append(vals + (p_amp, r_amp))
    if len(rows) < 50:
        res.add("3 seasonality", FAIL,
                f"only {len(rows)} cells are observed by both products")
        return
    arr = np.asarray(rows)
    p_wv, p_dv, r_wv, r_dv, p_amp, r_amp = arr.T

    def med(a):
        return float(np.median(a))

    wet_ratio = med(r_wv) / med(p_wv)
    dry_ratio = med(r_dv) / med(p_dv)
    corr = float(np.corrcoef(p_wv - p_dv, r_wv - r_dv)[0, 1])
    amp_ratio = med(r_amp) / med(p_amp)
    amp_med = med(r_amp)

    detail = (
        f"{len(rows):,} cells shared with the pilot's res-8 Rookery Bay grid "
        f"(shard {shard['region']}); "
        f"wet NDVI median pilot {med(p_wv):+.3f} vs regional {med(r_wv):+.3f} "
        f"(ratio {wet_ratio:.3f}); "
        f"dry NDVI median pilot {med(p_dv):+.3f} vs regional {med(r_dv):+.3f} "
        f"(ratio {dry_ratio:.3f}); "
        f"per-cell wet-dry correlation {corr:+.3f}; "
        f"seasonal amplitude median pilot {med(p_amp):+.3f} vs regional "
        f"{amp_med:+.3f} (ratio {amp_ratio:.3f})")

    # The D-18 rule: the regional's seasonal separation must match the
    # pilot's -- neither damped (compositing smoothing the cycle away) nor
    # inflated. 15 % on the seasonal means is the tolerance; the amplitude
    # is only checked for damping, because a single year of noisy 16-day
    # bins reaches further from its own mean than a 13-year record does.
    problems = []
    if not 0.85 <= wet_ratio <= 1.15:
        problems.append(f"wet NDVI ratio {wet_ratio:.3f} outside 0.85-1.15")
    if not 0.85 <= dry_ratio <= 1.15:
        problems.append(f"dry NDVI ratio {dry_ratio:.3f} outside 0.85-1.15")
    if corr < 0.5:
        problems.append(f"per-cell wet-dry correlation {corr:+.3f} below 0.5")
    if amp_ratio < 0.8:
        problems.append(f"seasonal amplitude ratio {amp_ratio:.3f} below 0.8 "
                        f"(the cycle is damped)")
    if amp_med < 0.10:
        problems.append(f"median seasonal amplitude {amp_med:.3f} below 0.10 "
                        f"(no visible cycle)")
    if problems:
        res.add("3 seasonality", FAIL, detail + "\n       - "
                + "; ".join(problems))
    else:
        res.add("3 seasonality", PASS, detail)


# --------------------------------------------------------------------------
# 4. cold load
# --------------------------------------------------------------------------
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_cold_load(res, out_dir, budget_ms=3000):
    probe = os.path.join(HERE, "cold_load_probe.mjs")
    if not os.path.exists(probe):
        res.add("4 cold load < 3 s", SKIP, "cold_load_probe.mjs not found")
        return
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        res.add("4 cold load < 3 s", SKIP, "node is not installed")
        return

    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=PROJECT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # Wait for the server to accept connections.
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            res.add("4 cold load < 3 s", FAIL, "the test server did not start")
            return

        rel = os.path.relpath(os.path.join(PROJECT, out_dir), PROJECT)
        proc = subprocess.run(
            [node, probe, f"http://127.0.0.1:{port}", rel],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            res.add("4 cold load < 3 s", FAIL,
                    f"probe failed: {proc.stdout.strip()} {proc.stderr.strip()}")
            return
        data = json.loads(proc.stdout)
    finally:
        server.terminate()
        server.wait(timeout=10)

    total = int(data["totalMs"])
    detail = (f"{total} ms total "
              f"(manifest {data['steps']['manifest']['ms']} + "
              f"download {data['steps']['download']['ms']} + "
              f"inflate {data['steps']['inflate']['ms']} + "
              f"parse {data['steps']['parse']['ms']}), "
              f"{data['servedMB']} MB served / {data['rawMB']} MB raw, "
              f"{data['nCells']:,} cells x {data['nPeriods']} periods")
    if total < budget_ms:
        res.add("4 cold load < 3 s", PASS, detail)
    else:
        res.add("4 cold load < 3 s", FAIL, detail)


# --------------------------------------------------------------------------
# 5. change schema
# --------------------------------------------------------------------------
CHANGE_FIELDS = ("break_bin", "break_date", "break_magnitude", "break_score",
                 "n_obs_used", "method")


def check_change_schema(res, out_dir, manifest):
    regions = manifest.get("regions") or []
    if not regions:
        res.add("5 change schema", FAIL, "manifest has no region shards")
        return

    missing = []
    no_methods = []
    n_breaks = 0
    n_cells = 0
    for shard in regions:
        path = os.path.join(out_dir, shard["file"])
        desc = read_descriptor(path)
        names = {f["name"] for f in desc["fields"]}
        for f in CHANGE_FIELDS:
            if f not in names:
                missing.append((shard["region"], f))
        if not desc.get("changeMethods"):
            no_methods.append(shard["region"])
        bb = None
        for f in desc["fields"]:
            if f["name"] == "break_bin":
                bb = f
        if bb is not None:
            raw = read_block(path)["break_bin"]
            n_breaks += int((raw != 65535).sum())
            n_cells += int(raw.size)

    if missing:
        res.add("5 change schema", FAIL,
                f"{len(missing)} missing fields, first: {missing[:3]}")
        return
    if no_methods:
        res.add("5 change schema", FAIL,
                f"{len(no_methods)} shards have no changeMethods table")
        return
    detail = (f"all {len(regions)} shards carry {'/'.join(CHANGE_FIELDS)} and a "
              f"changeMethods table; {n_breaks:,} of {n_cells:,} cells report a break")
    res.add("5 change schema", PASS, detail)


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default="data/regional")
    ap.add_argument("--cache-dir", default="data/cache_regional")
    ap.add_argument("--year", type=int, default=2017)
    ap.add_argument("--resolutions", default="7,8")
    ap.add_argument("--skip-rebuild", action="store_true",
                    help="skip criterion 1 (it re-exports every shard twice)")
    args = ap.parse_args()

    out_dir = os.path.join(PROJECT, args.out_dir)
    cache_dir = os.path.join(PROJECT, args.cache_dir)
    manifest_path = os.path.join(out_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"no manifest at {manifest_path}; build first")
        return 1

    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    res = Results()
    print(f"verifying {args.out_dir} "
          f"({len(manifest.get('regions') or [])} region shards, "
          f"{len(manifest.get('periods') or [])} periods)\n")

    if args.skip_rebuild:
        res.add("1 byte-identical rebuild", SKIP, "skipped by request")
    else:
        from h3lab import bins as bins_mod

        check_reproducible(res, out_dir, cache_dir, args.year,
                           tuple(int(r) for r in args.resolutions.split(",")),
                           bins_mod.bins_for_year(args.year))

    check_geometry(res, out_dir, manifest)
    check_seasonality(res, out_dir, manifest)
    check_cold_load(res, args.out_dir)
    check_change_schema(res, out_dir, manifest)

    print()
    passed = sum(1 for _, s, _ in res.rows if s == PASS)
    failed = sum(1 for _, s, _ in res.rows if s == FAIL)
    skipped = sum(1 for _, s, _ in res.rows if s == SKIP)
    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
