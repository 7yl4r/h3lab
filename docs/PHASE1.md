# Phase 1 — regional vertical slice: implementation plan

**Status:** implemented; all five exit criteria pass.
**Last updated:** 2026-10-21
**Read with:** `PLAN.md` §5 (phases), `docs/DESIGN.md` (how the system works),
`docs/DECISIONS.md` (why). This file is the working plan for Phase 1 only and
does not re-decide anything already settled; decisions taken while writing it
are appended to `DECISIONS.md` as D-21 onward.

> **Corrections found while executing this plan.** The body below is the plan
> as written; five of its numbers were wrong and are corrected in
> `DESIGN.md`/`DECISIONS.md` rather than edited here, so the plan still reads
> as the plan:
>
> - **Regions: 72, not 37.** 37 res-4 cells lie wholly inside the bbox, but
>   the res-8 grid reaches into 35 more res-4 parents that the bbox clips.
>   Grouping by `cell_to_parent` gives 72 shards and full coverage (D-21).
> - **Mosaic size: ~250–600 MB per bin, not 1.16 GB.** The cache stores raw
>   uint16 DN, not float32 (D-25), so a 2017 cache lands near 6–14 GB —
>   inside the 30 GB budget (D-22). The measured figure came out higher,
>   664 MB a bin and 15.3 GB for the year, and the budget turned out to be
>   per-year rather than per-record (D-29).
> - **Scene acceptance needed the intersection test** before the first scene
>   could be read at all (D-24).
> - **The change detector scores a step with a partial F, not a CUSUM.** The
>   plan's statistic (§6.4, §10) fires on essentially nothing: a standardised
>   CUSUM on the residuals never reached its own threshold on a simulated
>   +0.20 step, so the layer carried no information at all. The step-regressor
>   F-scan finds 78% of +0.20 steps and 100% of +0.40 steps at a 5%
>   false-positive rate, and its threshold is a quantile of a simulated null
>   rather than a constant. `DESIGN.md` §6.1 has the numbers; the fields, the
>   semantics and the plan's conclusions are unchanged. On the real 2017
>   data the layer flags 4.5% of cells against a 0.80% false-positive rate on
>   the shuffled null, and finds a +0.20 step at the right bin for 3.6% of all
>   cells but 19.1% of the cells that saw the whole year — so the layer is a
>   *candidates* layer, which is what Phase 1 asked for, and its power is a
>   coverage question as much as a sensitivity one.
> - **Exit criterion 3 is a comparison, not a bar.** §11 row 3 asks for the
>   regional's wet/dry amplitude "vs the pilot's at comparable cells", and
>   D-18 rule 4 asks whether the regional is *damped*. A bar of the form
>   "wet NDVI must exceed dry NDVI" cannot answer that: at Rookery Bay the
>   pilot's dry composite is higher than its wet composite in 13 of 13 years,
>   so such a bar would fail a correct build. The check is now the
>   pilot-vs-regional comparison, on the pilot's own season windows (D-28).
>
> The build is also staged rather than monolithic (D-26): only the `mosaic`
> stage touches the network, so a code change costs minutes to re-run rather
> than hours.
>
> **Where the build landed** (2017, 23 bins, res 8 + res 7 overview):
> 383 scenes; 23 mosaics; 128,236 res-8 cells in 72 shards plus an 18,115-cell
> res-7 overview; 64.5 MB served; under half a second cold load against a 3 s
> budget; 5,760 cells (4.5 %) carry a change break. The measured mosaic cache
> is 15.27 GB for the year — 664 MB a bin — so the full 612-bin record needs
> ~400 GB of it, against 1.7 GB of archive (D-29).

---

## 1. Scope

Phase 1 proves every new regional mechanism on the cheapest unit of work.

| property | value |
|---|---|
| extent | full South Florida bbox `(-82.6, 24.4, -80.0, 27.4)` ≈ 87,000 km² |
| period | **2017 only → 23 continuous 16-day bins** (bins 388–410) |
| sources | **Landsat 7 + 8 only** (no Sentinel-2, no MODIS) |
| resolution | **res 8** primary + **res 7** overview, both from the same mosaic |
| new mechanisms | per-period 30 m mosaic cache · H3 aggregation from the mosaic · change-layer schema · region shard writer · viewer two-level timeline + shard loading |
| compute | ~4% of the full 2000–2026 build |

2017 is chosen because it contains Hurricane Irma (10 Sep 2017), so the imagery
for the eventual Phase 2 acceptance test exists.

**Exit criteria — do not proceed to Phase 2 until all pass:**

1. All 23 periods build reproducibly (byte-identical across two runs).
2. The res-8 region shard loads in the viewer with correct geometry registration.
3. The wet/dry cycle is visible in the NDVI series.
4. Cold load under 3 s for the regional (res-7) overview.
5. The change-layer fields are present in the schema, even if the detector is
   still rough.

---

## 2. The gap to close

The pilot bins **per scene → per cell**: mean within a cell, then a temporal
median across the ~12 scenes of a season. The regional design (`DESIGN.md` §1,
D-16) requires a **mosaic-first** flow:

```
stac    enumerate L7/L8 scenes per 16-day bin, true-footprint test, cached manifest
          │
mosaic  per-bin common 30 m GeoTIFF over the bbox, per-pixel temporal median + n_obs
          │   (the expensive, cacheable artifact)
          │
h3      sample mosaic → res 7 + res 8 cells; per-cell mean, validFraction, nObs
          │   (per-period npz cache, mirrors the pilot's composite cache)
          ├──► change   harmonic fit + CUSUM on res-8 NDVI → break fields
          │
shard   res-7 single overview bundle  +  res-8 region shards
          │
index   manifest (schema version, epoch, bin list, per-shard hash)
```

None of `stac`, `mosaic`, the mosaic-based aggregation, the change layer, or the
shard writer exist yet. They are the "new mechanisms" Phase 1 must prove.

---

## 3. Decisions locked (2026-10-21)

Recorded as D-21…D-24 in `DECISIONS.md`.

- **Disk.** Up to 30 GB is approved; if the build exceeds it the machine is
  upgraded. This covers a **retained, compressed** mosaic cache plus the
  per-period cell caches. No build-and-discard.
- **Runs.** Overnight runs are fine; the full 2017 build may run unattended.
- **Region model.** Shards are regions. **Region = H3 res 4** (~1,770 km²,
  **37 regions** over the bbox). Region shards carry **res 8**; a single
  **res-7 overview shard** covers the whole bbox. See §7.
- **Sequencing.** Build and validate the **Rookery Bay region shard first**, so
  Irma is the test case, then roll the remaining regions.

### Hardware recommendation (proposed — confirm with the machine)

A full-bbox period is only **96 M px**. One band as float32 is 0.38 GB, six
bands 2.3 GB, and a six-band stack over ~6 scenes is ~14 GB. **At ≥64 GB RAM the
mosaic stage can read scenes whole and compute the median directly, which
removes the only genuinely error-prone new code in the build** (the tiled /
windowed read path). The plan below is written band-at-a-time so it also runs in
32 GB, but 64 GB is the simpler machine to target.

---

## 4. Measurements taken during review

These replace the order-of-magnitude estimates flagged in `DESIGN.md` §7. They
are measurements of *inputs* (STAC and grid), not yet of built products.

### 4.1 Observation density — the Phase 1 "first task"

2017, Landsat 7 + 8, cloud ≤ 85 %, per WRS path/row, via the Planetary Computer
STAC:

| path/row | scenes | bins with data | mean scenes/bin |
|---|---|---|---|
| 016/042 (Rookery Bay) | 43 | 22 / 23 | 1.95 |
| 015/042 (Florida Bay) | 44 | 23 / 23 | 1.91 |
| 015/043 | 45 | 23 / 23 | 1.96 |
| 017/041 | 41 | 23 / 23 | 1.78 |

**≈1.9 acquisitions per cell per 16-day bin** (about one L7 + one L8), present in
22–23 of 23 bins → **≈44 pre-cloud observations/cell/year** for 2017. After
cloud rejection the 25–35/year working figure in `DESIGN.md` §7 is supported.

This also confirms D-08 directly: with a continuous 16-day grid each sensor lands
almost exactly once per bin, so there is no calendar beat. The pilot's own cache
**cannot** answer this question — `MAX_SCENES_PER_COMPOSITE = 12` caps every wet
composite at 12 scenes, so the cached `n_obs` saturates. A STAC count is the
correct instrument.

### 4.2 Grid and region counts

Centre-inside the bbox, computed with the same `contains` rule as `grid.py`:

| res | cells / regions | edge | role |
|---|---|---|---|
| 4 | **37** | 26.1 km | region (shard) |
| 5 | 330 | 9.9 km | (superseded as the shard key by res 4 — see D-21) |
| 7 | **18,115** | 1.41 km | overview |
| 8 | **128,236** | 0.53 km | primary |

Rookery Bay's centre falls in res-4 parent `844413dffffffff`.

### 4.3 Environment

Python 3.13, all direct dependencies import, Planetary Computer STAC reachable
from the dev machine.

---

## 5. Temporal model

A new `bins.py` is the single source of truth for the time axis:

```
EPOCH    = 2000-01-01
bin_index(date)  = (date - EPOCH).days // 16
bin_centre(b)    = EPOCH + (b*16 + 8) days
2017 → bins 388..410   (23 bins)
```

Every derived period stores its **true centre date**. Hard rule from D-18: never
compute phase or climatology as `bin_index mod 23`. The viewer's wet/dry filter
selects on actual dates, not bin indices.

---

## 6. Pipeline stages

Each stage is independently re-runnable and caches its output.

### 6.1 `stac` — enumerate scenes per bin

- Enumerate Landsat C2 L2 for 2017, platforms L7 + L8, bucketed by bin.
- **Acceptance test changes.** The pilot's `_scene_covers_aoi` requires the scene
  to cover ≥ 95 % of the AOI (`ingest.py`). No Landsat scene covers 87,000 km²,
  so that test rejects **every** scene at regional extent. Replace it with an
  **intersection** test: accept a scene whose true footprint intersects the bbox,
  and record the fraction covered.
- Keep the pilot's rule that the STAC `geometry` is a swath envelope, not a
  footprint: open the raster header and use the real bounds.
- Drop the 12-scene cap (bins hold ~2 scenes); sort deterministically
  (cloud, then datetime, then id) for reproducibility.

### 6.2 `mosaic` — one 30 m mosaic per bin

- Target grid: EPSG:32617 (UTM 17N; the bbox spans ±1.6° of the central
  meridian, ~0.03 % scale error), 30 m, ≈ 8,667 × 11,100 px ≈ 96 M px per band.
- For each bin: read each contributing scene warped to the target grid
  (`WarpedVRT`, nearest — no resampling at 30 m → 30 m), apply the QA mask, and
  take the **per-pixel temporal median**, recording n-obs per pixel.
- **Read one band at a time** to bound memory (~2–3 GB), so 32 GB works; a 64 GB
  machine could hold the whole stack.
- Store as a **tiled, deflate-compressed GeoTIFF** in raw uint16 DN (lossless;
  `SR_SCALE/SR_OFFSET` applied at H3 sampling), plus an n-obs/valid raster.
  Compression matters: much of the bbox is dark water.
- Determinism: fixed scene order, fixed median tie-break, `gzip mtime=0`.

### 6.3 `h3` — sample the mosaic into cells

- Build label rasters for res 7 and res 8 on the one canonical analysis grid
  (`grid.py` already does this; it reads once and bins into all resolutions).
- Per period: bin mosaic pixels into cells → per-cell band mean, `validFraction`,
  `nObs`; save a per-period npz cache (mirrors the pilot's `composite.py`).
- **Semantics change to document:** the mosaic is a per-pixel temporal median
  before the spatial mean, whereas the pilot took the spatial mean before the
  temporal median. For ~2 observations these are effectively identical, but
  `nObs` now means *contributing scenes per pixel*, not scenes per composite.
  Note this in `DESIGN.md` §3.4.

### 6.4 `change` — change-point layer

- res-8 NDVI only, initially.
- Fit a harmonic regression (linear trend + 2–3 annual harmonics) on continuous
  time; CUSUM on the residuals; report the largest break.
- Store `break_bin`, `break_date`, `break_magnitude`, `break_score`,
  `n_obs_used`, `method` (algorithm name + version + parameters) per D-14.
- See §10 for why this is deliberately rough at 23 bins.

### 6.5 `shard` — region shards + manifest

- Group cells by **H3 res-4 parent** (region); res-8 cells inside it.
- Per region shard: baked geometry, the 23-period series, and the change fields.
- Manifest: schema version, epoch, bin list, and a content hash per shard so the
  viewer can detect stale files.
- Reuse `binary.py`'s quantised block format; extend it with the change-layer
  field types and the epoch/bin dates in the descriptor.

### 6.6 `index` — viewer metadata

One manifest plus the res-7 overview bundle, and the per-region res-8 shards.

---

## 7. Sharding and delivery model

| tier | unit | cells | periods | est. size |
|---|---|---|---|---|
| overview | one file, whole bbox, res 7 | 18,115 | 23 | ~12 MB raw → ~4 MB gz |
| region | one shard per H3 res-4 parent, res 8 | ~3,500 | 23 | ~2 MB |

(~3,500 = 128,236 res-8 cells ÷ 37 regions. At ~29 bytes/cell/period across the
6 bands, 6 indices, validFraction, observed and nObs.)

- The viewer loads the **res-7 overview** first → the sub-3 s cold load, then
  fetches the **res-8 region shards** intersecting the viewport with a small LRU
  and a per-shard loading state.
- 37 region shards is "several regions" and matches the proposal's RECOVER-area
  framing. Granularity is a one-line change if it needs revisiting.
- This supersedes `DESIGN.md` §5's res-5 shard key (D-10); see D-21.

---

## 8. Code changes

| file | change |
|---|---|
| `bins.py` | **new** — bin↔date math, `bins_for_year()`, wet/dry date predicates |
| `config.py` | add regional bbox, `EPOCH`, `BIN_DAYS=16`, `H3_RESOLUTIONS=(7,8)`, `REGION_RES=4`, `PLATFORMS=("landsat-7","landsat-8")`; keep pilot defaults intact |
| `stac.py` | **new** — per-bin enumeration; intersection acceptance; platform filter; deterministic order |
| `mosaic.py` | **new** — per-bin 30 m mosaic builder + cache |
| `aggregate.py` | generalise `ingest.aggregate_scene` to bin an arbitrary raster stack (mosaic), not one scene |
| `change.py` | **new** — harmonic regression + CUSUM; the stored break fields |
| `shard.py` | **new** — res-4 region grouping, shard + manifest writer, content hashes |
| `binary.py` | add change-layer field types; carry epoch/bin dates in the descriptor |
| `export_region.py` | **new** — res-7 overview bundle + res-8 region shards + manifest |
| `run.py` | staged entry point: `stac → mosaic → h3 → change → shard → index` |
| `viewer/regional.html` (+ shared JS) | manifest + shard loading, two-level timeline, date-based season filter, change mode; reuse the biography heatmap and spectral profile |

---

## 9. Blockers and traps found in review

Carried from the pilot plus new ones found while planning.

1. **Coverage test rejects the region.** `ingest._scene_covers_aoi` requires
   ≥ 95 % AOI coverage; unusable at bbox extent. Hard blocker → §6.1.
2. **`find_scenes` is season-scoped and capped at 12.** Per-bin enumeration wants
   all scenes in the bin; the cap is moot and should be dropped.
3. **`read_scene` returns full-raster arrays.** 96 M px × 6 bands cannot be read
   whole on a small machine; use band-at-a-time (§6.2).
4. **Compositing order and `nObs` semantics change** with mosaic-first (§6.3).
   Document, do not leave implicit.
5. **`grid.build_grid` derives its disk radius from `AOI_BBOX`** and uses
   `contains`; verify it still yields 37 res-4 / 18k res-7 / 128k res-8 cells for
   the regional box before trusting downstream counts.
6. Carried pilot traps (DESIGN §10): `(-x) % 16` sign, 1-D shape reduction,
   inverted-axis guards, triplet-as-CSS-colour, fixed-height overflow, silent
   "draws nothing" bugs.

---

## 10. Change detector: why it is rough in Phase 1

The detector is a harmonic regression (linear trend + 2–3 annual harmonics) with
CUSUM on the residuals. At 2017 alone it has almost nothing to work with:

1. **Parameter budget.** 23 bins; intercept + trend + harmonics consume 6–8,
   leaving ~15 residual points.
2. **Seasonality and the event are collinear.** One year is exactly one annual
   cycle, and the harmonics are free to absorb a step near a seasonal turn.
   **Irma lands at the end of the wet season** — the worst phase for detection.
3. **Too few post-event bins.** 10 Sep → 31 Dec is ~4–5 bins, ~2 obs each.
4. **CUSUM power scales with segment length** on both sides of the candidate
   break; one year gives short segments on both sides.
5. **No background rate.** A single year offers no non-event baseline to ask
   "is this break unusual?" — the same limitation the pilot README flags for
   Irma ("not detectable in the mean").
6. **NDVI only**, so no cross-index corroboration.

So Phase 1 asserts only that the **fields exist and round-trip** (exit criterion
5). The detector becomes real in **Phase 2** (2015–2021, ~161 bins, seven annual
cycles), which is where the Irma acceptance test — "ranks Irma-affected cells
highly without being told the date" — actually runs.

---

## 11. Exit criteria → tests

| # | criterion | test |
|---|---|---|
| 1 | byte-identical rebuild | run `mosaic → shard` twice; `md5sum` every shard + manifest; `gzip mtime=0` |
| 2 | geometry registration | res-8 centroids inside the bbox and equal to an independent `h3` computation; res-7 vs res-8 nested-cell alignment assert; visual coastline overlay |
| 3 | seasonality | regional 2017 NDVI wet/dry amplitude vs the pilot's at comparable cells (D-18 acceptance check) |
| 4 | cold load < 3 s | `viewer/probe.html` timing on the res-7 overview |
| 5 | change schema | assert every res-8 shard descriptor carries `break_bin/date/magnitude/score/n_obs_used/method` |

---

## 12. Work order

1. `bins.py` + config; unit-test bin↔date and that 2017 = 23 bins.
2. `stac.py` with the corrected intersection test; enumerate 2017; expect ~382
   items over 23 bins.
3. `mosaic.py` for **one bin** end-to-end; compare against a single-scene read;
   measure time and disk.
4. `aggregate.py` + H3 sampling → per-period npz for one bin; verify res-7/8
   nesting.
5. Run all 23 bins (overnight); check reproducibility.
6. `change.py` (NDVI) + `shard.py` + manifest, region = res 4.
7. Build the **Rookery Bay region shard** for 2017 end-to-end; check Irma against
   the pilot's known mangrove-loss cells.
8. Viewer: manifest + overview/region-shard loading, two-level timeline,
   date-based season filter, change mode.
9. Run the five exit-criteria tests; replace `DESIGN.md` §7 estimates with
   measured values; fix the remaining README/HTML framing.
10. Roll the remaining ~36 regions.

---

## 13. Risks and open items

1. **Wall-clock.** ~380 scene reads (all 2017 path/rows, L7 + L8), each warped to
   a 96 M px grid — hours to ~a day. Overnight, accepted.
2. **Mosaic cache size.** ~1.16 GB/period raw × 23 ≈ 27 GB raw; compressed,
   estimated 7–14 GB. Within the 30 GB budget, but keep the compressed COG
   default.
3. **Landsat 7 SLC-off striping** (~22 % from 2003): compositing fills it from
   L8. Label it, do not interpolate it away (D-06, DESIGN §4.1).
4. **Region granularity** is set to res 4 (37 regions) by D-21; revisit if the
   shard size or count proves wrong once real products exist.
5. **Change detector is rough by construction** at one year (§10); not a defect.
