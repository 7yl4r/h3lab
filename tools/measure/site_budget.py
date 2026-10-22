#!/usr/bin/env python3
"""Size budget for the published site.

    python tools/measure/site_budget.py                 # what the site will hold
    python tools/measure/site_budget.py site            # an assembled site dir
    python tools/measure/site_budget.py site --check    # CI: exit 1 if over

GitHub Pages is a static file host with two numbers that matter (both quoted
from GitHub's own limits page):

* a **published site may be no larger than 1 GB**, and
* Git itself blocks any **single file over 100 MB** (and warns from 50 MB).

The archive the viewer reads is small — 61.6 MiB for 2017 — but it grows
linearly with the record, and one file grows faster than the rest: the res-7
overview is deliberately a single unsharded bundle (D-21), so it carries every
cell for every period and hits the 100 MB single-file block first, at 294 bins
(~12.9 years). The 72 region shards are individually small enough for ~83
years.

`--check` is what the Pages workflow runs, so a build that would silently
exceed either limit fails the deploy instead of publishing a broken site.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# GitHub's published limits, in decimal bytes (GitHub says "1 GB" and
# "100 MB"; the decimal reading is the conservative one).
PAGES_SITE_LIMIT = 1_000_000_000
FILE_LIMIT = 100_000_000
FILE_WARN = 50_000_000
BANDWIDTH_LIMIT = 100_000_000_000     # soft, per month
BINS_PER_YEAR = 365.25 / 16

DEFAULT_REGIONAL = os.path.join("data", "regional")
DATA_DIR = "data"


def human(n: float) -> str:
    if n < 1024:
        return f"{n:,.0f} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        n /= 1024.0
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:,.1f} {unit}"
    return f"{n:,.1f} TiB"


def walk(root: str):
    """(relative path, bytes) for every regular file under root, largest first."""
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                out.append((os.path.relpath(p, root), os.path.getsize(p)))
            except OSError:
                pass
    out.sort(key=lambda r: r[1], reverse=True)
    return out


def regional_cost(regional_dir: str):
    """Per-bin byte cost of the regional archive, split by file role.

    Returns a dict with the totals and the per-bin costs, or None when there
    is no manifest to take the period count from.
    """
    manifest_path = os.path.join(regional_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return None
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        return None
    n_bins = len(manifest.get("periods") or [])
    if not n_bins:
        return None

    total = 0
    overview = 0
    largest_shard = 0
    for name, n in walk(regional_dir):
        total += n
        base = os.path.basename(name)
        if base.startswith("overview"):
            overview = max(overview, n)
        elif base.startswith("region_"):
            largest_shard = max(largest_shard, n)
    return {
        "n_bins": n_bins,
        "total": total,
        "overview": overview,
        "largest_shard": largest_shard,
        "bytes_per_bin": total / n_bins,
        "overview_per_bin": overview / n_bins if overview else 0.0,
        "shard_per_bin": largest_shard / n_bins if largest_shard else 0.0,
    }


def pilot_files():
    """The tracked pilot bundles, which the pilot viewer reads."""
    out = []
    if not os.path.isdir(DATA_DIR):
        return out
    for name in sorted(os.listdir(DATA_DIR)):
        p = os.path.join(DATA_DIR, name)
        if (os.path.isfile(p) and name.startswith("rookery_bay_h3")
                and (name.endswith(".bin.gz") or name.endswith(".json"))):
            out.append((os.path.join(DATA_DIR, name), os.path.getsize(p)))
    return out


def report(label: str, files, cost, check: bool) -> int:
    total = sum(n for _p, n in files)
    biggest = max(files, key=lambda r: r[1]) if files else ("-", 0)

    print(f"site: {label}")
    print(f"  files:   {len(files):,}")
    print(f"  bytes:   {total:,} ({human(total)})")
    print(f"  largest: {biggest[0]}  {biggest[1]:,} B ({human(biggest[1])})")

    print("\nlimits")
    ok = True

    # -- published site size -------------------------------------------------
    line = (f"  published site   {PAGES_SITE_LIMIT:>13,} B   "
            f"used {total / PAGES_SITE_LIMIT:6.1%}")
    if cost:
        extra = (PAGES_SITE_LIMIT - total) / cost["bytes_per_bin"]
        line += (f"   {human(PAGES_SITE_LIMIT - total)} headroom = "
                 f"{extra:,.0f} more bins ({extra / BINS_PER_YEAR:.1f} yr)")
    print(line)
    if total > PAGES_SITE_LIMIT:
        ok = False
        print(f"    OVER by {human(total - PAGES_SITE_LIMIT)}")

    # -- single-file size ----------------------------------------------------
    print(f"  single file      {FILE_LIMIT:>13,} B   "
          f"used {biggest[1] / FILE_LIMIT:6.1%}   "
          f"(largest is {os.path.basename(biggest[0])}, {human(biggest[1])})")
    if biggest[1] > FILE_LIMIT:
        ok = False
        print(f"    OVER by {human(biggest[1] - FILE_LIMIT)}")
    elif biggest[1] > FILE_WARN:
        print(f"    note: files above {human(FILE_WARN)} trigger a Git warning")
    if cost and cost["overview_per_bin"]:
        extra = (FILE_LIMIT - cost["overview"]) / cost["overview_per_bin"]
        print(f"    the overview ({human(cost['overview'])}) has "
              f"{human(FILE_LIMIT - cost['overview'])} headroom "
              f"= {extra:,.0f} more bins ({extra / BINS_PER_YEAR:.1f} yr)")

    # -- what scales ---------------------------------------------------------
    if cost:
        print(f"\n  measured: {human(cost['bytes_per_bin'])} per bin over "
              f"{cost['n_bins']} bins "
              f"(overview {human(cost['overview_per_bin'])}/bin, "
              f"largest shard {human(cost['shard_per_bin'])}/bin)")
        if cost["shard_per_bin"]:
            extra = (FILE_LIMIT - cost["largest_shard"]) / cost["shard_per_bin"]
            print(f"  the overview is the file that breaks first; the largest "
                  f"shard alone reaches {human(FILE_LIMIT)} at "
                  f"{extra:,.0f} bins ({extra / BINS_PER_YEAR:.0f} yr)")

    # A cold load of the regional viewer is the overview; the pilot loads its
    # own bundle for the chosen resolution.
    cold = cost["overview"] if cost and cost["overview"] else biggest[1]
    if cold:
        print(f"  bandwidth        {BANDWIDTH_LIMIT:,} B/mo (soft)   "
              f"~{BANDWIDTH_LIMIT / cold:,.0f} regional cold loads/month at "
              f"{human(cold)} each")

    print()
    print("budget: OK" if ok else "budget: OVER")
    return 0 if (ok or not check) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site", nargs="?", default=None,
                    help="assembled site directory (default: what the site will hold)")
    ap.add_argument("--regional", default=None,
                    help="regional archive directory used for the per-bin cost")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when the site exceeds a limit")
    args = ap.parse_args()

    if args.site:
        region = args.regional
        if region is None:
            candidate = os.path.join(args.site, "data", "regional")
            region = candidate if os.path.isdir(candidate) else DEFAULT_REGIONAL
        if not os.path.isdir(args.site):
            print(f"no such directory: {args.site}", file=sys.stderr)
            return 2
        return report(args.site, walk(args.site), regional_cost(region), args.check)

    # No site given: measure the two things the site would contain, in place.
    region = args.regional or DEFAULT_REGIONAL
    if not os.path.isdir(region):
        print(f"no regional archive at {region}; build it or pass a site dir",
              file=sys.stderr)
        return 2
    files = [(os.path.join(region, p), n) for p, n in walk(region)]
    files += pilot_files()
    return report("(regional archive + tracked pilot bundles)", files,
                  regional_cost(region), args.check)


if __name__ == "__main__":
    sys.exit(main())
