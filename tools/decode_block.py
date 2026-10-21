"""Decode an H3LB bundle in Python (mirrors the viewer's decodeBlock)."""
import gzip, json, struct, sys
import numpy as np


def read_block(path):
    if path.endswith('.gz'):
        with gzip.open(path, 'rb') as fh:
            raw = fh.read()
    else:
        with open(path, 'rb') as fh:
            raw = fh.read()
    assert raw[:4] == b'H3LB', raw[:4]
    version, flags, dlen, crc = struct.unpack('<HHII', raw[4:16])
    desc = json.loads(raw[16:16 + dlen].decode('utf-8'))
    offset = 16 + dlen
    offset += (-offset) % 16
    out = {'descriptor': desc, 'version': version, 'flags': flags}
    for f in desc['fields']:
        shape = f['shape']
        n = int(np.prod(shape)) if shape else 1
        dt = {'u16': np.uint16, 'u8': np.uint8, 'u16r': np.uint16}[f['dtype']]
        arr = np.frombuffer(raw, dtype=dt, count=n, offset=offset).reshape(shape)
        offset += n * np.dtype(dt).itemsize
        offset += (-offset) % 16
        out[f['name']] = arr
        out[f['name'] + '__meta'] = f
    return out


def dequant(arr, f, missing=65535):
    """u16 quantised -> float."""
    a = arr.astype(np.float64)
    v = f['min'] + a * f['scale']
    return np.where(a >= missing, np.nan, v)


if __name__ == '__main__':
    b = read_block(sys.argv[1])
    d = b['descriptor']
    print('descriptor keys:', sorted(d.keys()))
    print('nCells', d['nCells'], 'nPeriods', d['nPeriods'], 'epoch', d.get('epoch'),
          'binDays', d.get('binDays'))
    print('fields:', [(f['name'], f['dtype'], f['shape']) for f in d['fields']])
    print('changeMethods:', d.get('changeMethods'))
    for name in ('band.nir', 'index.ndvi', 'validFraction', 'observed', 'nObs'):
        if name in b:
            meta = b[name + '__meta']
            if meta['dtype'] == 'u16':
                v = dequant(b[name], meta)
            else:
                v = b[name].astype(np.float64)
            print(f'{name}: min {np.nanmin(v):.4f} max {np.nanmax(v):.4f} '
                  f'mean {np.nanmean(v):.4f} nan {int(np.isnan(v).sum())}/{v.size}')
    for name in ('break_bin', 'break_date', 'break_magnitude', 'break_score',
                 'n_obs_used', 'method'):
        if name in b:
            meta = b[name + '__meta']
            v = dequant(b[name], meta) if meta['dtype'] == 'u16' else b[name]
            print(f'{name}: unique {np.unique(v)[:8]} dtype {b[name].dtype}')
