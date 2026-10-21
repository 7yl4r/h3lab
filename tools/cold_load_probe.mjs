// Headless cold-load probe for the regional overview bundle.
//
//     node tools/cold_load_probe.mjs http://localhost:8000
//
// This mirrors what the browser does on a cold load of viewer/regional.html:
// fetch the manifest, fetch the res-7 overview, inflate it, parse the
// descriptor and materialise the typed arrays. It is the same work the
// viewer's probe.html measures, minus the map tiles, so it is a faithful --
// and automatable -- stand-in for the "< 3 s" exit criterion.
//
// Usage: node tools/cold_load_probe.mjs <baseUrl> [outDir]

const base = (process.argv[2] || 'http://localhost:8000').replace(/\/$/, '');
const outDir = process.argv[3] || 'data/regional';

const t = (fn) => {
  const s = performance.now();
  return Promise.resolve(fn()).then((v) => [v, performance.now() - s]);
};

// The viewer's decodeBlock, minus the parts that only matter for drawing.
function decodeBlock(buffer) {
  const dv = new DataView(buffer);
  const magic = dv.getUint32(0, true);
  if (magic !== 0x424c3348) throw new Error('not an H3LB bundle (bad magic)');
  const descLen = dv.getUint32(8, true);
  const desc = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 16, descLen)));
  const MISSING = desc.missing, C = desc.nCells, P = desc.nPeriods;

  const align16 = (x) => ((-x) % 16 + 16) % 16;
  let off = 16 + descLen + align16(16 + descLen);
  const raw = {};
  for (const f of desc.fields) {
    const n = f.shape.reduce((a, b) => a * b, 1);
    if (f.dtype === 'u8') raw[f.name] = new Uint8Array(buffer, off, n);
    else raw[f.name] = new Uint16Array(buffer, off, n);
    off += f.dtype === 'u8' ? n : n * 2;
    off += align16(off);
  }
  if (off !== buffer.byteLength) throw new Error(`bundle truncated: ${off} of ${buffer.byteLength}`);

  const cache = {};
  const perPeriod = (name) => {
    if (cache[name]) return cache[name];
    const f = desc.fields.find((x) => x.name === name);
    const src = raw[name];
    const flat = new Float32Array(P * C);
    for (let k = 0; k < P * C; k++) {
      const q = src[k];
      flat[k] = q === MISSING ? NaN : f.min + q * f.scale;
    }
    const out = [];
    for (let i = 0; i < P; i++) out.push(flat.subarray(i * C, (i + 1) * C));
    return (cache[name] = out);
  };

  const block = {
    periods: desc.periods, nCells: C, ids: desc.ids,
    lon: desc.lon, lat: desc.lat, areaKm2: desc.areaKm2,
    band: {}, index: {},
  };
  for (const [g, names] of [['band', desc.bands], ['index', desc.indices]]) {
    for (const nm of names) block[g][nm] = perPeriod(`${g}.${nm}`);
  }
  block.validFraction = perPeriod('validFraction');
  block.observed = [];
  for (let i = 0; i < P; i++) block.observed.push(raw.observed.subarray(i * C, (i + 1) * C));
  block.nObs = [];
  for (let i = 0; i < P; i++) block.nObs.push(raw.nObs.subarray(i * C, (i + 1) * C));
  block.descriptor = desc;
  return block;
}

async function gunzip(bytes) {
  const ds = new DecompressionStream('gzip');
  const buf = await new Response(new Blob([bytes]).stream().pipeThrough(ds)).arrayBuffer();
  return buf;
}

(async () => {
  const out = { base, outDir, steps: {} };

  const [manifest, manifestMs] = await t(async () => {
    const res = await fetch(`${base}/${outDir}/manifest.json`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`manifest HTTP ${res.status}`);
    return res.json();
  });
  out.steps.manifest = { ms: Math.round(manifestMs), bytes: 0 };
  if (!manifest.overview) throw new Error('manifest has no overview entry');

  const [bytes, downloadMs] = await t(async () => {
    const res = await fetch(`${base}/${outDir}/${manifest.overview.file}`, { cache: 'no-store' });
    if (!res.ok) throw new Error(`overview HTTP ${res.status}`);
    return new Uint8Array(await res.arrayBuffer());
  });
  out.steps.download = { ms: Math.round(downloadMs), bytes: bytes.length };

  const [raw, inflateMs] = await t(() => gunzip(bytes));
  out.steps.inflate = { ms: Math.round(inflateMs), bytes: raw.byteLength };

  const [block, parseMs] = await t(() => decodeBlock(raw));
  out.steps.parse = { ms: Math.round(parseMs) };

  // The viewer also touches the geometry buffer and the id list; reading them
  // is what a real first paint does.
  const [_, geomMs] = await t(() => {
    let n = 0;
    for (let i = 0; i < block.ids.length; i++) n += block.ids[i].length;
    return n;
  });
  out.steps.ids = { ms: Math.round(geomMs) };

  out.totalMs = Math.round(manifestMs + downloadMs + inflateMs + parseMs + geomMs);
  out.nCells = block.nCells;
  out.nPeriods = block.periods.length;
  out.servedMB = +(bytes.length / 1048576).toFixed(2);
  out.rawMB = +(raw.byteLength / 1048576).toFixed(2);

  process.stdout.write(JSON.stringify(out, null, 2) + '\n');
})().catch((e) => {
  process.stdout.write(JSON.stringify({ error: String(e && e.message || e) }) + '\n');
  process.exit(1);
});
