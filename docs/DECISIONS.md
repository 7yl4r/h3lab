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
