#!/usr/bin/env python3
"""Size budget for the published artefacts.

    python tools/measure/site_budget.py                      # dataset-repo budget
    python tools/measure/site_budget.py --check              # CI: exit 1 if over
    python tools/measure/site_budget.py --site site --check  # a Pages site

There are two kinds of published thing and they have different limits.

**This repository's Pages site** (the Rookery Bay bundles and the viewer code)
is measured with `--site`: GitHub caps a published site at 1 GB and blocks any
single file over 100 MB.

**The regional datasets** are not on this site any more. Each is its own
repository (D-31), so GitHub's per-repository limits apply to one dataset at a
time rather than to the whole archive. `--archive` measures that split from a
build directory and reports, per bin, how close each repository is to the
limits. It is a different answer from the single-repo arithmetic it replaces:
the largest file is no longer 72 shards plus an overview, it is one payload.

Measured from the 2017 South Florida build (2.7 MiB per bin in total):

* the overview repository's payload is 349 KiB per bin, so 100 MB is ~257 bins
  (11.3 years) away -- still the binding limit, and still because the overview
  is one unsharded bundle (D-21);
* the largest region repository's payload is 54 KiB per bin, so 100 MB is
  ~1,781 bins (78 years) away;
* the 1 GB per-repository limit is not reachable by either in any realistic
  record, because a repository now holds one dataset rather than all of them.

`tools/publish_datasets.py` produces the split this measures.
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

DEFAULT_ARCHIVE = os.path.join("data", "regional")
DEFAULT_REGIONAL = DEFAULT_ARCHIVE


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


# --------------------------------------------------------------------------
# mode 1: a Pages site
# --------------------------------------------------------------------------
def site_report(label: str, files, check: bool) -> int:
    total = sum(n for _p, n in files)
    biggest = max(files, key=lambda r: r[1]) if files else ("-", 0)

    print(f"site: {label}")
    print(f"  files:   {len(files):,}")
    print(f"  bytes:   {total:,} ({human(total)})")
    print(f"  largest: {biggest[0]}  {biggest[1]:,} B ({human(biggest[1])})")

    print("\nlimits")
    ok = True
    print(f"  published site   {PAGES_SITE_LIMIT:>13,} B   "
          f"used {total / PAGES_SITE_LIMIT:6.1%}   "
          f"{human(PAGES_SITE_LIMIT - total)} headroom")
    if total > PAGES_SITE_LIMIT:
        ok = False
        print(f"    OVER by {human(total - PAGES_SITE_LIMIT)}")

    print(f"  single file      {FILE_LIMIT:>13,} B   "
          f"used {biggest[1] / FILE_LIMIT:6.1%}   "
          f"(largest is {os.path.basename(biggest[0])}, {human(biggest[1])})")
    if biggest[1] > FILE_LIMIT:
        ok = False
        print(f"    OVER by {human(biggest[1] - FILE_LIMIT)}")
    elif biggest[1] > FILE_WARN:
        print(f"    note: files above {human(FILE_WARN)} trigger a Git warning")

    if biggest[1]:
        print(f"  bandwidth        {BANDWIDTH_LIMIT:,} B/mo (soft)   "
              f"~{BANDWIDTH_LIMIT / biggest[1]:,.0f} loads/month at "
              f"{human(biggest[1])} each")

    print()
    print("budget: OK" if ok else "budget: OVER")
    return 0 if (ok or not check) else 1


# --------------------------------------------------------------------------
# mode 2: the dataset split
# --------------------------------------------------------------------------
def archive_report(archive: str, check: bool) -> int:
    manifest_path = os.path.join(archive, "manifest.json")
    if not os.path.exists(manifest_path):
        print(f"no manifest in {archive}; build the archive first", file=sys.stderr)
        return 2
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"unreadable manifest: {exc}", file=sys.stderr)
        return 2

    n_bins = len(manifest.get("periods") or [])
    overview = manifest.get("overview") or {}
    regions = manifest.get("regions") or []
    if not n_bins or not regions:
        print(f"{manifest_path} has no periods or no regions", file=sys.stderr)
        return 2

    ov_bytes = int(overview.get("bytes") or 0)
    largest = max(regions, key=lambda r: int(r.get("bytes") or 0))
    sh_bytes = int(largest.get("bytes") or 0)
    total = sum(int(r.get("bytes") or 0) for r in regions) + ov_bytes
    n_datasets = 1 + len(regions)

    print(f"archive: {archive}")
    print(f"  bins:     {n_bins}")
    print(f"  build:    {total:,} B over {len(regions) + 1} payloads ({human(total)})")
    print(f"  datasets: {n_datasets} repositories "
          f"(1 overview + {len(regions)} regions)")
    print(f"  overview: {overview.get('file')}  {ov_bytes:,} B "
          f"({human(ov_bytes)}, {human(ov_bytes / n_bins)}/bin)")
    print(f"  largest:  {largest.get('region')}  {sh_bytes:,} B "
          f"({human(sh_bytes)}, {human(sh_bytes / n_bins)}/bin)")

    print("\nper-repository limits")
    ok = True

    ov_extra = (FILE_LIMIT - ov_bytes) / (ov_bytes / n_bins)
    line = f"  single file      {FILE_LIMIT:>13,} B   overview reaches it at "
    line += (f"{ov_extra:,.0f} more bins ({ov_extra / BINS_PER_YEAR:.1f} yr)")
    print(line)
    if ov_bytes > FILE_LIMIT:
        ok = False
        print(f"    OVER: the overview payload is {human(ov_bytes - FILE_LIMIT)} "
              f"past the limit")
    elif ov_bytes > FILE_WARN:
        print(f"    note: the overview payload is above {human(FILE_WARN)} "
              f"(Git warns, does not block)")

    sh_extra = (FILE_LIMIT - sh_bytes) / (sh_bytes / n_bins)
    print(f"                   largest region          reaches it at "
          f"{sh_extra:,.0f} bins ({sh_extra / BINS_PER_YEAR:.0f} yr)")

    # A repository holds one payload plus a descriptor, so the 1 GB limit is
    # measured against the largest payload, not the archive.
    repo_bytes = max(ov_bytes, sh_bytes) + 20_000     # + descriptor/README
    repo_extra = (PAGES_SITE_LIMIT - repo_bytes) / ((ov_bytes or sh_bytes) / n_bins)
    print(f"  one repository   {PAGES_SITE_LIMIT:>13,} B   largest is "
          f"{human(repo_bytes)}; full at {repo_extra:,.0f} bins "
          f"({repo_extra / BINS_PER_YEAR:.0f} yr)")
    if repo_bytes > PAGES_SITE_LIMIT:
        ok = False
        print("    OVER: a single dataset repository exceeds the 1 GB site limit")

    print("\n  the overview is still the file that breaks first: it is one "
          "unsharded bundle\n  carrying every cell for every period (D-21), "
          "while a shard is 72x smaller.")
    print(f"  splitting the overview by year is what buys the next decade; "
          f"it is Phase 2 work (D-30).")
    print(f"  repository count grows with regions, not with years: "
          f"{n_datasets} today, +{len(regions)} per new AOI.")

    print()
    print("budget: OK" if ok else "budget: OVER")
    return 0 if (ok or not check) else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default=None,
                    help="measure an assembled Pages site directory")
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE,
                    help=f"regional build to measure as a dataset split "
                         f"(default {DEFAULT_ARCHIVE})")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when a limit is exceeded")
    args = ap.parse_args()

    if args.site:
        if not os.path.isdir(args.site):
            print(f"no such directory: {args.site}", file=sys.stderr)
            return 2
        return site_report(args.site, walk(args.site), args.check)
    return archive_report(args.archive, args.check)


if __name__ == "__main__":
    sys.exit(main())
