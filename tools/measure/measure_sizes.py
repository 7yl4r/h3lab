"""Measured sizes, for DESIGN.md §5 and §7.1.

Everything here is a measurement of what is actually on disk, not an estimate.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REGIONAL = os.path.join(ROOT, "data", "regional")
MOSAIC = os.path.join(ROOT, "data", "mosaic_cache")
CACHE = os.path.join(ROOT, "data", "cache_regional")


def dirsize(path):
    total = 0
    for name in os.listdir(path):
        p = os.path.join(path, name)
        if os.path.isfile(p):
            total += os.path.getsize(p)
    return total


def main():
    manifest = json.load(open(os.path.join(REGIONAL, "manifest.json"), encoding="utf-8"))

    print("== res 7 overview")
    ov = manifest["overview"]
    print(f"   file {ov['file']}: {ov['bytes'] / 1e6:.2f} MB served, "
          f"{ov['nCells']:,} cells x {ov['nPeriods']} periods")

    print("== res 8 region shards")
    shards = manifest["regions"]
    cells = [s["nCells"] for s in shards]
    sizes = [s["bytes"] for s in shards]
    print(f"   {len(shards)} shards")
    print(f"   cells/shard: min {min(cells)}, max {max(cells)}, "
          f"mean {sum(cells) / len(cells):.0f}, total {sum(cells):,}")
    print(f"   size/shard: min {min(sizes) / 1e6:.2f} MB, "
          f"max {max(sizes) / 1e6:.2f} MB, "
          f"mean {sum(sizes) / len(sizes) / 1e6:.2f} MB, "
          f"total {sum(sizes) / 1e6:.1f} MB")
    served = os.path.getsize(os.path.join(REGIONAL, ov["file"])) + sum(sizes)
    print(f"   viewable total: {served / 1e6:.1f} MB")

    print("== mosaic cache (2017)")
    if os.path.isdir(MOSAIC):
        per_bin = {}
        for name in sorted(os.listdir(MOSAIC)):
            d = os.path.join(MOSAIC, name)
            if os.path.isdir(d):
                per_bin[name] = dirsize(d)
        tot = sum(per_bin.values())
        print(f"   {len(per_bin)} bins, {tot / 1e9:.2f} GB total, "
              f"mean {tot / max(len(per_bin), 1) / 1e6:.0f} MB/bin, "
              f"min {min(per_bin.values()) / 1e6:.0f} MB, "
              f"max {max(per_bin.values()) / 1e6:.0f} MB")
        print(f"   implied full record (612 bins): "
              f"{tot / max(len(per_bin), 1) * 612 / 1e9:.0f} GB")

    print("== h3 cell cache")
    if os.path.isdir(CACHE):
        npz = [n for n in os.listdir(CACHE) if n.endswith(".npz")]
        tot = sum(os.path.getsize(os.path.join(CACHE, n)) for n in npz)
        print(f"   {len(npz)} npz files, {tot / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
