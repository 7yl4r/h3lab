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

**Measured 2026-10-21** (centre-inside the bbox, same `contains` rule as
`grid.py`): res 4 = **37** cells (the region/shard unit, D-21), res 5 = 330,
res 7 = **18,115**, res 8 = **128,236**. The res 7/8 counts confirm the
order-of-magnitude figures above.

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

**The check is a comparison, and the comparison needs matched windows.** The
two products do not use the same seasons:

| | wet | dry |
|---|---|---|
| pilot (`config.SEASONS`) | 1 May – 31 Oct | 1 Nov – 31 Dec |
| regional (`bins.is_*_season_date`) | May – Oct | Nov – Apr |

Comparing a Nov–Dec pilot composite against a Nov–Apr regional mean folds two
extra months of a different season into the regional side and makes the
regional look damped when it is not. The check therefore applies the
**pilot's** calendar to the regional's bins. Measured on the 2017 build at
the 351 cells the two products share (D-28):

| quantity | pilot | regional |
|---|---|---|
| wet NDVI (May–Oct 2017), median | +0.600 | +0.597 (ratio 0.995) |
| dry NDVI (Nov–Dec 2017), median | +0.568 | +0.559 (ratio 0.985) |
| per-cell wet − dry | — | correlation with the pilot **+0.887** |
| per-cell seasonal amplitude (max − min) | +0.174 | +0.229 (ratio 1.31) |

The regional cycle is not damped: on the seasonal means it matches the pilot
to within 1.5 %, on the per-cell contrast it correlates at +0.89, and its
amplitude is *larger* than the pilot's — the pilot's amplitude is a 13-year
max − min, which averages out extremes that a single year of noisier 16-day
bins reaches more readily.

### 3.4 Compositing

Per cell, per bin, per source family:

- collect valid observations in the bin after QA;
- temporal median (robust to residual cloud);
- record `n_obs` and the source mix.

Do **not** blend different sensor families into one value. Landsat/HLS and
MODIS remain separate series; see §4.3.

**Order of operations (regional build).** The pilot took the *spatial mean*
inside a cell first, then the *temporal median* across a season's scenes. The
regional build goes **mosaic-first** (D-16): the per-pixel temporal median is
formed first, then the spatial mean inside each cell. For the ~2 observations a
16-day bin actually holds these are effectively identical, but the meaning of
`n_obs` changes — it is now *contributing scenes per pixel*, not scenes per
composite. Do not silently compare the two conventions.

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

- **Shard key:** H3 res 4 region, each carrying the **res-8** cells inside it.
  The **res-7 overview is a single unsharded bundle** for the whole bbox.
  (D-21 supersedes the res-5 key.)
- **Per shard:** baked geometry, the 16-day series, and the change fields.
- **Manifest:** lists shards, schema version, epoch, bin count, and a content
  hash per shard so the viewer can detect stale files.

**The region count is 72, not 37.** The number 37 is the count of res-4 cells
whose hexagon lies *entirely* inside the bbox. The res-8 grid is built by the
same whole-hexagon rule, so its cells reach into res-4 parents that the bbox
clips, and those parents are real regions too. Grouping the res-8 cells by
their actual res-4 parent (`h3.cell_to_parent`) gives **72 shards covering
100 % of the res-8 grid**; dropping the clipped parents instead would throw
away 31 % of the area the mosaic was just computed for. The keys are H3's
own parent indices, so the two conventions disagree for about 6 % of the
cells whose centre falls just outside their parent's polygon (see §10).

Measured shard sizes (2017, 23 bins, res 8):

| tier | cells/shard | measured |
|---|---|---|
| res 8, 16-day (region) | 28 – 2,401 (mean 1,781) | 72 shards, 0.01 – 1.27 MB each (56.3 MB total) |
| res 7, 16-day (overview, one file) | 18,115 | one file, 8.21 MB |
| res 9, 16-day (Phase 5) | ~2,400 | unchanged estimate |

The whole 2017 regional product is therefore **64.5 MB served** — the overview
plus all 72 shards — which is what the viewer's cold-load budget is spent on.
At these sizes MapLibre handles a shard as a plain GeoJSON source.
**Sharding removes the need for vector tiles**, which was the largest predicted
viewer rewrite. Keep it that way.

## 6. Derived products stored in the archive

### 6.1 Change-point layer

Per cell, per index (NDVI initially):

| field | meaning |
|---|---|
| `break_bin` | bin index of the largest break, or "none" |
| `break_date` | its centre date (stored as days since the epoch) |
| `break_magnitude` | change in index units (post − pre), on the residuals |
| `break_score` | partial F statistic of the step regressor |
| `n_obs_used` | observations entering the fit |
| `method` | algorithm name + version + parameters |

Three things about these numbers are worth stating plainly, because all three
are properties of the estimator rather than of the data.

**The magnitude is a residual step.** The regression has already removed the
trend and the seasonal cycle, so `break_magnitude` is the difference between
the two segments *after* that removal, not the raw difference in NDVI. The raw
difference is not a usable measure at all: it is swamped by the seasonal
curve, and a cell whose season rises 0.4 while its break adds 0.2 reports a
raw difference of 0.6. The fitted step is close to the real one — on simulated
series it recovers a +0.20 step as 0.196 — so the residual step is the
quantity to store, and the raw difference is the one to throw away.

**The score is a partial F, not a standardised excursion.** For each
candidate break the model is refitted with a step regressor, and the step is
scored by the partial F statistic of that regressor. The first version of the
detector used Page's CUSUM on the residuals instead. Both rank candidate
breaks by the same quantity, but the CUSUM divides the mean residual
difference by the spread of the *seasonal-only* residuals, while the F divides
by the spread *after* the step has been removed. At a real break the second is
much the smaller number, and the separation is far better: on 23-bin series
with a seasonal amplitude of 0.35 and AR(1) noise, at a 5% false-positive
rate, a +0.20 step is detected in 78% of series by the F-scan and in none by
the CUSUM, and a +0.40 step in 100% against none.

**The score is scale-free, so it needs a threshold.** The scan runs over ~20
candidate breaks, which makes the null wider than a single test's, so the
threshold is a quantile of a *simulated* null — series with the same seasonal
shape and AR(1) noise and no step, scanned the same way. Measured null
quantiles for 23 bins, 2 harmonics, AR(1) φ = 0.4 (600 simulations): median
7.2, p95 15.9, p99 21.1. The default threshold is the 99% quantile, i.e. a
1% per-cell false-positive rate; a cell below it stores "no break" and a cell
above it stores its best candidate. Without a threshold every cell with
enough observations reports a break and the field carries no information.

Method:

1. Fit a harmonic regression — linear trend plus 2–3 annual harmonics — on the
   NDVI series. Continuous time, not bin-index modulo.
2. For every interior candidate break, refit with a step regressor and score
   the step with a partial F-test.
3. Report the candidate with the largest F, if it clears the threshold.

Rationale for harmonic regression over STL: 16-day bins do not divide the year
evenly (22.83/year), so an integer seasonal period is awkward. Continuous-time
harmonics handle the non-integer period directly.

The method and its parameters are part of the stored value. A change layer
without its algorithm is not reproducible and should not be published.

#### Measured on the 2017 build

The numbers below are the detector's behaviour on the real res-8 regional
series (128,236 cells × 23 bins, 2017), measured with
`tools/measure/validate_change.py` and `tools/measure/change_power.py`. They
are the honest characterisation of the layer, and they are also its limits.

**Coverage first.** A cell can only show a break it was observed through. Of
the 128,236 res-8 cells, the median cell has 14 of 23 bins (p10 6, p90 21) and
only 1,787 cells were observed in all 23. 99.2 % of cells have at least one
observation after the middle of the year, so a step in the second half is
visible to almost every cell — but only weakly.

**Power against an injected step**, injected at period 12 (bin 400) and
measured as "found at the right bin":

| true step | of all cells | of cells with ≥ 22 obs | of cells with all 23 obs | magnitude estimate (median) |
|---|---|---|---|---|
| +0.10 | 0.6 % | 1.5 % | 2.6 % | **+0.205** |
| +0.20 | 3.6 % | 11.3 % | 19.1 % | **+0.257** |
| +0.40 | 14.4 % | 46.9 % | 71.6 % | **+0.423** |

Two things follow, and both matter for how the field may be used.

*The estimator over-states a small step and is nearly unbiased on a large
one.* A +0.10 step comes out at +0.205; a +0.40 step comes out at +0.423.
This is a selection effect, not a fitting error: a magnitude is stored only
for cells that cleared the threshold, and the cells that clear it are the ones
with the largest noise, which inflates the step they appear to have. The
inflation is worst for the steps that are barely detectable, which is exactly
where a user would reach for the number. The stored magnitude is a *relative*
signal — a −0.22 next to a +0.20 is meaningful, a +0.205 next to a +0.10 is
not.

*Power is a coverage question as much as a sensitivity question.* A +0.20
step is found for 3.6 % of all cells but 19.1 % of the cells that saw the whole
year. The region-wide number is diluted by cells that were never observed
after the step; the per-fully-covered-cell number is the honest sensitivity.

**The threshold is calibrated, and the calibration holds.** On the
shuffled-residual null — the fitted series with its residuals permuted, so it
keeps the real noise but loses the ordering — 1,030 of 128,236 cells (0.80 %)
are flagged, against the 1 % the threshold claims.

**But the real series flags 4.5 %, and that gap is not a bug in the
calibration.** 5,760 cells carry a break on the real 2017 series against 0.80 %
on the shuffled null: a 3.7-point excess. The shuffled null is a null of *no
step in a series whose residuals are exchangeable*; the real series has
*ordered* residuals that are not steps — an asymmetric phenology, a rain
event, a cloud-driven dip in one bin — and the scan cannot tell those from a
step, because a one-bin dip in a 23-point series *is* a step, statistically.
An attempt to explain the excess by seasonal misfit (a single year's cycle is
not a two-harmonic sinusoid) does not hold: with 3 harmonics the real flag
rate falls only from 4.69 % to 4.21 % on a 25,000-cell subsample, while the
null's false-positive rate *rises* to 1.30 % and the power of a +0.20 step
*falls* from 3.64 % to 2.58 %. Two harmonics is kept, and the excess is
recorded here as a property of the layer rather than tuned away.

The practical reading: a break in this layer is a *candidate*, and a
candidates-only layer is what Phase 1 promised. The break distribution is not
uniform either — the three autumn bins 404, 406 and 407 carry 2,559 of the
5,760 breaks, with bin 406 (2017-10-22) the largest at 988 and the Irma bin
404 (2017-09-20) close behind at 784 — and the Rookery Bay shard has 91 of its
205 breaks in the Irma bin alone, which is the first sign that the field is
picking up something real and not only noise. Ranking Irma-affected cells
without being told the date is Phase 2's acceptance test, and it has seven
annual cycles to work with.

### 6.2 Anomaly fields (for cross-source comparison)

Per cell, per source: mean and standard deviation of the record, so any
observation can be expressed as a z-score against its own source.

This is the only space in which Landsat and MODIS are comparable. A useful
derived signal is **source disagreement**: same cell, same bin, anomalies
pointing opposite ways. That usually means a mixed pixel, a cloud artefact, or
a genuine sub-pixel change one instrument resolves and the other cannot.

## 7. Sizes and compute

### 7.0 Measured inputs (2026-10-21)

The Phase 1 "first task" — usable observations per cell per year — is now
measured from the STAC catalogue rather than guessed:

- 2017, Landsat 7 + 8: **383 scenes** across the 23 bins, **9–18 per bin**
  (mean 16.7). Every bin has scenes; none is empty.
- Grid counts, using H3's own "whole hexagon inside the bbox" rule: res 4 =
  37 (fully contained cells), res 5 = 330, res 7 = **18,115**, res 8 =
  **128,236**. Grouping the res-8 cells by res-4 parent gives **72 regions**
  (§5).
- Scene acceptance is by **intersection** (D-24), so the ~17 scenes per bin
  are the whole path/row set over the bbox, not one scene that happens to
  cover it.

The pilot's own cache cannot answer the density question: `MAX_SCENES_PER_COMPOSITE
= 12` saturates every wet composite. The product-size estimates below are now
measured rather than estimated.

### 7.1 Storage

Measured on the 2017 build (23 bins), not estimated. The full-record column
scales the 2017 figure by the 612 / 23 = 26.6× bin ratio, which is what a
2000–2026 archive costs at the same design.

| item | 2017 measured | full record (612 bins) |
|---|---|---|
| res 8, 16-day, 6 bands uint16 + change fields | 56.3 MB (72 shards) | ~1.50 GB |
| res 7, 16-day | 8.21 MB (one file) | ~218 MB |
| change fields | included in the shards above | included |
| **total, viewable** | **64.5 MB** | **~1.7 GB** |
| per-cell composite cache (`npz`, build accelerator) | 70.4 MB | ~1.9 GB |

**The mosaic cache is an order of magnitude larger than the product, and that
is the number that has to be planned against.** Measured: 15.27 GB for the 23
bins of 2017 — a mean of **664 MB per bin** (259 – 817 MB) — which scales to
**~406 GB for the full 612-bin record**. Deflate buys only about 1.75× over
the raw uint16 DN (1.16 GB/period), because deflate does not exploit the
16-bit structure of the DN values.

That does not fit D-22's 30 GB budget, and it was never going to: the budget
covers **one year of the cache**, not the record. Two consequences:

- The 2017 cache (15.27 GB) is inside 30 GB with room to spare, so Phase 1
  and any single-year experiment fit the budget as stated.
- A full-record build needs ~400 GB of mosaic cache, or a retention policy
  that keeps the archive (1.7 GB) and rebuilds a bin's mosaic on demand.
  Rebuilding is cheap in storage and expensive in network: one bin is 664 MB
  of reads.

The mosaic is worth keeping for the years being worked on. It is not worth
keeping for the whole record unless the machine has the disk.

### 7.1a Hosting: what the published site is

The viewers are static files and the archive is static files, so the whole
thing is a GitHub Pages site — there is no server in the read path. The site
is **75.1 MiB** today: 61.6 MiB of regional archive (overview + 72 shards +
manifest) and 13.4 MiB of pilot bundles. The mosaic cache (15.3 GiB) and the
npz composite cache (70.4 MiB) are deliberately *not* published; they are
build accelerators and the pipeline regenerates them.

That size is comfortable, but it is not constant, and two of GitHub's limits
are on a trajectory to meet it. Measured per bin from the 2017 build
(2.7 MiB per bin, of which the overview is 349 KiB and the largest shard
54 KiB):

| limit | binding file | reached at |
|---|---|---|
| 1 GB published site | the whole archive | 328 more bins — **14.4 years** |
| 100 MB per file | `overview_res7.bin.gz` | 257 more bins — **11.3 years** |
| 100 MB per file | largest res-8 shard | 1,781 bins — 78 years |
| 100 GB/month bandwidth (soft) | res-7 overview per cold load | ~12,200 loads/month |

**The overview is the file that breaks first, and that is a direct
consequence of D-21.** Making the res-7 overview one unsharded bundle keeps
cold load to a single fetch (and the 3 s budget comfortable), and its cost is
that it carries every cell for every period: it scales as `cells × periods`,
while each shard scales as `cells_in_shard × periods` and is 72× smaller.
Past ~11 years the overview has to be split by year. Doing that also fixes a
second, quieter problem: because a shard holds every period, adding a year
rewrites all 74 files, so git history grows quadratically even though the
working tree grows linearly. Per-year payloads make each year's files
immutable and the history linear.

`tools/measure/site_budget.py` measures all of the above against a site tree
and `--check` fails on either limit; `.github/workflows/pages.yml` runs it
before every deploy, so a record that outgrew GitHub Pages fails the build
rather than publishing a truncated site (D-30).

The deployed prototype is `https://tylar.info/h3lab/` (project site on the
user site's custom domain; the `github.io` name redirects). Measured from
outside: 74 archive files all 200, nothing outside the assembled `site/` tree
reachable, and a 698 ms cold load of the res-7 overview over the network —
the local-server figure is 441 ms, so transit is the difference and parsing
is not the constraint.

**Overflow path.** When the record outgrows the per-file or site limit, the
agreed move is an object store (a Google Cloud bucket) for `data/regional/`,
with the site keeping only the HTML. This is cheap because the read path has
exactly one place that knows where the data lives — `BASE` in
`viewer/regional.html` (and `STEM` in `viewer/pilot.html`). Nothing else
assumes same-origin, so the switch is an absolute URL plus a CORS policy on
the bucket; the manifest and block format do not change. Splitting the
overview by year remains the fix that keeps everything on Pages, and the two
are compatible: the bucket simply holds the per-year files.

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

- res 7 16-day (218 MB for the full record) — an aggregation of an existing mosaic.
- MODIS daily — ~3% of Landsat pixel volume.
- the change layer — minutes of numerics once the series exists.
- res 7 / res 9 resampling — cheap once the mosaic exists.

**One structural optimisation:** composite to a single common 30 m mosaic per
period, then sample H3 cells from it once. Reprojecting each scene separately
against the hex grid costs 2–4× more for identical output.

**Measured cold load (2017, res-7 overview):** 386 ms end to end — manifest
73 ms, download 56 ms, inflate 182 ms, parse 73 ms — against the 3 s budget.
7.83 MB served, 12.8 MB inflated. The download is not the bottleneck and
neither is the parse; inflating the bundle is the largest single term, and
it is the term that grows with the record (the overview is one file holding
every cell, so its size is linear in the bin count). The measurement moves a
little between runs — 463 ms on a re-run, of which 254 ms is the inflate —
so the honest figure is *under half a second*, and the margin against the
3 s budget is a factor of six, not a factor of eight.

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
4. `change` — harmonic fit + step-regressor F-scan per cell.
5. `shard` — write H3 parent shards + manifest.
6. `index` — manifest and viewer bundle metadata.

Rules carried from the pilot:

- **Year-sharded builds.** Reprocess one year without rebuilding 42.
- **Byte-reproducible output.** Fix all timestamps (`gzip mtime=0`).
- **Fail loudly on truncation** rather than returning wrong numbers silently.

## 10. Traps already paid for

Carried from the pilot; do not rediscover them.

- **A ≥ 95 % AOI-coverage test does not scale.** The pilot accepts a scene only
  when it covers 95 % of the ~310 km² AOI. No Landsat scene covers the 87,000 km²
  regional bbox, so that test rejects every scene at regional extent. Accept on
  **intersection** and record the covered fraction (D-24).

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

### 10.1 Regional build traps (Phase 1)

- **A SAS token expires long before the build finishes.** Planetary Computer's
  signed asset URLs last about 45 minutes; a 2017 regional build takes about
  3.5 hours. A URL cached at the start of the build is dead by the middle of
  it, and the failure looks like "no data" rather than like an auth error —
  every mosaic came back empty. Cache the **unsigned** URL and sign it at
  read time, and surface a signing failure as an error rather than as an
  empty band.

- **A child's centroid can lie outside its parent's polygon.** H3 builds a
  child's geometry from its parent's, so for about 6 % of res-8 cells the
  centre falls 0.5–0.9 km outside the res-4 hexagon that contains the cell's
  area. `h3.latlng_to_cell(centre, 4)` then returns the *neighbouring* res-4
  cell, while `h3.cell_to_parent(cell, 4)` returns the right one. Group by
  `cell_to_parent`, never by a point lookup.

- **`np.flatnonzero` on an array of indices returns the wrong cells.** A
  boolean mask and a list of row indices are both "a selection", and
  `flatnonzero` silently treats the second as the first: index 0 is dropped
  and every later cell shifts by one. The two cases need different code.

- **A ratio index is not bounded by its own range.** `(a − c) / (a + c)` is
  only in [−1, 1] when `a` and `c` do not straddle zero. Over dark water
  Collection 2's −0.2 offset puts one band near zero and the other clearly
  negative, and NDVI of −200 came out of it. The same-sign rule is what
  actually bounds the ratio; a denominator epsilon alone does not.

- **Per-cell containment tests do not scale to 250,000 cells.** The pilot's
  shapely `contains` test per hexagon takes minutes at res 8. H3's own
  rasteriser applies the same rule (`contain="full"`) in about a tenth of a
  second.

- **Days-since-epoch fits in a u16 only until 2179.** `break_date` is stored
  as days since 2000-01-01 to keep it an integer that survives the block
  format; 2017 is ~6,300–6,600 days, and the field has to be widened before
  the archive reaches 2179-10-19.

- **A hung HTTP read is a stalled build, not a slow one.** GDAL's HTTP driver
  has no default timeout, so one range request that never returns freezes a
  worker indefinitely — measured: 13 minutes with zero bytes read and every
  thread parked. Set `GDAL_HTTP_TIMEOUT` so the read fails and is retried
  instead.

- **A file on disk is not a finished file.** A build that is interrupted
  mid-write leaves a truncated GeoTIFF behind, and the next run — which
  decides what to reuse from what is on disk — takes it for a cache hit and
  builds a mosaic with a hole in it. Write each output to a temporary name
  and rename it into place, and check a cached output by opening it and
  reading its last block, not by looking at its name.
