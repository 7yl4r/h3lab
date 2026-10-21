"""Regional build runner for Phase 1.

    python -m h3lab.run_regional --year 2017 --resolutions 7,8
    python -m h3lab.run_regional --stage change,shard,index

The pipeline itself lives in `h3lab.regional`; this module is the original
entry point and now just forwards to it.
"""

from __future__ import annotations

import argparse

from . import bins
from .regional import STAGES, build_regional


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=2017)
    ap.add_argument("--resolutions", default="7,8")
    ap.add_argument("--bins", default="", help="comma-separated bin numbers")
    ap.add_argument("--stage", default="",
                    help=f"comma-separated stages to run: {','.join(STAGES)}")
    ap.add_argument("--force", action="store_true",
                    help="ignore the on-disk caches")
    ap.add_argument("--no-gzip", action="store_true",
                    help="write uncompressed .bin blocks")
    ap.add_argument("--workers", type=int, default=12,
                    help="concurrent scene reads per bin")
    args = ap.parse_args()

    resolutions = [int(r) for r in args.resolutions.split(",") if r]
    stages = [s for s in args.stage.split(",") if s] or None
    bins_list = None
    if args.bins:
        wanted = {int(x) for x in args.bins.split(",")}
        bins_list = [b for b in bins.bins_for_year(args.year) if b in wanted]

    build_regional(year=args.year, resolutions=resolutions, stages=stages,
                   force=args.force, gz=not args.no_gzip, bins_list=bins_list,
                   workers=args.workers)


if __name__ == "__main__":
    main()
