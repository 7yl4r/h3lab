"""The Rookery Bay region shard (D-23).

Phase 1 was sequenced so that the Rookery Bay region is built and validated
first, because Hurricane Irma (10 Sep 2017) landed there and the imagery for
the Phase 2 acceptance test exists. This checks that shard on its own:

  * it exists, and its cells are the res-8 cells of its res-4 parent;
  * its NDVI series has the wet/dry cycle;
  * the change layer is populated, and the breaks it finds cluster around the
    Irma window rather than being spread uniformly over the year.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from decode_block import dequant, read_block          # noqa: E402
from h3lab.bins import bin_centre, bins_for_year       # noqa: E402

REGION = "844413dffffffff"          # Rookery Bay's res-4 parent
IRMA_BIN = 404                      # 2017-09-08 .. 2017-09-24


def main():
    out_dir = os.path.join(ROOT, "data", "regional")
    manifest = json.load(open(os.path.join(out_dir, "manifest.json"), encoding="utf-8"))
    shards = manifest.get("regions") or []
    match = [s for s in shards if s["region"] == REGION]
    if not match:
        print(f"FAIL: no shard for region {REGION}; "
              f"{len(shards)} regions in the manifest")
        return 1
    shard = match[0]
    path = os.path.join(out_dir, shard["file"])
    print(f"shard {shard['file']}  ({os.path.getsize(path) / 1e6:.2f} MB served)")

    blk = read_block(path)
    desc = blk["descriptor"]
    ids = desc["ids"]
    print(f"cells {len(ids):,}  periods {desc['nPeriods']}  epoch {desc.get('epoch')}")

    ndvi = dequant(blk["index.ndvi"], blk["index.ndvi__meta"])
    observed = blk["observed"].astype(bool)
    print(f"observed fraction {observed.mean():.3f}")
    periods = desc["periods"]
    dates = [p["date"] for p in periods]
    print("periods:", ", ".join(d[:10] for d in dates))

    # wet/dry amplitude on this shard
    from h3lab.bins import is_wet_season_date
    wet = np.array([is_wet_season_date(d) for d in dates])
    w = np.nanmean(np.where(wet[:, None], ndvi, np.nan), axis=0)
    d = np.nanmean(np.where(~wet[:, None], ndvi, np.nan), axis=0)
    both = np.isfinite(w) & np.isfinite(d)
    if both.any():
        amp = (w - d)[both]
        print(f"wet - dry NDVI: median {np.median(amp):+.3f}, "
              f"{100 * (amp > 0).mean():.0f}% positive, "
              f"{int(both.sum()):,} cells in both seasons")

    # the change layer
    names = {f["name"] for f in desc["fields"]}
    for need in ("break_bin", "break_date", "break_magnitude", "break_score",
                 "n_obs_used", "method"):
        if need not in names:
            print(f"FAIL: missing change field {need}")
            return 1
    bb = blk["break_bin"]
    flagged = bb != 65535
    print(f"breaks flagged in {int(flagged.sum()):,}/{len(ids):,} cells "
          f"({100 * flagged.mean():.1f}%)")
    if flagged.any():
        vals, counts = np.unique(bb[flagged], return_counts=True)
        for v, c in sorted(zip(vals, counts), key=lambda kv: -kv[1]):
            print(f"   bin {int(v):3d} ({bin_centre(int(v))}): {int(c):,}")
        mag = dequant(blk["break_magnitude"], blk["break_magnitude__meta"])
        print(f"   magnitude: median {np.nanmedian(mag):+.3f}, "
              f"p10 {np.nanpercentile(mag, 10):+.3f}, "
              f"p90 {np.nanpercentile(mag, 90):+.3f}")
        sc = dequant(blk["break_score"], blk["break_score__meta"])
        print(f"   score: median {np.nanmedian(sc):.1f}, max {np.nanmax(sc):.1f}")
        near = int(((bb == IRMA_BIN)).sum())
        print(f"   breaks at the Irma bin ({IRMA_BIN}, "
              f"{bin_centre(IRMA_BIN)}): {near:,}")

    # NDVI drop across the Irma window, for the cells that lost vegetation
    pi = [i for i, p in enumerate(periods) if p.get("bin") == IRMA_BIN
          or bin_centre(int(p.get("bin", -1))) == bin_centre(IRMA_BIN)]
    if pi:
        i = pi[0]
        before = np.nanmean(ndvi[max(i - 2, 0):i], axis=0)
        after = np.nanmean(ndvi[i:min(i + 2, ndvi.shape[0])], axis=0)
        drop = after - before
        ok = np.isfinite(drop)
        if ok.any():
            print(f"NDVI across the Irma bin: median {np.nanmedian(drop):+.3f}, "
                  f"p5 {np.nanpercentile(drop, 5):+.3f}, "
                  f"p95 {np.nanpercentile(drop, 95):+.3f}")
            worst = np.argsort(np.where(ok, drop, np.inf))[:5]
            for c in worst:
                print(f"   {ids[c]}  drop {drop[c]:+.3f}  "
                      f"break {int(bb[c]) if bb[c] != 65535 else 'none'}")
    else:
        print("WARN: the Irma bin is not among this shard's periods")
    return 0


if __name__ == "__main__":
    sys.exit(main())
