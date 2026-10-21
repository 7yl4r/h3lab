# Project plan — South Florida multi-source land-surface record

**Status:** planning / pre-build. No work on the regional archive has started.
**Last updated:** 2026-10-21
**Code:** this repository (`h3lab/`), which currently contains a finished
Rookery Bay pilot. The regional build extends it.

This file is the entry point. Read it first, then `docs/DESIGN.md` for the
technical model and `docs/DECISIONS.md` for why things are the way they are.

---

## 1. What this project is

The WetCoast work is planned as **two separate tools**:

| tool | question it answers | status |
|---|---|---|
| **A. Identification** | *Where and when did something change?* | this repository |
| **B. Characterisation** | *What happened during this known event?* | not started |

This repository is **tool A**. Its job is to surface candidate events and
trends across a long record so a human can decide what deserves a closer look.
Tool B will take a known event and do detailed work on it.

The existing Rookery Bay viewer is the prototype of tool A: a hexagon map where
each cell can be read as a biography.

## 2. Goals

These are stated objectives, not inferences. Keep them in mind when trading
scope.

- **Identify temporal events and trends** across satellite sources. This is the
  primary purpose of tool A.
- **Keep temporal resolution as high as possible**, specifically so events can
  be detected rather than smoothed away. Reducing cadence for analysis is
  acceptable; losing it in the archive is not.
- **Cross-satellite analysis is a value add** and an explicit differentiator.
  Multiple sources should corroborate one another, not merely coexist.
- **Highlight NASA data.** Prefer NASA products and make provenance visible.
- **Preserve seasonality.** Wet/dry cycling is a primary signal, not noise.
  Nothing in the temporal design may destroy it.
- **Support downstream analysis**, including seasonal decomposition and trend
  work planned for later.
- **Cover all of South Florida**, as a full bounding box rather than a
  hand-drawn AOI.
- **Eventually publish the archive itself** as a reusable artifact.

## 3. What exists today

A working pilot over Rookery Bay NERR (~310 km²):

- Landsat Collection 2 Level-2 surface reflectance from the Microsoft
  Planetary Computer STAC.
- H3 resolutions 8, 9, 10; 20,607 cells at res 10.
- 2013–2025, two seasons per year, 26 composites.
- Six reflectance bands and six spectral indices (NDVI, NDWI, MNDWI, NDMI,
  NBR, SAVI). **No land-cover classification anywhere** — that machinery was
  deliberately removed; this is a spectra-and-indices product.
- Gzipped, quantised uint16 binary bundles with baked hexagon geometry.
  13.4 MB total, ~0.65 s cold load.
- A MapLibre viewer with a density-anomaly heatmap, spectral profile,
  spectral-stability layer, and a ranked-cell panel.

Details and hard-won traps are in `README.md`.

## 4. Target system

A regional, multi-source, 26-year record served as sharded static files.

| property | target |
|---|---|
| extent | full South Florida bbox, ≈87,000 km² including water |
| period | 2000–2026 (hard floor 2000; see D-06) |
| primary resolution | H3 res 8 (~118k cells) |
| secondary | res 7 overview (~16.9k), res 9 later (~826k) |
| operational cadence | continuous 16-day bins, ≈612 periods |
| seasonal cadence | monthly calendar bins, derived from native |
| 30 m sources | Landsat C2 L2 (L5/L7/L8/L9) + HLS S30 (2015+, Phase 4; 2020+ via PC) |
| daily source | MODIS at res 7 |
| derived layer | per-cell change-point statistics, stored |
| delivery | H3 res-5 shards + manifest, on object storage |

Sizes are order-of-magnitude estimates; see `docs/DESIGN.md` §7.

## 5. Phases

Compute shares are approximate fractions of the full 2000–2026 build.

### Phase 0 — pilot (complete)

Rookery Bay, 2013–2025, 26 composites, three resolutions.

**Delivered:** the binary format, the viewer, the stability layer, the heatmap
and spectral profile, the tracking and reproduction story.

**Not delivered, and not required:** regional scale, multi-source, high
cadence.

### Phase 1 — regional vertical slice

**Goal:** prove every new mechanism on the cheapest possible unit of work.

- Extent: full bbox.
- Period: **2017 only** (23 bins). Chosen because it contains Hurricane Irma,
  so the imagery for the eventual acceptance test exists.
- Sources: Landsat 7 + 8 only. No Sentinel-2 yet.
- Resolution: res 8, plus a res 7 overview derived from the same mosaic.
- Build the per-period 30 m mosaic cache, the H3 aggregation, the change-layer
  schema, and the shard writer.
- Viewer: two-level timeline and shard loading.

**Compute:** ~4% of the full build.

**Exit criteria — do not proceed until all pass:**

1. All 23 periods build reproducibly (byte-identical across two runs).
2. The res 8 bundle loads in the viewer with correct geometry registration.
3. The wet/dry cycle is visible in the NDVI series — a direct seasonality
   check.
4. Cold load under 3 s for the regional overview.
5. The change-layer fields are present in the schema, even if the detector is
   still rough.

### Phase 2 — seven-year prototype

**Goal:** a record long enough for honest seasonal modelling and a real event.

- Period: **2015–2021** (~161 bins, seven annual cycles).
- First touches Landsat 9, commissioned in late 2021; the full L9 era arrives
  in Phase 3. Also exercises the L7→L8 chain across the whole window.
- MODIS daily at res 7 across the same years.
- Change-point detector run for real, NDVI only.

**Compute:** ~26% cumulative.

**Exit criteria:**

1. **Acceptance test:** the change layer ranks Irma-affected cells highly
   *without being told the date*, and the daily MODIS tier places the
   disturbance in early September 2017.
2. Seasonal amplitude recovered from the regional archive matches the pilot's
   magnitude at comparable cells.
3. No spurious step at the 2021 Landsat 9 transition (see D-11 and
   DESIGN §4.4).
4. A human can identify at least one previously unnoticed candidate event.

### Phase 3 — full backfill

- Add 2000–2014 and 2022–2026; complete the 2000–2026 record (~612 bins).
- Expect a density dip in 2011–2013 (Landsat 5 ends Nov 2011, Landsat 8 starts
  Apr 2013; only Landsat 7 SLC-off in between). Label it; do not "fix" it.

**Compute:** remaining ~74% of the Landsat 30 m work.

### Phase 4 — second sensor family

- Add HLS S30 (Sentinel-2) for 2015 onward.
- Requires empirical cross-calibration against the Landsat backbone and a
  source flag on every observation.
- This is where cross-satellite corroboration becomes real.
- **Access caveat:** the Planetary Computer's HLS copy starts in 2020, not
  2015. Reaching 2015–2019 needs a second access path (LP DAAC/earthaccess).
  Decide at the start of the phase whether that is worth building — see
  `docs/DESIGN.md` §4.2.

**Compute:** roughly +40% on the post-2015 Landsat reads.

### Phase 5 — finer resolution

- Add res 9.
- Because resampling happens from the cached 30 m mosaic, this is mostly
  **storage, not compute**. Keep that separation.

### Later / deferred

See §7.

## 6. Decisions already made

Full log with rationale in `docs/DECISIONS.md`. Summary:

- Full South Florida bounding box, water included.
- 2000 start; no pre-2000 work unless it proves necessary.
- 16-day operational cadence, continuous and epoch-anchored.
- Native observations retained; 16-day and monthly are derived views.
- res 8 first; res 9 later.
- Landsat-only for Phases 1–3; Sentinel-2 in Phase 4.
- MODIS daily at res 7 for event timing.
- Never blend different sensors in value space; compare in anomaly space.
- Change-point statistics live in the archive.
- Archive packaging/publication deferred.

## 7. Deferred / explicitly not now

Do not start these without revisiting the plan:

- **VIIRS.** A later version. MODIS alone spans the whole record.
- **Pre-2000 (Landsat 4/5 from 1982/1984).** No daily tier, sparse single-sensor
  coverage, extra sensor transitions. Reversible later because the build is
  year-sharded and the archive native.
- **Archive packaging** — GeoParquet, data dictionary, DOI, publication. The
  project lead has asked to optimise for this tool's usage first and return to
  packaging later. The *change layer* is still stored (see D-14).
- **Tool B (characterisation).** A separate product.
- **Seasonal decomposition and trend products.** Planned follow-up analyses;
  the archive must support them, but they are not built here.
- **Reprocessing the pilot AOI at higher cadence.** Not needed; the regional
  build supersedes it.

## 8. Open questions

Tracked here until answered. Each blocks or shapes a phase.

1. **Does MODIS-only suffice for the daily tier in Phase 2, or does VIIRS need
   to come earlier?** Current plan: MODIS only.
2. **How many observations per cell per year does South Florida actually
   yield after cloud rejection?** Estimate used in sizing is 25–35/year. The
   pilot's cached data can measure this before Phase 1 starts.
3. **Does the change detector generalise beyond NDVI in Phase 4+?** Currently
   NDVI only. Adding indices multiplies detector work.
4. **Is the res 7 16-day tier worth keeping once res 7 MODIS daily exists?**
   Current leaning: yes, they answer different questions (30 m-derived overview
   vs 500 m daily timing). Confirm during Phase 1.
5. **What is the target publication venue/format for the archive?** Deferred,
   but it will constrain schema choices later.
6. **Which events besides Irma form the acceptance set?** One is enough for
   Phase 2; more strengthen validation.

## 9. Picking this up

Read, in order:

1. This file.
2. `docs/DESIGN.md` — how the system is meant to work.
3. `docs/DECISIONS.md` — why, and what was rejected.
4. `README.md` — the pilot's technical narrative, performance notes and traps.

To reproduce the pilot:

```bash
cd h3lab
export PYTHONPATH=$PWD/src
python3 -u -m h3lab.run --years 2013:2025 --resolutions 8,9,10 --export
./serve.sh          # viewer at http://127.0.0.1:8765/viewer/
```

**First task for Phase 1:** measure usable observations per cell per year from
the pilot's cached composites, then replace the sizing estimate in
`docs/DESIGN.md` §7 with a measured number. Everything downstream depends on
that figure.

## 10. Documentation map

| file | contents |
|---|---|
| `PLAN.md` | goals, phases, scope, deferred work, open questions |
| `docs/DESIGN.md` | extent, grids, temporal model, sources, tiers, computes, viewer |
| `docs/DECISIONS.md` | dated decision log with alternatives and consequences |
| `README.md` | the pilot: format, viewer internals, performance, traps |

## 11. Known documentation debt

`README.md` predates the removal of land-cover classification. Its title and
opening sections still describe a "land-cover change" product and mention
fractional cover and H3 res 11, none of which the code does any more. The code
and these planning documents are correct; the README's framing needs a pass.
