"""Rebuild the published pilot bundle in the assessment format (D-32).

The pilot bundle predates the assessment export: it carries six reflectance
bands per composite and no per-cell fit. Rebuilding it from scenes needs the
raster cache and a long Planetary Computer run, so this tool recomputes the
new fields from the index and band values already in the published bundle,
using the same :mod:`h3lab.analysis` code the export runs.

What changes
------------
* ``band.*`` fields are dropped, and with them ``meta.bands``/``band_info``.
* ``cell.<metric>.<index>`` fields are added: trend, trend significance,
  seasonal amplitude, seasonal peak, fit R2 and largest anomaly, for every
  index in the bundle.
* ``cell.stab.angle`` / ``cell.stab.churn`` are added, so the stability layer
  no longer needs the band vectors in the browser.
* ``cellraw.anomp.<index>`` records which composite the largest anomaly fell
  on (65535 = none).

The tool reads the *old* bundle, so it cannot be run twice: after the first
run there are no bands to compute stability from. The committed bundle in
git is the safety net.

    PYTHONPATH=src python tools/reanalyze_pilot.py --dry-run
    PYTHONPATH=src python tools/reanalyze_pilot.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from h3lab.analysis import SPECS as ANALYSIS_SPECS, analyse   # noqa: E402
from h3lab.binary import read_block, write_block              # noqa: E402

PROJECT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STEM = "rookery_bay_h3"


def _fields(desc):
    return {f["name"]: f for f in desc["fields"]}


def _float_field(desc, raw, name):
    """Dequantise one field; missing -> NaN."""
    f = _fields(desc)[name]
    q = raw[name].astype(np.float64)
    return np.where(q == desc["missing"], np.nan, f["min"] + q * f["scale"])


def _geometry(desc, raw):
    f = _fields(desc)["geomXY"]
    q = raw["geomXY"].astype(np.float64)
    lon = f["lonLo"] + q[0::2] * f["gscaleX"]
    lat = f["latLo"] + q[1::2] * f["gscaleY"]
    flat = np.empty(lon.size * 2)
    flat[0::2] = lon
    flat[1::2] = lat
    return np.asarray(raw["geomVerts"]), flat.tolist()


def rebuild(path):
    """Read one old-format block and return (new block dict, stats)."""
    desc, raw = read_block(path)
    bands = list(desc.get("bands") or [])
    if not bands or f"band.{bands[0]}" not in raw:
        raise SystemExit(
            f"{path}: no band fields -- already migrated, or not a pilot bundle"
        )

    periods = desc["periods"]
    indices = list(desc["indices"])

    index_values = {k: _float_field(desc, raw, f"index.{k}") for k in indices}
    band_values = {b: _float_field(desc, raw, f"band.{b}") for b in bands}
    observed = np.asarray(raw["observed"], dtype=bool)

    cell, cellraw = analyse(periods, index_values, observed, band_values)

    verts, flat = _geometry(desc, raw)
    block = {
        "periods": periods,
        "nCells": desc["nCells"],
        "ids": desc["ids"],
        "lon": desc["lon"],
        "lat": desc["lat"],
        "areaKm2": desc["areaKm2"],
        "index": index_values,
        "observed": observed,
        "validFraction": _float_field(desc, raw, "validFraction"),
        "nObs": np.asarray(raw["nObs"]),
        "cell": cell,
        "cellraw": cellraw,
        "_nVerts": verts,
        "_geomXY": flat,
        "_indices": indices,
    }
    stats = {"index": index_values, "cell": cell, "observed": observed}
    return block, stats


def report(res, stats):
    print(f"\nres {res}")
    for k in stats["index"]:
        c = stats["cell"]
        tr, amp, phase, r2, anom = (c[f"trend.{k}"], c[f"amp.{k}"],
                                    c[f"phase.{k}"], c[f"r2.{k}"],
                                    c[f"anom.{k}"])
        ok = np.isfinite(tr)
        if not ok.any():
            print(f"  {k:5s} no fitted cells")
            continue
        wet = np.nanmean(phase == 8) * 100
        print(f"  {k:5s} fitted {ok.sum():5d}/{ok.size:<5d} "
              f"trend {np.nanmean(tr):+.4f} "
              f"[{np.nanpercentile(tr[np.isfinite(tr)], 5):+.4f},"
              f"{np.nanpercentile(tr[np.isfinite(tr)], 95):+.4f}] "
              f"amp {np.nanmean(amp):.3f} "
              f"wet-peak {wet:4.0f}% "
              f"r2 {np.nanmean(r2):.2f} "
              f"anom {np.nanmean(anom):.2f}σ")
    ang = stats["cell"]["stab.angle"]
    churn = stats["cell"]["stab.churn"]
    print(f"  stability: angle {np.isfinite(ang).sum()}/{ang.size} "
          f"median {np.nanmedian(ang):.2f}°, "
          f"churn {np.isfinite(churn).sum()}/{churn.size} "
          f"median {np.nanmedian(churn):.3f}")


def update_meta(meta_path, infos, old_meta):
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    meta.pop("bands", None)
    meta.pop("band_info", None)
    meta["indices"] = list(old_meta["indices"])
    meta["analysis"] = ANALYSIS_SPECS
    meta["note"] = (
        "Spectral indices and a per-cell assessment of the whole record "
        "(trend, season, fit, anomaly, stability). No land-cover "
        "classification is applied anywhere in this pipeline."
    )
    by_res = {r["res"]: r for r in meta["resolutions"]}
    for res, info in infos.items():
        entry = by_res[res]
        entry["nCells"] = info["cells"]
        entry["nPeriods"] = info["periods"]
        entry["mb"] = info["served_mb"]
        entry["rawMb"] = info["raw_mb"]
        entry["bin"] = os.path.basename(info["path"])
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, separators=(",", ":"), allow_nan=False)
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=os.path.join(PROJECT_DIR, "data"))
    ap.add_argument("--res", default="8,9,10")
    ap.add_argument("--dry-run", action="store_true",
                    help="decode and report, write nothing")
    args = ap.parse_args()

    resolutions = [int(r) for r in args.res.split(",") if r.strip()]
    files = {r: os.path.join(args.data_dir, f"{STEM}_res{r}.bin.gz")
             for r in resolutions}
    for path in files.values():
        if not os.path.exists(path):
            raise SystemExit(f"missing {path}")

    meta_path = os.path.join(args.data_dir, f"{STEM}_meta.json")
    with open(meta_path, encoding="utf-8") as fh:
        old_meta = json.load(fh)

    infos = {}
    for res in resolutions:
        path = files[res]
        before = os.path.getsize(path) / 1e6
        block, stats = rebuild(path)
        report(res, stats)
        if args.dry_run:
            continue
        tmp = os.path.join(args.data_dir, f".{STEM}_res{res}.tmp")
        info = write_block(block, tmp, gz=True)
        os.replace(info["path"], path)
        info["path"] = path                       # meta names the published file
        infos[res] = info
        print(f"  wrote {os.path.basename(path)}: "
              f"{before:.2f} -> {info['served_mb']:.2f} MB served "
              f"({info['raw_mb']:.2f} MB raw)")

    if args.dry_run:
        print("\ndry run: nothing written")
        return
    update_meta(meta_path, infos, old_meta)
    print(f"\nupdated {os.path.basename(meta_path)}")


if __name__ == "__main__":
    main()
