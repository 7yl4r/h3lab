"""Compact binary bundle format for the viewer.

Why not JSON
------------
Profiling the viewer showed the cost is bytes on the wire, not CPU: parsing
9 MB of JSON took 51 ms and 62.5 MB took 342 ms, but `python -m http.server`
ignores `Accept-Encoding` and ships all of it uncompressed. The fix is to stop
serialising floats as decimal text.

Floats are quantised to uint16 per field against that field's own observed
range. Reflectance is 0..1 and indices -1..1, so 65 534 steps is far finer
than anything being displayed, and the per-field scale makes the range
adaptive rather than assumed.

Layout, little-endian throughout:

    0   4  magic  b"H3LB"
    4   2  uint16 format version
    6   2  uint16 flags (reserved, 0)
    8   4  uint32 descriptor length in bytes (UTF-8 JSON)
    12  4  uint32 CRC32 of the descriptor (integrity check)
    16  ...   descriptor JSON, padded to a 16-byte boundary
    ...      payload arrays, back to back, in descriptor order

Every field in the payload is either:
  * "u16"  quantised float: value = min + q * scale, q == 65535 means missing
  * "u8"   unsigned byte: observed
  * "u16r" raw unsigned short: nObs, break fields, method index

The descriptor names every field and its shape, so the viewer can build typed
array views without any hard-coded knowledge of the layout.

Change-layer fields (D-14)
---------------------------
A regional shard carries the break statistics alongside the observations:

  * "break_bin"      u16r, the bin number of the largest break; 65535 = none
  * "break_date"     u16r, days since the descriptor's epoch (the break bin's
                     centre date); 65535 = none
  * "break_magnitude" u16, quantised float: change in index units (post - pre)
  * "break_score"    u16, quantised float: partial F statistic of the step
  * "n_obs_used"     u16r, observations entering the fit
  * "method"         u16r, index into the descriptor's "changeMethods" table

The method table is what makes the layer reproducible: it records the
algorithm name, version and parameters (D-14), and it lives in the descriptor
rather than being repeated once per cell.
"""

from __future__ import annotations

import gzip
import json
import struct
import zlib

import numpy as np

MAGIC = b"H3LB"
VERSION = 1
HEADER = 16
ALIGN = 16
MISSING = 65535


def _flatten(values: list, n_cells: int) -> np.ndarray:
    """Period-major (P, C) float array from the JSON block layout."""
    arr = np.empty((len(values), n_cells), dtype=np.float64)
    for i, row in enumerate(values):
        # Catch a length mismatch here, where the offending field is named,
        # rather than as a numpy broadcast error several frames deeper.
        if len(row) != n_cells:
            raise ValueError(
                f"row {i} has {len(row)} values but the block has {n_cells} cells"
            )
        arr[i] = [np.nan if v is None else float(v) for v in row]
    return arr


def _flat_cells(values, n_cells: int) -> np.ndarray:
    """Per-cell float array (the change layer has no time axis)."""
    arr = np.asarray(values, dtype=np.float64).ravel()
    if arr.size != n_cells:
        raise ValueError(
            f"change field has {arr.size} values but the block has {n_cells} cells"
        )
    return arr


def _quantise(arr: np.ndarray) -> tuple[np.ndarray, float, float]:
    """uint16 array plus (min, scale). Missing values become MISSING."""
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.full(arr.shape, MISSING, dtype=np.uint16), 0.0, 0.0
    lo = float(finite.min())
    hi = float(finite.max())
    if hi <= lo:
        # constant field: any scale works, keep 0 so the value round-trips
        hi = lo + 1.0
    scale = (hi - lo) / (MISSING - 1)
    q = np.rint((np.where(np.isfinite(arr), arr, lo) - lo) / scale)
    q = np.clip(q, 0, MISSING - 1).astype(np.uint16)
    q[~np.isfinite(arr)] = MISSING
    return q, lo, scale


def build_block(block: dict) -> tuple[dict, list[bytes]]:
    """Return (descriptor, payload chunks) for one resolution block."""
    n_cells = block["nCells"]
    n_periods = len(block["periods"])
    shape = [n_periods, n_cells]
    fields: list[dict] = []
    chunks: list[bytes] = []

    # `band` is optional: the pilot export drops reflectance and ships the
    # assessment instead, while the regional archive still carries it.
    float_groups = (
        ("band", block.get("band") or {}),
        ("index", block.get("index") or {}),
    )
    for group_name, group in float_groups:
        for name, values in group.items():
            arr = _flatten(values, n_cells)
            q, lo, scale = _quantise(arr)
            fields.append({
                "name": f"{group_name}.{name}",
                "dtype": "u16",
                "shape": shape,
                "min": lo,
                "scale": scale,
            })
            chunks.append(q.tobytes())

    # Per-cell assessment fields have no time axis, like the change layer.
    cell_fields = []
    for name, values in (block.get("cell") or {}).items():
        arr = _flat_cells(values, n_cells)
        q, lo, scale = _quantise(arr)
        fields.append({"name": f"cell.{name}", "dtype": "u16",
                       "shape": [n_cells], "min": lo, "scale": scale})
        chunks.append(q.tobytes())
        cell_fields.append(name)

    # Raw per-cell integers (e.g. the period index of an anomaly). Kept out
    # of the float quantiser so an index round-trips exactly.
    cellraw_fields = []
    for name, values in (block.get("cellraw") or {}).items():
        arr = np.asarray(values, dtype=np.int64).ravel()
        if arr.size != n_cells:
            raise ValueError(
                f"cellraw field {name!r} has {arr.size} values but the block "
                f"has {n_cells} cells"
            )
        fields.append({"name": f"cellraw.{name}", "dtype": "u16r",
                       "shape": [n_cells], "missing": MISSING})
        chunks.append(np.where((arr < 0) | (arr >= MISSING), MISSING, arr)
                      .astype(np.uint16).tobytes())
        cellraw_fields.append(name)

    # validFraction is a float: quantise it like the other numeric fields.
    vf = _flatten(block["validFraction"], n_cells)
    q, lo, scale = _quantise(vf)
    fields.append({"name": "validFraction", "dtype": "u16", "shape": shape,
                   "min": lo, "scale": scale})
    chunks.append(q.tobytes())

    # observed is a boolean and nObs a count: neither is a float, so both are
    # stored raw rather than quantised.
    observed = np.asarray(block["observed"], dtype=bool).astype(np.uint8)
    fields.append({"name": "observed", "dtype": "u8", "shape": shape})
    chunks.append(observed.tobytes())

    n_obs = np.asarray(block["nObs"], dtype=np.uint16)
    fields.append({"name": "nObs", "dtype": "u16r", "shape": shape})
    chunks.append(n_obs.tobytes())

    # Hexagon outlines. These are fixed properties of the grid, but computing
    # them was measured as the single most expensive part of opening a block
    # (3.5 s for 20,607 cells at res 10) because it calls into the H3
    # library for every cell on every load. Baking them turns opening a block
    # into a header parse plus a copy.
    #
    # H3 returns 5 vertices for pentagons and 6 for most hexagons, so vertices
    # are stored in one flat buffer with a per-cell offset. Floats are
    # quantised against the block's bounding box, which costs about 1e-7
    # degrees -- far below a pixel at every zoom level used here.
    verts_per_cell = np.asarray(block["_nVerts"], dtype=np.uint16)
    flat = np.asarray(block["_geomXY"], dtype=np.float64)   # 1-D, interleaved
    nverts_total = int(verts_per_cell.sum())
    coords = flat[: nverts_total * 2].reshape(-1, 2)
    lon_lo, lon_hi = float(coords[:, 0].min()), float(coords[:, 0].max())
    lat_lo, lat_hi = float(coords[:, 1].min()), float(coords[:, 1].max())
    gscale_x = (lon_hi - lon_lo) / (MISSING - 1) or 1.0
    gscale_y = (lat_hi - lat_lo) / (MISSING - 1) or 1.0
    gq = np.empty(nverts_total * 2, dtype=np.uint16)
    gq[0::2] = np.clip(np.rint((coords[:, 0] - lon_lo) / gscale_x), 0, MISSING - 1)
    gq[1::2] = np.clip(np.rint((coords[:, 1] - lat_lo) / gscale_y), 0, MISSING - 1)
    fields.append({"name": "geomVerts", "dtype": "u16r", "shape": [n_cells]})
    chunks.append(verts_per_cell.tobytes())
    fields.append({"name": "geomXY", "dtype": "u16", "shape": [gq.size],
                   "min": 0.0, "scale": 0.0, "lonLo": lon_lo, "latLo": lat_lo,
                   "gscaleX": gscale_x, "gscaleY": gscale_y})
    chunks.append(gq.tobytes())

    change_methods = []
    if block.get("change"):
        change = block["change"]
        methods = list(change.get("methods", []))
        method_idx = np.asarray(change["method"], dtype=np.int64)
        # A method name that is not in the table is an error, not a silent 0.
        for m in method_idx:
            if m < 0 or m >= len(methods):
                raise ValueError(f"change method index {m} out of range")
        change_methods = methods

        # break_bin / break_date: raw shorts, MISSING means "no break".
        bb = np.asarray(change["break_bin"], dtype=np.int64)
        fields.append({"name": "break_bin", "dtype": "u16r", "shape": [n_cells],
                       "missing": MISSING,
                       "desc": "bin number of the largest break"})
        chunks.append(np.where(bb < 0, MISSING, bb).astype(np.uint16).tobytes())

        bd = np.asarray(change["break_date"], dtype=np.int64)
        fields.append({"name": "break_date", "dtype": "u16r", "shape": [n_cells],
                       "missing": MISSING, "unit": "days since epoch",
                       "desc": "centre date of the break bin"})
        chunks.append(np.where(bd < 0, MISSING, bd).astype(np.uint16).tobytes())

        bm = _flat_cells(change["break_magnitude"], n_cells)
        q, lo, scale = _quantise(bm)
        fields.append({"name": "break_magnitude", "dtype": "u16", "shape": [n_cells],
                       "min": lo, "scale": scale})
        chunks.append(q.tobytes())

        bs = _flat_cells(change["break_score"], n_cells)
        q, lo, scale = _quantise(bs)
        fields.append({"name": "break_score", "dtype": "u16", "shape": [n_cells],
                       "min": lo, "scale": scale})
        chunks.append(q.tobytes())

        no = np.asarray(change["n_obs_used"], dtype=np.uint16)
        fields.append({"name": "n_obs_used", "dtype": "u16r", "shape": [n_cells]})
        chunks.append(no.tobytes())

        fields.append({"name": "method", "dtype": "u16r", "shape": [n_cells],
                       "table": "changeMethods",
                       "desc": "index into the changeMethods table"})
        chunks.append(method_idx.astype(np.uint16).tobytes())

    descriptor = {
        "nCells": n_cells,
        "nPeriods": n_periods,
        "periods": block["periods"],
        "bands": block.get("_bands", []),
        "indices": block.get("_indices", []),
        "ids": block["ids"],
        "lon": block["lon"],
        "lat": block["lat"],
        "areaKm2": block["areaKm2"],
        "fields": fields,
        "missing": MISSING,
    }
    if cell_fields:
        descriptor["cells"] = cell_fields
    if cellraw_fields:
        descriptor["cellraws"] = cellraw_fields
    if block.get("epoch"):
        descriptor["epoch"] = block["epoch"]
    if block.get("binDays"):
        descriptor["binDays"] = block["binDays"]
    if change_methods:
        descriptor["changeMethods"] = change_methods
    return descriptor, chunks


def pack(descriptor: dict, chunks: list[bytes]) -> bytes:
    raw = json.dumps(descriptor, separators=(",", ":")).encode("utf-8")
    pad = (-(HEADER + len(raw))) % ALIGN
    out = bytearray()
    out += MAGIC
    out += struct.pack("<HHII", VERSION, 0, len(raw), zlib.crc32(raw) & 0xFFFFFFFF)
    out += raw
    out += b"\x00" * pad
    for c in chunks:
        out += c
        out += b"\x00" * ((-len(out)) % ALIGN)
    return bytes(out)


def read_descriptor(path: str) -> dict:
    """Read the descriptor of a written block, without its payload.

    The descriptor is the first thing in the file and is plain JSON, so a
    reader can learn the block's shape -- how many cells, how many periods,
    which fields exist -- without decompressing or decoding the payload. The
    manifest and the verification tooling use this to describe the bundles
    without holding them in memory.
    """
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as fh:
        head = fh.read(HEADER)
        if len(head) < HEADER or head[:4] != MAGIC:
            raise ValueError(f"{path}: not an H3LB block")
        _version, _flags, length, crc = struct.unpack("<HHII", head[4:HEADER])
        raw = fh.read(length)
    if len(raw) != length:
        raise ValueError(f"{path}: truncated descriptor")
    if zlib.crc32(raw) & 0xFFFFFFFF != crc:
        raise ValueError(f"{path}: descriptor CRC mismatch")
    return json.loads(raw.decode("utf-8"))


def read_block(path: str) -> tuple[dict, dict[str, np.ndarray]]:
    """Read a written block in full.

    Returns the descriptor and the raw (still quantised) fields keyed by
    field name. This is the counterpart to :func:`write_block`; the migration
    tool uses it to rebuild a published bundle without going back to scenes.
    """
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as fh:
        blob = fh.read()
    if len(blob) < HEADER or blob[:4] != MAGIC:
        raise ValueError(f"{path}: not an H3LB block")
    _version, _flags, length, crc = struct.unpack("<HHII", blob[4:HEADER])
    raw = blob[HEADER:HEADER + length]
    if len(raw) != length:
        raise ValueError(f"{path}: truncated descriptor")
    if zlib.crc32(raw) & 0xFFFFFFFF != crc:
        raise ValueError(f"{path}: descriptor CRC mismatch")
    desc = json.loads(raw.decode("utf-8"))

    off = HEADER + length
    off += (-off) % ALIGN
    out: dict[str, np.ndarray] = {}
    for field in desc["fields"]:
        n = 1
        for dim in field["shape"]:
            n *= int(dim)
        dtype = np.uint8 if field["dtype"] == "u8" else np.uint16
        need = n * np.dtype(dtype).itemsize
        if off + need > len(blob):
            raise ValueError(f"{path}: field {field['name']!r} runs past the end")
        out[field["name"]] = np.frombuffer(
            blob, dtype=dtype, count=n, offset=off
        ).reshape(field["shape"])
        off += need
        off += (-off) % ALIGN
    if off != len(blob):
        raise ValueError(f"{path}: {len(blob) - off} trailing bytes")
    return desc, out


def write_block(block: dict, path: str, gz: bool = True) -> dict:
    descriptor, chunks = build_block(block)
    blob = pack(descriptor, chunks)
    n_cells = descriptor["nCells"]
    n_periods = descriptor["nPeriods"]

    if gz:
        # mtime=0 keeps the gzip header free of a timestamp, so re-exporting
        # unchanged data produces byte-identical files. Without it every rebuild
        # is a 14 MB phantom diff and, once committed, permanent history growth.
        payload = gzip.compress(blob, compresslevel=6, mtime=0)
        out = path + ".gz"
    else:
        payload = blob
        out = path
    with open(out, "wb") as fh:
        fh.write(payload)

    return {
        "raw_mb": round(len(blob) / 1e6, 2),
        "served_mb": round(len(payload) / 1e6, 2),
        "cells": n_cells,
        "periods": n_periods,
        "path": out,
    }
