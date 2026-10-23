"""Restore reflectance to the pilot bundle after the assessment migration.

D-32 added the per-cell assessment but dropped ``band.*`` and ``meta.bands`` /
``band_info`` at the same time. The source rasters are not kept in this
checkout, so the pre-migration bundles committed at ``faf3bc7`` are the only
remaining copy. This tool rebuilds each bundle from that copy: the bands and
indices are dequantised, the assessment is recomputed with the same
:mod:`h3lab.analysis` code the export runs, and both groups are written
together. It also puts ``bands`` / ``band_info`` back in the metadata.

Extract the old bundle first, for example:

    mkdir -p /tmp/old
    for f in data/rookery_bay_h3_res8.bin.gz \
             data/rookery_bay_h3_res9.bin.gz \
             data/rookery_bay_h3_res10.bin.gz; do
      git show faf3bc7:$f > /tmp/old/$(basename $f)
    done
    git show faf3bc7:data/rookery_bay_h3_meta.json > /tmp/old/meta.json

    PYTHONPATH=src python tools/restore_spectra.py --src-dir /tmp/old

This is a one-shot recovery: after the rebuild there is no pre-migration
bundle left on disk, and running it again from the new bundle would only
find assessment fields. ``--dry-run`` decodes and compares without writing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

from h3lab.analysis import analyse                         # noqa: E402
from h3lab.binary import read_block, write_block           # noqa: E402
from reanalyze_pilot import _float_field, _geometry         # noqa: E402

PROJECT_DIR = os.path.abspath(os.path.join(HERE, ".."))
STEM = "rookery_bay_h3"


def rebuild(src_path):
    """Read a pre-migration block; return a block with bands + assessment."""
    desc, raw = read_block(src_path)
    bands = list(desc.get("bands") or [])
    if not bands or f"band.{bands[0]}" not in raw:
        raise SystemExit(
            f"{src_path}: no band fields -- already migrated, or not a pilot bundle"
        )
    indices = list(desc.get("indices") or [])

    index_values = {k: _float_field(desc, raw, f"index.{k}") for k in indices}
    band_values = {b: _float_field(desc, raw, f"band.{b}") for b in bands}
    observed = np.asarray(raw["observed"], dtype=bool)
    verts, flat = _geometry(desc, raw)

    block = {
        "periods": desc["periods"],
        "nCells": desc["nCells"],
        "ids": desc["ids"],
        "lon": desc["lon"],
        "lat": desc["lat"],
        "areaKm2": desc["areaKm2"],
        "band": band_values,
        "index": index_values,
        "observed": observed,
        "validFraction": _float_field(desc, raw, "validFraction"),
        "nObs": np.asarray(raw["nObs"]),
        "_bands": bands,
        "_indices": indices,
        "_nVerts": verts,
        "_geomXY": flat,
    }
    block["cell"], block["cellraw"] = analyse(
        block["periods"], index_values, observed, band_values
    )
    return block


def compare_cells(block, current_path):
    """Max |difference| between rebuilt and currently published cell fields.

    The current bundle was built from the same dequantised source, so the
    only difference should be the second quantisation. A large number means
    the source or the analysis code has moved under this recovery.
    """
    if not os.path.exists(current_path):
        return {}
    desc, raw = read_block(current_path)
    names = list(desc.get("cells") or [])
    out = {}
    for name in names:
        if name not in block["cell"]:
            continue
        a = np.asarray(block["cell"][name], dtype=np.float64)
        b = _float_field(desc, raw, f"cell.{name}")
        ok = np.isfinite(a) & np.isfinite(b)
        out[name] = float(np.max(np.abs(a[ok] - b[ok]))) if ok.any() else 0.0
    return out


def merge_meta(old_meta, current_meta, infos):
    """Current metadata plus the band tables the migration removed."""
    meta = {}
    for key, value in current_meta.items():
        if key == "analysis":
            continue
        if key == "indices":
            meta["bands"] = list(old_meta["bands"])
            meta["band_info"] = old_meta["band_info"]
        meta[key] = value
        if key == "index_specs" and "analysis" in current_meta:
            meta["analysis"] = current_meta["analysis"]
    meta["note"] = (
        "Surface reflectance, spectral indices and a per-cell assessment of "
        "the whole record. No land-cover classification is applied anywhere "
        "in this pipeline."
    )
    by_res = {r["res"]: r for r in meta["resolutions"]}
    for res, info in infos.items():
        entry = by_res[res]
        entry["nCells"] = info["cells"]
        entry["nPeriods"] = info["periods"]
        entry["mb"] = info["served_mb"]
        entry["rawMb"] = info["raw_mb"]
        entry["bin"] = os.path.basename(info["path"])
    return meta


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--src-dir", default="/tmp/old",
                    help="directory with the pre-migration bundles and meta.json")
    ap.add_argument("--data-dir", default=os.path.join(PROJECT_DIR, "data"))
    ap.add_argument("--res", default="8,9,10")
    ap.add_argument("--dry-run", action="store_true",
                    help="decode, compare and report; write nothing")
    args = ap.parse_args()

    resolutions = [int(r) for r in args.res.split(",") if r.strip()]
    meta_path = os.path.join(args.data_dir, f"{STEM}_meta.json")
    with open(os.path.join(args.src_dir, "meta.json"), encoding="utf-8") as fh:
        old_meta = json.load(fh)
    with open(meta_path, encoding="utf-8") as fh:
        current_meta = json.load(fh)

    infos = {}
    for res in resolutions:
        src = os.path.join(args.src_dir, f"{STEM}_res{res}.bin.gz")
        dst = os.path.join(args.data_dir, f"{STEM}_res{res}.bin.gz")
        if not os.path.exists(src):
            raise SystemExit(f"missing source bundle {src}")

        block = rebuild(src)
        diffs = compare_cells(block, dst)
        before = os.path.getsize(dst) / 1e6 if os.path.exists(dst) else 0.0
        worst = max(diffs.items(), key=lambda kv: kv[1], default=(None, 0.0))
        print(f"res {res}: {len(block['_bands'])} bands, "
              f"{len(block['periods'])} composites")
        if worst[0]:
            print(f"  assessment vs current: max |diff| {worst[1]:.2e} "
                  f"({worst[0]})")
        if args.dry_run:
            continue

        tmp = os.path.join(args.data_dir, f".{STEM}_res{res}.tmp")
        info = write_block(block, tmp, gz=True)
        os.replace(info["path"], dst)
        info["path"] = dst
        infos[res] = info
        print(f"  wrote {os.path.basename(dst)}: "
              f"{before:.2f} -> {info['served_mb']:.2f} MB served "
              f"({info['raw_mb']:.2f} MB raw)")

    if args.dry_run:
        print("\ndry run: nothing written")
        return

    meta = merge_meta(old_meta, current_meta, infos)
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, separators=(",", ":"), allow_nan=False)
    print(f"\nupdated {os.path.basename(meta_path)} "
          f"({len(meta['bands'])} bands restored)")


if __name__ == "__main__":
    main()
