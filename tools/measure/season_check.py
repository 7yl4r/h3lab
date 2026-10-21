"""D-18 acceptance check: regional wet/dry amplitude vs the pilot's.

The plan's exit criterion 3 is a *comparison*, not an absolute bar:

    "regional 2017 NDVI wet/dry amplitude vs the pilot's at comparable cells
     (D-18 acceptance check)"                                      PHASE1 §11

and D-18 rule 4 states it as: "regional wet/dry NDVI amplitude must match the
pilot's at comparable cells. If it is damped, compositing is smoothing the
cycle."

The two products use different season windows and they have to be matched
before the amplitudes can be compared at all:

  pilot   wet = May 1 - Oct 31      dry = Nov 1 - Dec 31    (config.SEASONS;
                                 `ingest.find_scenes` uses Nov-Dec of the same
                                 year -- the "(+ Jan-Feb folded in)" comment on
                                 config.SEASONS is stale)
  region  wet = May - Oct           dry = Nov - Apr         (the complement of
                                 bins.is_wet_season_date, matching the viewer)

The regional's dry season is two months longer than the pilot's, so the
comparison uses the **pilot's** windows on the regional's bins (D-28): the
regional's Nov-Apr dry mean against the pilot's Nov-Dec dry mean would read
March and April as a damped cycle. The cells compared are the ones the two
products share: the 421 res-8 cells of the Rookery Bay AOI that also fall
inside the regional archive.
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

REGIONAL = os.path.join(ROOT, "data", "regional")
PILOT = os.path.join(ROOT, "data", "rookery_bay_h3_res8.bin.gz")
ROOKERY = "844413dffffffff"        # res-4 parent of the Rookery Bay AOI


def windows(periods):
    """(wet_mask, dry_mask) for a list of period descriptors.

    The pilot's periods carry `year` and `season`; the regional's carry a
    `date`. Both are mapped onto the *pilot's* calendar so the two products
    are compared over the same months of the same year.
    """
    wet = np.zeros(len(periods), dtype=bool)
    dry = np.zeros(len(periods), dtype=bool)
    for i, p in enumerate(periods):
        if "season" in p:
            wet[i] = p["season"] == "wet"
            dry[i] = p["season"] == "dry"
        else:
            d = p["date"]
            month = int(d[5:7])
            wet[i] = 5 <= month <= 10
            dry[i] = month in (11, 12)
    return wet, dry


def season_mean(ndvi, mask):
    with np.errstate(invalid="ignore"):
        return np.nanmean(np.where(mask[:, None], ndvi, np.nan), axis=0)


def main():
    # -- pilot --------------------------------------------------------------
    if not os.path.exists(PILOT):
        print(f"skip: the pilot bundle {PILOT} is not present. It is "
              f"tracked in the repository, so a fresh clone has it; a build "
              f"tree without it cannot run this comparison.")
        return
    pb = read_block(PILOT)
    pdesc = pb["descriptor"]
    pndvi = dequant(pb["index.ndvi"], pb["index.ndvi__meta"])
    pwet, pdry = windows(pdesc["periods"])
    print(f"pilot: {pdesc['nCells']} cells, {pdesc['nPeriods']} periods, "
          f"{pdesc['periods'][0]['year']}-{pdesc['periods'][-1]['year']}, "
          f"{pwet.sum()} wet / {pdry.sum()} dry")
    print("   period dates:", [p.get("date") or
                               f"{p.get('year')}-{p.get('season')}"
                               for p in pdesc["periods"]][:6], "...")

    # the pilot's 2017 periods only
    p2017 = np.array([p.get("year") == 2017 for p in pdesc["periods"]])
    print(f"   2017 periods: {int(p2017.sum())} "
          f"(wet {int((p2017 & pwet).sum())}, dry {int((p2017 & pdry).sum())})")

    pw_all = season_mean(pndvi, pwet)
    pd_all = season_mean(pndvi, pdry)
    pw17 = season_mean(pndvi[p2017], pwet[p2017])
    pd17 = season_mean(pndvi[p2017], pdry[p2017])

    # -- regional, on the Rookery Bay shard ---------------------------------
    manifest = json.load(open(os.path.join(REGIONAL, "manifest.json"),
                              encoding="utf-8"))
    shard = [s for s in manifest["regions"] if s["region"] == ROOKERY][0]
    rb = read_block(os.path.join(REGIONAL, shard["file"]))
    rdesc = rb["descriptor"]
    rndvi = dequant(rb["index.ndvi"], rb["index.ndvi__meta"])
    rwet, rdry = windows(rdesc["periods"])
    rw17 = season_mean(rndvi, rwet)
    rd17 = season_mean(rndvi, rdry)
    print(f"\nregional: Rookery Bay shard, {rdesc['nCells']} cells, "
          f"{rdesc['nPeriods']} bins, {rwet.sum()} wet / {rdry.sum()} dry bins")

    # -- shared cells -------------------------------------------------------
    pidx = {c: i for i, c in enumerate(pdesc["ids"])}
    ridx = {c: i for i, c in enumerate(rdesc["ids"])}
    shared = sorted(set(pidx) & set(ridx))
    print(f"shared cells: {len(shared)}")

    rows = []
    for c in shared:
        i, j = pidx[c], ridx[c]
        p_w, p_d = pw17[i], pd17[i]
        r_w, r_d = rw17[j], rd17[j]
        if not all(np.isfinite(v) for v in (p_w, p_d, r_w, r_d)):
            continue
        # the pilot's whole-record seasonal amplitude, and the regional's
        # 2017 amplitude across the 23 bins
        p_series = pndvi[:, i]
        p_amp = np.nanmax(p_series) - np.nanmin(p_series)
        r_series = rndvi[:, j]
        r_amp = np.nanmax(r_series) - np.nanmin(r_series)
        rows.append((c, p_w, p_d, r_w, r_d, p_amp, r_amp))
    arr = np.array([[r[1], r[2], r[3], r[4], r[5], r[6]] for r in rows])
    print(f"cells with all four seasonal values: {len(rows)}")
    p_wet, p_dry, r_wet, r_dry, p_amp, r_amp = arr.T

    def stats(name, a):
        print(f"   {name:<28s} median {np.median(a):+.3f}  mean {a.mean():+.3f}  "
              f"p25 {np.percentile(a, 25):+.3f}  p75 {np.percentile(a, 75):+.3f}")

    print("\n1. matched-window seasonal means at the shared cells")
    stats("pilot wet (May-Oct) 2017", p_wet)
    stats("pilot dry (Nov-Dec) 2017", p_dry)
    stats("regional wet (May-Oct) 2017", r_wet)
    stats("regional dry (Nov-Dec) 2017", r_dry)
    print()
    p_wd = p_wet - p_dry
    r_wd = r_wet - r_dry
    stats("pilot    wet - dry", p_wd)
    stats("regional wet - dry", r_wd)
    print(f"   correlation (wet-dry): {np.corrcoef(p_wd, r_wd)[0, 1]:+.3f}")
    print(f"   regional dry / pilot dry: {np.median(r_dry) / np.median(p_dry):.3f}")
    print(f"   regional wet / pilot wet: {np.median(r_wet) / np.median(p_wet):.3f}")

    print("\n2. seasonal amplitude (max - min over the record)")
    stats("pilot 13-year amplitude", p_amp)
    stats("regional 2017 amplitude", r_amp)
    print(f"   correlation (amplitude): {np.corrcoef(p_amp, r_amp)[0, 1]:+.3f}")
    print(f"   ratio median regional/pilot amplitude: "
          f"{np.median(r_amp) / np.median(p_amp):.3f}")
    ratio = r_amp / np.maximum(p_amp, 1e-6)
    print(f"   per-cell ratio: median {np.median(ratio):.3f}, "
          f"p25 {np.percentile(ratio, 25):.3f}, p75 {np.percentile(ratio, 75):.3f}")

    # -- the region's own seasonal profile ---------------------------------
    print("\n3. regional 2017 profile at the shared cells (mean NDVI per bin)")
    sel = [r[0] for r in rows]
    for pi, (p, row) in enumerate(zip(rdesc["periods"], rndvi)):
        vals = np.array([row[ridx[c]] for c in sel])
        tag = "wet " if rwet[pi] else ("dry " if rdry[pi] else "    ")
        print(f"   {p['date']} {tag} mean {np.nanmean(vals):+.3f}  "
              f"observed {int(np.isfinite(vals).sum()):>4d}/{len(vals)}")


if __name__ == "__main__":
    main()
