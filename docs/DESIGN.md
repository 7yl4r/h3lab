# Design — regional multi-source archive

Companion to `PLAN.md`. This describes *how* the system is meant to work, not
what has been agreed to build. Where something is proposed rather than settled,
it says so.

All sizes and volumes here are **order-of-magnitude estimates** and should be
replaced with measurements as soon as Phase 1 produces a first period.

---

## 1. Data flow

```
STAC search
    │
    ▼
scene windows (COG range reads)
    │  cloud / QA mask
    ▼
per-period 30 m mosaic  ◄── the expensive, cacheable artifact
    │
    ├──► sample to H3 res 7/8/9 ──► composite ──► binary shard   (viewer)
    │
    ├──► sample to H3 ──► change detector ──► change fields      (stored)
    │
    └──► sample to H3 ──► monthly aggregation ──► seasonal product (later)

MODIS daily (500 m)
    │
    └──► sample to H3 res 7 ──► daily grid ──► binary shard       (viewer)
```

Two properties matter:

- **The mosaic is the unit of cost.** Everything downstream is resampling.
- **The archive is native.** The 16-day and monthly grids are views computed
  from observations, so changing a grid later does not require re-reading
  scenes.

## 2. Extent and resolutions

Full South Florida bounding box, including Gulf and Atlantic water. Roughly
24.4–27.4 °N, 82.6–80.0 °W → **≈87,000 km²**.

| res | mean cell area | cells over bbox | role |
|---|---|---|---|
| 6 | 36.1 km² | ~2.4k | possible future coarse overview |
| 7 | 5.16 km² | ~16.9k | overview; also the MODIS tier |
| 8 | 0.737 km² | ~118k | primary (Phase 1–4) |
| 9 | 0.105 km² | ~826k | detail (Phase 5) |
| 10 | 0.0150 km² | ~5.8M | on demand only; not planned |

Res 7 is the natural home for MODIS: a res 7 cell is ~2.8 km across, so a
500 m MODIS pixel is averaged over ~30 samples. At res 8 the cell is ~860 m
across and there would be barely three MODIS pixels per cell — not an average,
a resample. **MODIS belongs at res 7 and must not be pushed finer.**

Res 8 is ~860 m across, giving ~28 Landsat 30 m pixels per cell — a genuine
spatial aggregate.

## 3. Temporal model

This is the most delicate part of the design. Read the seasonality section
before changing anything here.

### 3.1 Three time axes

| axis | definition | purpose | stored? |
|---|---|---|---|
| **native** | actual acquisition datetime per observation | ground truth; all analysis | yes |
| **16-day** | continuous bins from a fixed epoch | viewer timeline, event detection | derived, stored per shard |
| **monthly** | calendar months | seasonal decomposition, climatology | derived on demand |

The native store is the archive. The other two are products.

### 3.2 The 16-day grid is continuous, not calendar-anchored

Bins are counted as `floor((date − 2000-01-01) / 16 days)`. There is no annual
reset and no leap-year adjustment. 2000–2026 yields **≈612 bins**.

**Why not calendar-anchored (23 bins per year)?** Landsat's repeat cycle is
exactly 16 days; the calendar year is 365.25. A fixed calendar grid therefore
slides against the satellite by ~2.75 days per year, producing a **~5.8-year
beat** in how many overpasses land in each bin. A bin receives one overpass,
then two, then none. Observation *counts* then oscillate with a period that has
nothing to do with land or weather, and because composite noise depends on
count, that oscillation leaks into variance-based statistics. It is exactly the
kind of artefact that corrupts long-record trend work.

A continuous 16-day grid has the opposite trade: because the bin period equals
the repeat period, **each sensor lands exactly once per bin, indefinitely**.
No beat, no annual discontinuity.

**The cost:** bins precess through the calendar by ~13 days per year. After a
few years, bin index carries no seasonal meaning. See §3.3 — this is safe only
if you follow the rule there.

### 3.3 Seasonality is a first-class requirement

The project lead has stated that seasonality must not be lost. The design
preserves it in three ways:

1. **Native observations are retained**, so no seasonal information is
   discarded at ingest.
2. **A monthly calendar product is a first-class derived view**, not an
   afterthought. Calendar months have a fixed day-of-year phase and no beat,
   which makes them the correct input for STL, climatologies and wet/dry
   comparison.
3. **Every derived bin stores its real centre date**, so any analysis can
   recover day-of-year without reconstructing it.

**Hard rule: never compute a climatology or seasonal phase by
`bin_index mod 23`.** With a continuous grid that expression is meaningless.
Use the stored date, or resample to the monthly product.

The viewer's wet/dry filter operates on **actual dates**, not bin indices. A
"wet season" view selects May–October observations regardless of which bin they
fell in.

**Acceptance check for seasonality:** the wet/dry NDVI amplitude recovered from
the regional archive must match the pilot's magnitude at comparable cells. If
it is smaller, something in the compositing is smoothing the annual cycle.

### 3.4 Compositing

Per cell, per bin, per source family:

- collect valid observations in the bin after QA;
- temporal median (robust to residual cloud);
- record `n_obs` and the source mix.

Do **not** blend different sensor families into one value. Landsat/HLS and
MODIS remain separate series; see §4.3.

## 4. Sources

### 4.1 Primary 30 m: Landsat Collection 2 Level-2

Landsat 5 TM (2000–2011), 7 ETM+ (2000–), 8 OLI (2013–), 9 OLI-2 (2021–), via
the Microsoft Planetary Computer STAC.

C2 L2 is used as the **backbone across the whole record** rather than switching
to HLS at 2013, because one atmospheric-correction chain avoids a processing
discontinuity mid-record.

Known properties:

- Landsat 7 SLC-off from May 2003: ~22% striping. Compositing fills it from
  neighbouring bins; do not treat the resulting gaps as change.
- Landsat 5 ends imaging November 2011; Landsat 8 starts April 2013. That
  17-month window is Landsat 7 only — expect a density dip, label it, do not
  interpolate it away.
- Band centres differ slightly between TM, ETM+ and OLI. See §4.4.

### 4.2 Sentinel-2: HLS S30 (Phase 4)

HLS S30 is NASA's Sentinel-2 product already regressed onto the Landsat band
space. Use it rather than raw ESA L2A; hand-harmonising the two instrument
families is exactly the failure mode this project cannot afford.

**Access caveat — confirmed 2026-10.** The Microsoft Planetary Computer copy of
HLS v2.0 (`hls2-l30`, `hls2-s30`) **only extends back to 2020-01-01**. The
products themselves are older at NASA LP DAAC (L30 from 2013, S30 from late
2015), but reaching that era means a second access path — earthaccess/LP DAAC
rather than the PC STAC — with different chunking, authentication and no
identical STAC API.

That has three consequences for Phase 4:

- The 2015–2019 S30 window needs a separate downloader. Budget for it; do not
  assume the PC client generalises.
- The overlap period (2020+) is where both paths exist, so it is the place to
  validate that they return equivalent values.
- If only the PC path is built, S30 effectively starts in 2020. That still
  helps Irma-era *comparison* not at all, but it does help the 2020s. Decide at
  the start of Phase 4 whether the LP DAAC path is worth it.

S30 is deferred to Phase 4 so the pipeline is proven before the hardest
calibration question is introduced.

### 4.3 Daily: MODIS (Phase 1 onward)

MODIS Terra/Aqua surface reflectance, 500 m, sampled to **res 7**, on a daily
grid with a sentinel for "no valid observation".

Used for **timing** — the only source dense enough to survive a Florida wet
season. One to four bands is sufficient; the plan is to carry enough to derive
NDVI and NDWI at minimum, plus SWIR2 if NBR is wanted.

**MODIS values are never plotted on the same axis as Landsat values.** Different
point-spread function, different atmospheric correction, different spatial
averaging. Comparison happens in anomaly space only (§6.2).

### 4.4 Cross-sensor calibration

Four transitions in a 2000–2026 record: 2013 (L8 joins), 2015 (S2 joins, Phase
4), 2021 (L9 joins), plus the 2003 SLC-off change. Published TM/ETM+ vs OLI
NDVI differences are of order 0.01–0.03 — the same magnitude as a slow decadal
trend.

Protocol, to be run before any trend claim:

1. For each sensor pair, select near-coincident dates (< 8 days apart).
2. Regress per-band reflectance across the AOI against the reference sensor.
3. Apply gains/offsets; record the fit and residuals in the archive metadata.
4. Refuse to publish a trend whose magnitude is below the calibration residual
   for that pair.

This is cheap to compute once the mosaics exist, and it is the difference
between a trend and an inventory of satellite launches.

## 5. Sharding

Static files, no server.

- **Shard key:** H3 res 5 parent (~252 km²), giving ~345 parents over the bbox,
  of which the land/coastal subset is smaller.
- **Per shard:** baked geometry, the 16-day series, and the change fields.
- **Manifest:** lists shards, schema version, epoch, bin count, and a content
  hash per shard so the viewer can detect stale files.

Estimated shard sizes:

| tier | cells/shard | bytes | estimate |
|---|---|---|---|
| res 8, 16-day | ~340 | 340 × 612 × 12 | ~2.5 MB |
| res 7, 16-day | ~49 | 49 × 612 × 12 | ~0.4 MB |
| res 7, MODIS daily | ~49 | 49 × ~9,800 × 4 | ~1.9 MB |
| res 9, 16-day (Phase 5) | ~2,400 | — | ~18 MB |

At these sizes MapLibre handles a shard as a plain GeoJSON source. **Sharding
removes the need for vector tiles**, which was the largest predicted viewer
rewrite. Keep it that way.

## 6. Derived products stored in the archive

### 6.1 Change-point layer

Per cell, per index (NDVI initially):

| field | meaning |
|---|---|
| `break_bin` | bin index of the largest break |
| `break_date` | its centre date, ISO |
| `break_magnitude` | change in index units (post − pre) |
| `break_score` | normalised strength |
| `n_obs_used` | observations entering the fit |
| `method` | algorithm name + version + parameters |

Method:

1. Fit a harmonic regression — linear trend plus 2–3 annual harmonics — on the
   NDVI series. Continuous time, not bin-index modulo.
2. Run CUSUM on the residuals.
3. Report the largest break.

Rationale for harmonic regression over STL: 16-day bins do not divide the year
evenly (22.83/year), so an integer seasonal period is awkward. Continuous-time
harmonics handle the non-integer period directly.

The method and its parameters are part of the stored value. A change layer
without its algorithm is not reproducible and should not be published.

### 6.2 Anomaly fields (for cross-source comparison)

Per cell, per source: mean and standard deviation of the record, so any
observation can be expressed as a z-score against its own source.

This is the only space in which Landsat and MODIS are comparable. A useful
derived signal is **source disagreement**: same cell, same bin, anomalies
pointing opposite ways. That usually means a mixed pixel, a cloud artefact, or
a genuine sub-pixel change one instrument resolves and the other cannot.

## 7. Sizes and compute

### 7.1 Storage

| item | estimate |
|---|---|
| res 8, 16-day, 6 bands uint16 | ~440 MB gzipped |
| res 7, 16-day | ~63 MB |
| res 7, MODIS daily, 4 bands 8-bit | ~200–250 MB |
| change fields | negligible (< 1%) |
| **total, viewable** | **< 1 GB** |

The **mosaic cache is larger than the product**: ~1.16 GB per period at 30 m
(6 bands × 2 B over ~9.7 × 10⁷ pixels), so ~710 GB for the full record and
~27 GB for a single year. This is the artifact worth keeping; it is what makes
res 9 nearly free later.

### 7.2 Compute ranking

The expensive operation is scene reads for the 30 m tier: roughly
**3–4 TB of pixels** for the full record (≈612 periods × 8–18 scene reads ×
~40% of the bbox × 6 bands).

| lever | saving | cost |
|---|---|---|
| fewer years | linear | archive completeness |
| defer S30 | ~40% of post-2015 reads | cross-sensor demo |
| drop blue band | ~17% | blue reflectance in archive |
| fewer scenes | proportional | source diversity |
| smaller extent | linear | ruled out |

**Cheap things that should not be cut for compute reasons:**

- res 7 16-day (~63 MB) — an aggregation of an existing mosaic.
- MODIS daily — ~3% of Landsat pixel volume.
- the change layer — minutes of numerics once the series exists.
- res 7 / res 9 resampling — cheap once the mosaic exists.

**One structural optimisation:** composite to a single common 30 m mosaic per
period, then sample H3 cells from it once. Reprojecting each scene separately
against the hex grid costs 2–4× more for identical output.

## 8. Viewer changes

The pilot viewer assumes one bundle per resolution containing every period for
every cell. The regional viewer needs:

1. **Manifest + shard loading.** Fetch shards intersecting the viewport; keep a
   small LRU cache; show a loading state per shard.
2. **Two-level timeline.** With ~612 frames a single slider is unusable. Use
   year/decade selection plus a 16-day step, and a range brush for animation
   windows ("animate 2016–2019").
3. **Date-based season filter.** Wet/dry presets become filters over actual
   dates, not bin indices.
4. **Change-layer mode.** Colour and rank cells by `break_score`, recency, or
   magnitude; clicking a cell jumps the timeline to `break_bin`.
5. **Source attribution.** Each composite reports which sensor(s) contributed
   and how many observations. Where sources disagree, say so.
6. **Lazy field decode.** Already present in the pilot; keep it. Daily grids at
   res 7 are ~9,800 values per cell per band and must never be fully
   materialised.

## 9. Build pipeline

Stages, each independently re-runnable:

1. `stac` — enumerate scenes per bin and source.
2. `mosaic` — write the per-period 30 m mosaic. **Cache this.**
3. `h3` — sample mosaic → cells, composite, record `n_obs`/source.
4. `change` — harmonic fit + CUSUM per cell.
5. `shard` — write H3 parent shards + manifest.
6. `index` — manifest and viewer bundle metadata.

Rules carried from the pilot:

- **Year-sharded builds.** Reprocess one year without rebuilding 42.
- **Byte-reproducible output.** Fix all timestamps (`gzip mtime=0`).
- **Fail loudly on truncation** rather than returning wrong numbers silently.

## 10. Traps already paid for

Carried from the pilot; do not rediscover them.

- **`(-x) % 16` differs between JavaScript and Python.** Python returns
  non-negative, JavaScript sign-follows. This silently misaligned every binary
  field.
- **`f.shape[0] * f.shape[1]` is `NaN` for a 1-D array.** Reduce shapes
  generically.
- **Inverted y-axes make "above/below" comparisons negative.** A guard
  `yHi - yLo > threshold` silently drew nothing. Compare absolute gaps.
- **A triplet is not a CSS colour.** Colours must be hex or functional
  notation, or the browser renders black without error.
- **Fixed-height containers ignore smaller heights.** Overflow lands on the
  next row.
- **Errors that draw nothing are invisible.** Element-count assertions in the
  browser catch these; code review does not.
