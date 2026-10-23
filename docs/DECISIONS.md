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

---

## D-30 — Publish the archive, not the caches; gate the deploy on the size budget
*2026-10-22 · accepted · engineering*

> **Superseded in part by D-31.** The cache-vs-product split, the size gate
> and the overflow reasoning below still hold. The *topology* — one published
> site holding the pilot and the whole regional archive — does not: the archive
> is now one repository per dataset. The 75.1 MiB and 14.4-year figures are the
> single-repository arithmetic this decision replaced and are kept as the
> record of why the split was made.

**Decision.** The archive exports are tracked in git and the viewers are
served as a GitHub Pages site. The build caches (`data/mosaic_cache/`,
`data/cache_regional/`) stay gitignored and are never published. The Pages
workflow runs `tools/measure/site_budget.py --check` before uploading, so a
site that would exceed GitHub's published-site or per-file limit fails the
deploy instead of being published in a broken state.

**Why.** The read path is entirely static: two HTML files and a set of
gzipped binary bundles. Nothing in the viewer needs a server, a database or
a function, so Pages is the cheapest correct host. What it cannot host is the
build cache, and the question "is the data too big" is answered by separating
the two: the published archive is **75.1 MiB** for 2017 (61.6 MiB regional +
13.4 MiB pilot), while the mosaic cache alone is **15.3 GiB** — 200× larger
than the product and regenerable (D-29).

Tracking the archive changes the repository's contract, and it is the same
contract the pilot bundles already have: the files are derived, but a fresh
clone should be able to open the viewer without a multi-hour network build,
and the exporter writes byte-identical output for unchanged data so a diff
means the data changed. The `data/regional/` tree is now that, for the
regional product.

**Measured limits.** GitHub Pages publishes at most 1 GB and Git blocks any
single file over 100 MB (warns from 50 MB). At 2.7 MiB per bin:

| limit | binding file | headroom |
|---|---|---|
| 1 GB site | the archive | 328 bins — 14.4 years |
| 100 MB file | the res-7 overview | 257 bins — 11.3 years |
| 100 MB file | the largest shard | 1,781 bins — 78 years |

**Consequence.** The res-7 overview is the binding constraint and the fault
is structural, not incidental: D-21 made it one unsharded bundle, so it
scales as `cells × periods` and is 72× larger than any shard. Past ~11 years
it has to be split by year. There is a second reason to do that at the same
time: because a shard carries every period, adding a year rewrites all 74
files, so git history grows quadratically (2.7 MiB × ΣN for N years) while
the working tree grows linearly. Per-year payloads fix both — the per-file
limit stops being reachable and each year's files become immutable — and
that is Phase 2 work, not Phase 1's. For the one-year slice the budget is
7.9 % used, so the gate is there to catch the change rather than to block
it today.

**Overflow path (agreed, not built).** If the record outgrows Pages before it
is split by year, the archive moves to a Google Cloud bucket and the site
keeps only the HTML. The read path has one constant that names the data
location (`BASE` in `viewer/regional.html`, `STEM` in `viewer/pilot.html`),
so that move is an absolute URL plus a bucket CORS policy — not a format or
protocol change. It is the escape hatch of record, not a plan to reach for
early: at 75.1 MiB the site uses 7.9 % of the limit and one year of archive
costs less than the pilot's res-10 bundle.

**Outcome.** Deployed and verified from outside at
`https://tylar.info/h3lab/` (the project site inherits the user site's custom
domain; `github.io` 301-redirects). All 74 archive files serve; `src/`,
`tools/`, `docs/` and `README.md` 404, so the workflow publishes the
assembled tree rather than the repository; cold load is 698 ms over the
network against the 3 s budget. The one thing the workflow could not do
itself was create the Pages site — that needs repository admin, and
`actions/configure-pages` returned 403 until the setting was flipped by
hand. Worth knowing for any other repository adopting this workflow.

---

## D-31 — One repository per dataset; h3lab is the Rookery Bay record
*2026-10-22 · deferred 2026-10-23 · architecture*

> **Deferred by D-33.** The 73 regional dataset repositories are not being
> created, and regional publication is not active work. The pilot remains the
> focus. This entry is kept as the design record in case regional publication
> resumes; nothing below is a near-term task.

**Decision.** The unit of publication is the **dataset**, not the build and not
the AOI. A regional build is split into:

- `h3-overview` — the res-7 overview **and** `manifest.json`, which is the
  index: every region repository and the absolute URL of every payload;
- `h3-region-<res4 parent>` — one repository per res-8 region shard, 72 of them
  for South Florida 2017.

`h3lab` itself publishes only the Rookery Bay pilot and the viewer code. The
site opens straight into `viewer/pilot.html`; there is no landing page and no
catalog of datasets to keep in sync. Other AOIs get their own overview and
region repositories later, and an overview "might differ" from the regional
ones, so the dataset viewer takes an overview manifest URL (`?manifest=`)
rather than assuming a shape.

**Why.** The previous topology (D-30) put 75.1 MiB of archive, 73 datasets and
the pilot in one repository and one Pages site. Three problems followed, and
none of them is about total bytes:

1. **History.** A shard carries every period, so adding a year rewrites all 74
   files in one commit. Git history grew quadratically while the working tree
   grew linearly. Per dataset, a year rewrites one payload per repository, so
   each history is linear in years.
2. **A shared ceiling.** GitHub's 1 GB site limit applied to the sum of all
   regions, so the largest region decided what the smallest could store, and
   growing any one region forced a global re-plan. Per repository it applies
   to one dataset, which leaves 122 years of headroom for the largest.
3. **Coupling.** Rebuilding one shard touched the repository everyone clones.
   Now it touches only the dataset being rebuilt.

**Addressing is a template.** Payloads are not paths relative to the viewer.
The publisher stamps `manifest.overview.url` and `manifest.regions[].url` from
`--url-template`, default
`https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{file}`. The viewer
resolves `e.url || BASE + e.file`, so:

- a local, unstamped build (`data/regional/manifest.json`, payloads adjacent)
  still works and is still what a rebuild produces byte-for-byte;
- a published manifest never assumes same-origin;
- a Google Cloud bucket is the same command with
  `--url-template 'https://storage.googleapis.com/<bucket>/{repo}/{file}'`.

`raw.githubusercontent.com` returns `access-control-allow-origin: *` and
`application/octet-stream` (verified by HEAD), so shard repositories need no
per-repository Pages enablement. If a dataset is later served from Pages, the
only change is its URL in the template output.

**Measured limits, per repository.** From the 2017 build (payload
64,544,937 B; overview 348.6 KiB/bin; largest shard 54.1 KiB/bin):

| limit | applies to | headroom |
|---|---|---|
| 1 GB published site | h3lab's site, 13.5 MiB | 1.4 % used |
| 100 MB per file | `overview_res7.bin.gz` | 257 bins — **11.3 years** |
| 100 MB per file | largest region shard | 1,781 bins — 78 years |
| 1 GB per repository | largest dataset repo, 7.8 MiB | 2,779 bins — 122 years |

The overview is still the binding file and still for D-21's reason: it is one
unsharded bundle scaling as `cells × periods` and is 72× larger than a shard.
Splitting it by year remains the Phase 2 fix (D-30). What the split changes is
that the overview's limit is now its own: it no longer caps the regions.

**Consequences.**

- `data/regional/` is gitignored build output again. It is the *input* to
  publication, not the published thing, and removing it from git removes it
  from the Pages site too — so the site has nothing regional to show.
- The site is the pilot: `/h3lab/` and `/h3lab/viewer/` redirect to
  `viewer/pilot.html`. A dataset the site does not host is opened by URL
  (`viewer/regional.html?manifest=...`), so there is no catalog to go stale
  and no placeholder entry to maintain.
- The publisher (`tools/publish_datasets.py`) copies payloads unchanged and
  writes a `dataset.json` and `README.md` per repository, so the build stays
  byte-identical and a repository makes sense when opened on its own. It
  refuses to create repositories: `--emit-create` writes the `gh repo create`
  script and `--push` pushes staged trees, because creation needs a token and
  has a blast radius a build should not have.
- Repository count grows with regions, never with years: 73 today, +72 per new
  AOI. That is the real cost of this decision and it is accepted deliberately.

**What is not built.** The 73 repositories do not exist on GitHub yet, and the
build hosts here have neither `gh` nor a token, so nothing has been pushed.
What *is* verified: the split stages 73 trees (61.6 MiB of payload), and with
a localhost `--url-template` all 73 stamped payload URLs resolve 200 from the
single overview manifest — i.e. the URL-addressed model is proven end to end,
leaving only repository creation and authentication. The overview-per-year
split and the bucket both remain open, and both are now data-side changes.

**Status.** Deferred by D-33 (2026-10-23). No repositories are being created
and the regional build is not being published; the single-site pilot is the
active work.

---

## D-32 — The pilot drops reflectance and ships per-cell assessment instead
*2026-10-22 · superseded in part by D-33 · pilot / product*

> **Superseded in part by D-33 (2026-10-23).** The per-cell assessment and the
> stability default stand. The removal of reflectance and the per-band layers
> does not: both returned to the pilot bundle and viewer. The rationale below
> for computing the assessment at export still applies; the payload and
> "consequences" claims about bands are historical.

**Decision.** The Rookery Bay pilot bundle no longer carries the six
surface-reflectance series, and the viewer no longer offers per-band spectra
layers. In their place the bundle carries three groups of per-cell fields:

- **stability**, precomputed at export: `cell.stab.angle` and
  `cell.stab.churn`;
- **assessment**, one set per index: `cell.trend.<index>`,
  `cell.trendz.<index>`, `cell.amp.<index>`, `cell.phase.<index>`,
  `cell.r2.<index>`, `cell.anom.<index>`;
- **assessment metadata**, exactly recoverable: `cellraw.anomp.<index>`, the
  period index of the largest anomaly, with `65535` meaning none.

The default layer is spectral stability, on the angle metric. Trend,
significance, seasonal amplitude, peak season, fit R² and largest anomaly are
selectable layers, all static over the whole record.

**Why.** The pilot had grown into a combinatorial surface: six indices, six
reflectance bands, three resolutions and 26 composites, and every one of those
was a layer or a series in the biography. Most of it was not read. The bands
existed for exactly one purpose — computing spectral stability in the
browser — and cost six times the payload of an index to do it. Dropping them
removes a third of the served bytes (res 8: 0.28 → 0.19 MB; res 9: 1.84 →
1.23; res 10: 11.88 → 7.91) and removes the only reason the browser needed a
second data group.

The assessment fields are static, so computing them in the browser was also
the wrong place: the same fit was redone on every load and every mode change.
At export they are computed once, quantised with the existing uint16 scheme,
and shipped as 38 small columns at res 8 (six metrics × six indices, plus the
two stability fields). The map shading is then a field lookup, not a fit.

**The model.** The fit is the identifiable form of a trend-plus-season
regression:

    y = a + b (year − mean year) + s · wet

where `wet` is 1 for the wet composite and 0 for the dry one. `b` is the
trend per year, `|s|/2` is the seasonal amplitude the layer shows, and the
peak is the season `s` favours: August (month 8) or January (month 1). A free
annual harmonic, `c cos 2πt + d sin 2πt`, is *rank-deficient* with two
composites a year: its columns depend on the season alone, so together with
an intercept they span the same space as the wet/dry indicator. The design
condition number is ~6e8, and solving its normal equations in double
precision returned a seasonal amplitude of 0.122 for a synthetic 0.100
signal. The form above reproduces the synthetic values to machine precision
and makes the phase limit explicit rather than hiding it: with two samples a
year the peak resolves to a season, not a date.

Fits need `MIN_FIT_OBS = 8` observed composites. Stability needs two for a
change and four before its "thin" flag clears; the angle additionally needs
summed reflectance above 0.10, because Collection 2's −0.2 offset puts dark
water near the denominator's zero.

**Consequences.**

- `meta.bands` and `meta.band_info` were removed (restored by D-33);
  `meta.analysis` describes the six metrics (key, label, rank direction,
  display span, palette) so the viewer's controls are generated from the
  bundle rather than hard-coded.
- The binary writer treats the band group as optional. `build_block` still
  writes it when a block has one, so the regional archive
  (`export_region.py`, `viewer/regional.html`) is unchanged, bands and all.
- The pilot could not be rebuilt from scenes here — there is no
  `data/cache/` — so `tools/reanalyze_pilot.py` recomputed the new fields from
  the *published* bundle using the same `h3lab.analysis` code as the exporter.
  It is a one-shot migration. The pre-migration bundles survived in git, and
  D-33 recovered them; a later change to the model still needs a scene
  rebuild or another published copy to work from.
- `tools/test_analysis.py` pins the properties the layers claim (slope,
  swing, phase, planted anomaly, gaps, thin records, scale-invariant angle),
  and the viewer's `fitCell` was checked against the Python fit to ~1e-15 on
  the same synthetic record. The biography re-fits one cell so its chart
  cannot disagree with the layer that selected it.
- D-03's "six reflectance bands and six indices" remains true of the regional
  **archive** and, after D-33, of the pilot **bundle** again. D-31 was the
  publishing plan at the time; it is now deferred by D-33.

---

## D-33 — Spectra return to the pilot; regional publication is deferred
*2026-10-23 · accepted · pilot / scope*

**Decision.** Two changes:

1. The pilot bundle and viewer carry surface reflectance again. The bundle
   writes `band.<name>` fields and restores `meta.bands` / `meta.band_info`;
   the viewer offers one reflectance layer per band, includes the six
   reflectance rows in the biography heatmap, and draws the spectral profile.
   The per-cell assessment and the spectral-stability default from D-32 stay.
2. The 73-repository regional deployment in D-31 is deferred. This repository
   publishes the Rookery Bay pilot and the viewer code only. `tools/` keeps the
   regional publisher as a design record, but no repositories are being created
   and no regional build is being split or pushed. The single-site prototype is
   the work.

**Why.** The assessment is a summary of the spectra, not a substitute for
them. Without reflectance in the payload the biography could rank cells by
stability and trend but could not show the reflections those numbers were
computed from; the reader had to trust the summary. The band layers and the
profile make the summary checkable. Separately, the regional deployment is an
administrative and operational commitment (73 repositories, tokens, per-repo
addresses) far ahead of the evidence; the pilot still needs iteration, and
each iteration is cheaper on one site.

The cost is bounded: res 8/9/10 are 0.31 / 2.05 / 13.24 MB served, 15.6 MB in
total, against 9.3 MB without bands. The largest file is 13.24 MB, 13% of the
100 MB per-file ceiling. That is acceptable for the pilot; a future
assessment-only export can drop the group again.

**Recovery.** The pre-migration bundles were still in git at `faf3bc7`. The
source rasters are not in this checkout, so `tools/restore_spectra.py` reads
those bundles, dequantises bands and indices, recomputes the assessment with
`h3lab.analysis.analyse`, and writes bands, indices and assessment together.
The recomputed cell fields match the assessment-only bundle within one
quantisation step (max |diff| 3.6e-4 on `stab.angle`), so this is a recovery,
not a model change. `tools/reanalyze_pilot.py` remains the record of the
original migration.

**Consequences.**

- `viewer/pilot.html` decodes `desc.bands`, restores the band colour modes,
  the heatmap's reflectance group and the spectral profile, and keeps the
  assessment layers and the stability default.
- The pilot bundle is larger; README sizes, load notes and the tracked-file
  inventory are updated.
- D-31 is deferred, not deleted: the regional archive and publisher still
  exist, and the design is available if regional publication is picked up
  later.
