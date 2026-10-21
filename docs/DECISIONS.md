# Decision log

Dated, append-only. Each entry records what was decided, what was rejected, and
what it costs. Add new entries at the bottom; do not rewrite old ones except to
mark them superseded.

Statuses: **accepted** (agreed by the project lead), **proposed** (engineering
recommendation, awaiting sign-off), **deferred** (explicitly postponed),
**superseded**.

---

## D-01 — Use H3 hexagons as the analysis grid
*2026-10 · accepted · pilot*

**Decision.** All analysis and delivery is on Uber H3 cells rather than a raster
grid. Resolutions 8, 9 and 10 in the pilot.

**Why.** Cell area is explicit and reportable, aggregation is exact, and scale
becomes an experimental variable — whether a signal survives aggregation is
itself a result. A 30 m pixel grid encodes none of this.

**Consequence.** Geometry must be baked or generated per shard, and cell counts
grow ~7× per resolution step.

---

## D-02 — Landsat Collection 2 Level-2 as the primary source
*2026-10 · accepted · pilot*

**Decision.** Surface reflectance from Landsat C2 L2 via the Microsoft
Planetary Computer STAC.

**Why.** NASA/USGS product, 30 m, consistent processing across L5–L9, open
access. Meets the "highlight NASA data" objective without a bespoke atmospheric
correction.

**Rejected.** HLS-only (`hls2-l30`). **Verified 2026-10:** the Planetary
Computer collection is HLS v2.0 and starts **2020-01-01**, so it cannot cover
Hurricane Irma or the earlier record. (HLS itself goes back to 2013/2015, but
only via LP DAAC/earthaccess — see DESIGN §4.2.) Once past 2013 the backbone is
still C2 L2, so the record has one processing chain throughout.

---

## D-03 — Remove land-cover classification entirely
*2026-10 · accepted · pilot*

**Decision.** The product is six reflectance bands and six spectral indices.
No hard classes, no fractional cover, no unmixing.

**Why.** Classification obscured the measurement and added a large failure
surface. Spectra and indices are what the science actually needs, and they are
recomputable from the archive.

**Consequence.** `README.md` still contains stale land-cover framing and a res
11 mention. Code and planning docs are correct; the README needs a pass.

---

## D-04 — Quantised uint16 binary bundles, gzipped, geometry baked
*2026-10 · accepted · pilot*

**Decision.** One `.bin.gz` per resolution plus a small JSON index.

**Why.** 13.4 MB total and ~0.65 s cold load for the pilot. JSON or GeoJSON of
the same data would be an order of magnitude larger and far slower to parse.

**Consequence.** A bespoke decoder is required. Acceptable now; revisited in
D-15 when the archive is packaged for external use.

---

## D-05 — Full South Florida bounding box, water included
*2026-10 · accepted · project lead*

**Decision.** Extend from the Rookery Bay AOI (~310 km²) to the full regional
bounding box, ~87,000 km², including Gulf and Atlantic water.

**Why.** Avoid a hand-drawn AOI that encodes assumptions about where change can
happen. Water is also part of the system being studied (Florida Bay, the reef
tract, red tides).

**Alternative rejected.** Land-and-nearshore mask. Roughly 3× smaller, but it
would silently exclude the coastal water the indices are partly designed for.

**Consequence.** ~118k cells at res 8, ~826k at res 9. Sharding becomes
mandatory (D-10).

---

## D-06 — Start at 2000, not 1984
*2026-10 · accepted · project lead*

**Decision.** The record begins in 2000. Pre-2000 Landsat is deferred
indefinitely.

**Why.** Before 2000 there is no daily tier, coverage is a single sparse
16-day sensor, and two extra sensor transitions would have to be calibrated.
The cost is high and the payoff uncertain.

**What is lost.** The 1980s and 1990s, including Hurricane Andrew (1992).

**Reversibility.** The build is year-sharded and the archive native, so adding
1984–1999 later is a backfill, not a redesign.

---

## D-07 — 16-day operational cadence
*2026-10 · accepted · project lead*

**Decision.** The viewer timeline and the primary derived product use 16-day
steps, not monthly.

**Why.** Matches the Landsat repeat cycle, preserves phenology that monthly
smooths, and gives ~2.4× the temporal resolution of monthly at a storage cost
that is affordable.

**Rejected.** Monthly (smooths events and phenology); fortnightly (costs ~87%
of native density while discarding ~87% of acquisition dates).

---

## D-08 — Continuous, epoch-anchored 16-day bins
*2026-10 · accepted, conditional on D-18*

**Decision.** Bins are `floor((date − 2000-01-01) / 16 days)`. No annual reset,
no year alignment. ~612 bins for 2000–2026.

**Why.** A calendar-anchored grid (23 bins/year) slides against the 16-day
Landsat repeat by ~2.75 days/year, producing a **~5.8-year beat** in
observations per bin. Because composite noise depends on observation count,
that beat leaks into variance and trend statistics. With a continuous grid each
sensor lands exactly once per bin indefinitely.

**Cost.** Bins precess through the calendar by ~13 days/year, so bin index
carries no seasonal meaning. See D-18 — this is only acceptable because
seasonality is served by a separate monthly product and by stored dates.

---

## D-09 — Native observations are the archive; grids are views
*2026-10 · accepted*

**Decision.** Store every valid observation with its acquisition datetime and
source. The 16-day and monthly grids are derived.

**Why.** Binning is irreversible. Keeping native data means the analysis
cadence can change without re-reading scenes — which matters because the
project explicitly wants to "reduce temporal resolution as needed for analysis"
without losing it in the archive.

**Consequence.** Native storage is ~2.2× the 16-day product. Acceptable.

---

## D-10 — Shard by H3 res-5 parent; no vector tiles
*2026-10 · accepted · project lead*

**Decision.** Static files keyed by H3 res 5 (~252 km²), plus a manifest.

**Why.** A res-5 shard holds ~340 res-8 cells — small enough for MapLibre to
consume as plain GeoJSON, and small enough to fetch per viewport. This solves
payload and rendering with one mechanism.

**Consequence.** The predicted PMTiles build step is unnecessary. Keep shards
small enough that this stays true.

---

## D-11 — Landsat C2 L2 is the backbone; HLS S30 is added later
*2026-10 · accepted (phasing proposed)*

**Decision.** Use C2 L2 for Landsat across the entire record, including after
2013. Add HLS S30 for Sentinel-2 in Phase 4.

**Why.** One atmospheric-correction chain avoids a processing discontinuity at
2013. HLS S30 is already harmonised into the Landsat band space, so it only has
to agree with Landsat over the years the two overlap.

**Rejected.** Switching to HLS L30 at 2013 (introduces a mid-record processing
change); raw ESA S2 L2A (requires hand-harmonisation, the exact failure mode
this project cannot afford).

**Consequence.** TM/ETM+/OLI bandpass differences remain and must be handled
empirically — see DESIGN §4.4.

---

## D-12 — MODIS daily at res 7 for event timing
*2026-10 · accepted (phasing proposed)*

**Decision.** A daily MODIS tier sampled to H3 res 7.

**Why.** It is the only source dense enough to survive a Florida wet season,
and wet season is when the events of interest occur. It is also NASA data,
which the project explicitly wants to foreground. Computationally it is ~3% of
the Landsat pixel volume.

**Constraint.** Res 7, never finer: at res 8 a 500 m pixel gives barely three
samples per cell.

**Constraint.** Never plotted against Landsat values on a shared axis — see
D-13.

---

## D-13 — No cross-sensor blending in value space
*2026-10 · accepted*

**Decision.** Different sensor families are never combined into one composite
or compared on a shared value axis. Comparison happens in anomaly space
(z-score against each source's own climatology for that cell).

**Why.** MODIS and Landsat differ in point-spread function, atmospheric
correction and spatial averaging. Their value difference is an instrument
property, not land change. Plotting them together manufactures change.

**Consequence.** The archive carries per-source means and standard deviations,
and can report **source disagreement** — a signal in its own right.

---

## D-14 — The change-point layer lives in the archive
*2026-10 · accepted · project lead*

**Decision.** Break statistics are stored per cell alongside the observations,
not computed only in the viewer.

**Why.** The archive is intended to support analyses beyond this tool, and a
derived layer that exists only in a browser is not an archive.

**Consequence.** Each stored break must carry the algorithm name, version and
parameters. An unexplained derived field is not reproducible and must not be
published.

---

## D-15 — Archive packaging and publication deferred
*2026-10 · accepted · project lead*

**Decision.** Do not build the publication format (GeoParquet, data dictionary,
DOI, external documentation) yet. Optimise first for this tool's usage.

**Why.** Packaging decisions constrain schema in ways that are better made once
the archive's shape is settled by a working prototype.

**Deferred, not rejected.** D-14 still applies: the change layer is stored now,
even though the container is a bespoke binary.

---

## D-16 — One common 30 m mosaic per period
*2026-10 · accepted · project lead*

**Decision.** Build a single mosaic per 16-day period, then sample H3 cells
from it once.

**Why.** Reprojecting each scene separately against the hex grid costs 2–4× more
for identical output.

**Consequence.** The mosaic cache becomes the largest artifact (~1.16 GB per
period, ~710 GB for the full record) and should be retained: it makes res 9 and
res 7 nearly free to add later.

---

## D-17 — res 8 first, res 9 later
*2026-10 · accepted · project lead*

**Decision.** The prototype ships res 8 only. res 9 is Phase 5.

**Why.** res 8 is ~118k cells and ~440 MB; res 9 is ~826k cells and ~5 GB.
Since resampling from the cached mosaic is cheap, res 9 becomes a storage
decision rather than a compute one — safe to defer.

---

## D-18 — Seasonality is a first-class derived product
*2026-10 · accepted · project lead*

**Decision.** A monthly calendar product is maintained as a first-class view,
and every derived bin stores its true centre date.

**Why.** The project lead has stated seasonality is "hugely important" and must
not be lost. Continuous 16-day bins (D-08) precess through the calendar, so
they cannot carry seasonal phase on their own.

**Rules that follow:**

1. Never compute climatology or phase as `bin_index mod 23`.
2. The viewer's wet/dry filter selects on actual dates.
3. Seasonal decomposition uses the monthly calendar product (fixed phase) or
   continuous-time harmonic regression (handles the non-integer annual period).
4. Acceptance check: regional wet/dry NDVI amplitude must match the pilot's at
   comparable cells. If it is damped, compositing is smoothing the cycle.

---

## D-19 — Two tools, split by question
*2026-10 · accepted · project lead*

**Decision.** Tool A identifies events ("where and when did something change?");
Tool B characterises a known event. This repository is Tool A.

**Why.** Identification wants breadth, density and cheap screening. 
Characterisation wants depth, precision and per-event custom handling. Building
both in one product would compromise both.

**Consequence.** Scope decisions here should favour breadth: more area, more
time, cheaper per-cell summaries — not deeper per-cell analysis.

---

## D-20 — No VIIRS in the prototype
*2026-10 · accepted · project lead*

**Decision.** MODIS alone provides the daily tier. VIIRS is a later version.

**Why.** MODIS spans the whole record with one pipeline and one QA scheme.
VIIRS adds density only after 2012, for a second product and a second QA path.

**Consequence.** Daily density improves notably from 2012 in a later revision.

---

## D-21 — Shard by H3 res-4 region; res-7 is one overview shard
*2026-10-21 · accepted · project lead · region count corrected 2026-10-21*

**Decision.** The detail tier is sharded by **H3 res 4** parent, each shard
carrying the **res-8** cells inside it. The **res-7 overview is a single
unsharded bundle** covering the whole bbox.

**Region count: 72, not 37.** The original 37 was the number of res-4 cells
whose hexagon lies *entirely* inside the bbox. The res-8 grid is built by the
same whole-hexagon rule, so its cells reach into res-4 parents that the bbox
clips; grouping the res-8 cells by their actual parent (`h3.cell_to_parent`)
gives **72 shards covering 100 % of the res-8 grid**. Dropping the clipped
parents instead would discard 31 % of the area the mosaic was computed for.

**The shard key is `cell_to_parent`, never a point lookup.** About 6 % of res-8
cells have a centroid that falls outside their res-4 parent's polygon, so
`h3.latlng_to_cell(centre, 4)` returns the neighbouring res-4 cell for them.

**Why.** "Shards are regions." res 4 yields a handful of regions that match the
proposal's RECOVER-area framing and keeps a shard small enough for MapLibre as
plain GeoJSON. One overview file is the simplest way to hit the < 3 s cold load
at the current bin count.

**Supersedes.** D-10's res-5 shard key (~330 shards). res 5 is still useful as an
internal grouping but is too fine to call a "region."

**Rejected.** res 5 as the region (too many to be "several"); res 3 (3 regions,
too coarse to fetch per viewport).

**Consequence.** `DESIGN.md` §5's shard table changes; the viewer fetches one
overview file then per-region res-8 shards. Granularity is a one-line change.

---

## D-22 — Retain the mosaic cache; budget the build machine for it
*2026-10-21 · accepted · project lead (RAM sizing proposed)*

**Decision.** Keep the per-period 30 m mosaic (D-16) as a **compressed** GeoTIFF.
Budget **30 GB** of disk, with a machine upgrade if the build exceeds it.

**Why.** Retaining the mosaic is what makes res 9 and res 7 nearly free later
(D-16, D-17). A raw mosaic is ~1.16 GB/period (~27 GB for 2017); deflate
compression over a mostly-water bbox brings the estimate to 7–14 GB.

**Proposed, awaiting the machine.** Target **≥ 64 GB RAM**. A full-bbox period is
only 96 M px (2.3 GB for six float32 bands), so ample RAM lets the mosaic stage
read scenes whole and take the median directly, **removing the tiled/windowed
read path** — the most error-prone new code in the build. The plan is written
band-at-a-time so 32 GB also works; 64 GB is simply simpler.

**Consequence.** No build-and-discard; the mosaic is a first-class artifact.

---

## D-23 — Build region-first, starting with Rookery Bay
*2026-10-21 · accepted · project lead*

**Decision.** Build and validate the **Rookery Bay region shard** (res-4 parent
`844413dffffffff`) for 2017 end-to-end before rolling the remaining 71 regions.

**Why.** It is the smallest region with a known event: the pilot already shows
Hurricane Irma mangrove loss at Rookery Bay, so the first shard can be checked
against an existing result. It surfaces every unknown on the cheapest unit of
work, consistent with the Phase 1 goal.

**Consequence.** Phase 1 delivers a working region shard + overview early, then
expands. Region order after Rookery Bay is unconstrained.

---

## D-24 — Regional scene acceptance is intersection-based, not full-AOI coverage
*2026-10-21 · accepted · engineering*

**Decision.** At regional extent, accept a scene when its true footprint
**intersects** the bbox and record the covered fraction. Do not require the scene
to cover ≥ 95 % of the AOI.

**Why.** The pilot's `_scene_covers_aoi` requires ≥ 95 % AOI coverage. No Landsat
scene covers 87,000 km², so that test rejects **every** scene for the regional
bbox — a hard blocker. The 95 % rule was correct for a ~310 km² AOI and wrong for
a regional one.

**Consequence.** Mosaic assembly must combine several path/rows per period. The
STAC `geometry`-is-a-swath-envelope trap still applies: the footprint comes from
the raster header, not the catalogue geometry.

---

## D-25 — The mosaic cache stores raw uint16 DN; the SR transform is applied at sampling
*2026-10-21 · accepted · engineering*

**Decision.** Write the per-period 30 m mosaic as a tiled, deflate-compressed
GeoTIFF holding the **raw uint16 digital numbers**, and apply
`SR_SCALE`/`SR_OFFSET` when the mosaic is sampled into H3 cells.

**Why.** Storing DN is lossless: the scaling is two multiplies and an add, so
doing it later costs nothing, while storing scaled floats would round-trip
through a float and bloat the cache. It also makes the cache directly
comparable with the source products, which is what D-09's "the archive is
native" principle asks for. The QA mask is the only screen applied at build
time: a pixel is valid when its DN is non-zero *and* its QA says clear.

**Consequence.** `mosaic.py` writes DN; `aggregate.py` scales. Measured: a
2017 bin is **~250–600 MB** on disk depending on how much of the bbox the
scenes actually cover, against D-16's ~1.16 GB estimate for raw float32. The
cache stays inside D-22's 30 GB budget with room to spare.

---

## D-26 — The regional build is a staged pipeline with per-stage disk caches
*2026-10-21 · accepted · engineering*

**Decision.** Split the regional build into six stages — `stac`, `mosaic`,
`h3`, `change`, `shard`, `index` — each of which leaves its result on disk and
can be re-run on its own. Only the `mosaic` stage touches the network.

**Why.** The mosaic stage reads every scene of every bin, which for a year of
South Florida is tens of gigabytes and several hours; everything after it is
arithmetic over files already on disk. Separating them means a rebuild after a
code change costs minutes instead of hours, and it puts the expensive stage
behind an explicit, inspectable cache. It also makes the stages independently
testable, which is what the exit criteria need.

**Consequence.** `python -m h3lab.run --regional --year 2017 --stage ...`
runs any subset of the stages in dependency order. Stage boundaries double as
the module boundaries: `bins`, `stac`, `mosaic`, `aggregate`, `change`,
`shard`, `export_region`. The pipeline reverses the pilot's order in exactly
one place — mosaic first, then sample (D-16) — and the reason is physical, not
cosmetic: the temporal median has to be taken per *pixel*, before pixels are
pooled into cells.

---

## D-27 — The change detector scores a step with a partial F, not a CUSUM
*2026-10-21 · accepted · engineering*

**Decision.** Detect breaks by scanning every interior candidate break with a
step regressor added to the harmonic model, and score each candidate with the
partial F statistic of that regressor. Calibrate the detection threshold from
a *simulated* null (same seasonal shape, AR(1) noise, no step, scanned the
same way) at a stated per-cell false-positive rate, rather than using a fixed
constant.

**Why.** The first implementation ran Page's CUSUM on the harmonic residuals,
which the plan specified. Both statistics rank candidate breaks by the same
quantity — the difference in mean residual before and after the break — so
they choose the same break, but they normalise it differently: the CUSUM
divides by the spread of the *seasonal-only* residuals, the F by the spread
*after* the step has been removed. At a real break the second is much the
smaller number, and the separation between "there is a break" and "there is
not" is far better. Measured on 23-bin series with a seasonal amplitude of
0.35 and AR(1) noise, at a 5% false-positive rate: a +0.20 step is detected
in 78% of series by the F-scan and in none by the CUSUM; a +0.40 step in
100% against none. The CUSUM layer carried no information at all, which
satisfies the exit criteria and nothing else.

The threshold is simulated rather than analytic for two reasons. The scan
over ~20 candidate breaks makes the null wider than a single test's, and
GDAL-independent simulation of the *whole* scan gets that right without a
multiplicity correction; and the residuals of a real NDVI series are
autocorrelated, which an F-table's independence assumption does not allow
for.

**Consequence.** `break_score` is a partial F statistic, not a standardised
excursion, and the detection threshold is a property of the method rather
than a constant in the source: the method string records the harmonics, the
trend, the null's AR(1) coefficient, the number of simulations, the seed and
the false-positive rate, so a stored layer is reproducible from its own
descriptor (D-14). The fitted step is close to the true step (0.196
estimated for a 0.20 step), so `break_magnitude` remains a *residual* change
— the raw difference of means is swamped by the seasonal curve and is not
stored. One year of data still limits what is detectable: the powers above
are for the F-scan's own calibration, and the acceptance test that matters —
ranking Irma-affected cells without being told the date — stays in Phase 2,
where there are seven annual cycles to work with.

**Measured on the 2017 regional build** (128,236 res-8 cells × 23 bins).
Calibration holds: on the shuffled-residual null the layer flags 0.80 % of
cells against the 1 % the threshold claims. Power, measured as "found at the
right bin" for a step injected at bin 400, is a coverage question as much as
a sensitivity one — 0.6 % / 3.6 % / 14.4 % of all cells for a +0.10 / +0.20 /
+0.40 step, against 2.6 % / 19.1 % / 71.6 % of the 1,787 cells that saw the
whole year. The stored magnitude is inflated for small steps (+0.205 for a
true +0.10) and nearly unbiased for large ones (+0.423 for a true +0.40),
because a magnitude is stored only for cells that cleared the threshold and
those are the noisiest cells. The real series flags 4.5 % against 0.80 % on
the null: the excess is *ordered* real residual structure that is not a step —
a one-bin dip in a 23-point series is a step, statistically — and not
seasonal misfit, since 3 harmonics move the real flag rate only from 4.69 %
to 4.21 % while raising the null rate to 1.30 % and cutting the +0.20 power
from 3.64 % to 2.58 %. Two harmonics is kept and the excess is documented
rather than tuned away: `DESIGN.md` §6.1 has the tables. The layer is a
*candidates* layer, which is what Phase 1 asked for.

---

## D-28 — The seasonality check compares against the pilot on the pilot's calendar
*2026-10-21 · accepted · engineering*

**Decision.** Exit criterion 3 is the comparison D-18 rule 4 names, not an
absolute bar: the regional's wet/dry NDVI separation is measured at the cells
it shares with the pilot, on the **pilot's** season windows (wet = 1 May –
31 Oct, dry = 1 Nov – 31 Dec), and the test is that the regional is not
*damped* relative to the pilot. Concretely: the seasonal-mean ratios within
15 %, the per-cell wet − dry correlation at or above 0.5, the seasonal
amplitude ratio at or above 0.8, and a median per-cell amplitude of at least
0.10 so the cycle is visible at all.

**Why.** The two products do not use the same seasons. The pilot composites
per season — `config.SEASONS` gives wet = May–Oct and dry = Nov–Dec, and
`ingest.find_scenes` uses Nov–Dec of the same year — while the regional build's
dry season is everything outside the growing season, November through April.
An earlier version of the verifier compared the regional's Nov–Apr dry mean
against the pilot's Nov–Dec dry composite, and read the extra two months as a
damped cycle. It is not damped: the regional is low in the two extra months
for the ordinary reason, which is that March and April are not part of the
pilot's dry season.

The comparison has to be anchored on something outside both products, and the
pilot is the only such thing in the repository: it is an independent
implementation of the same measurement over a different time axis and a
different compositing order (spatial mean then temporal median, against the
regional's mosaic-first order), over the same 421 res-8 cells.

**Measured** (2017, 351 cells observed by both products):

| quantity | pilot | regional |
|---|---|---|
| wet NDVI median | +0.600 | +0.597 (ratio 0.995) |
| dry NDVI median | +0.568 | +0.559 (ratio 0.985) |
| per-cell wet − dry | — | correlation +0.887 |
| seasonal amplitude median | +0.174 | +0.229 (ratio 1.31) |

**Consequence.** `tools/verify_phase1.py` criterion 3 runs this comparison
and reports the numbers; the previous absolute "wet NDVI must exceed dry NDVI
by more than 0.05" bar is gone, because a bare bar cannot tell a damped cycle
from a site whose dry season is greener than its wet one. That is the normal
case at Rookery Bay: the pilot's dry composite is *higher* than its wet
composite in 13 of 13 years, and a bar asserting the opposite would have
failed a correct build.

---

## D-29 — The mosaic cache budget is one year, not the record
*2026-10-21 · accepted · engineering*

**Decision.** D-22's 30 GB disk budget covers **one year** of the per-period
30 m mosaic cache. A full 612-bin record needs ~400 GB of it, and the archive
itself — the product — is 1.7 GB. Keep the mosaic for the years being worked
on; rebuild on demand rather than retaining the whole record.

**Why.** Measured on the 2017 build: **664 MB per bin** (259 – 817 MB), so
15.27 GB for 23 bins and **~406 GB for the full record**. Deflate buys only
about 1.75× over the raw uint16 DN, because it does not exploit the 16-bit
structure of the DN values; D-22's "7–14 GB for a single year" was therefore
right at the top of its range, and D-25's "~250–600 MB per bin" was at the
bottom of the measured range.

**Consequence.** The archive stays small enough to serve, which is what the
viewer cares about: 64.5 MB for the whole of 2017 (8.21 MB overview + 56.3 MB
of shards), under half a second cold load against a 3 s budget. The mosaic
cache is a build accelerator, not the product, and its retention is a
per-year choice. The one thing that must not happen is a rebuild that
silently re-reads the network when a cached mosaic is wanted — the `mosaic`
stage is therefore cached per bin and the rest of the pipeline never touches
the network.
