# H3lab — hexagon biographies for Rookery Bay land-cover change

A pilot that replaces raster pixels with **Uber H3 hexagons** at four
resolutions, builds a per-cell, per-season surface-reflectance record from
**NASA Landsat Collection 2 Level-2**, and serves it in a browsable HTML
viewer where any single hexagon can be selected and read as a *biography*.

This is the exploratory half of the WetCoast work: the mechanism for looking
at habitat change one place at a time, rather than at a stack of rasters.

---

## Why hexagons

H3 cells give three things a 30 m pixel grid does not:

- **A sampling scale that is explicit.** Every cell knows its own area, so a
  value at res 8 and a value at res 10 are not silently comparable.
- **Exact aggregation.** Cell areas vary slightly; `cell_area()` reports the
  true value per cell rather than assuming a nominal one.
- **Scale as an experimental variable.** The same observation can be summarised
  at ~680 000 m², ~97 000 m², ~14 000 m² or ~2 000 m². Where a change signal
  survives or dissolves as you aggregate is itself a result.

Resolution is chosen against the Landsat sampling scale:

| res | mean cell | 30 m px / cell | what it is good for |
|-----|-----------|----------------|---------------------|
| 8 | 678 000 m² | ~750 | robust regional aggregation |
| 9 | 97 000 m² | ~107 | site level — the default |
| 10 | 13 800 m² | ~15 | near-native, sparse |
| 11 | 1 980 m² | ~2 | sub-pixel: fractional cover only |

At res 11 a cell holds about two Landsat pixels, so a hard land-cover class is
close to meaningless and the *fractions* carry the information. That contrast
is the point of shipping all four.

## Why NASA data, and which product

Source is **Landsat Collection 2 Level-2 surface reflectance** (NASA/USGS),
pulled from Microsoft's Planetary Computer STAC mirror, which serves public
COGs over HTTPS with no Earthdata Login token. The whole pipeline runs from a
clean checkout with no credentials.

Two alternatives were checked and rejected for this site:

- **`hls2-l30`** (NASA's HLS Landsat branch) is also on Planetary Computer, but
  its temporal extent **starts 2020-01-01**. It cannot see Hurricane Irma
  (September 2017), which is the event the whole pilot is built around.
- **USGS `landsatlook` STAC** returns the same scenes but its assets point at
  requester-pays S3.

Landsat C2 L2 spans 1982–present, so the baseline can extend far behind the
disturbance rather than stopping a few years before it.

## Data handling notes

Three things in this pipeline are easy to get wrong and are worth stating
plainly.

**STAC `geometry` is not a scene footprint.** In these catalogues the geometry
for a south-west Florida scene spans roughly 22–36° N — about 1500 km, the
acquisition swath rather than the scene. Selecting or filtering scenes by it
silently returns scenes from other paths and rows. Every scene here is
accepted only after opening its raster header and intersecting the *real*
bounds with the AOI in the scene's own CRS.

**QA_PIXEL bit 6 is not ice.** Bits 5–6 encode snow/ice. Bit 6 alone is set in
~98% of pixels over this AOI, including scenes with under 1% cloud. Masking it
discards the entire study area. Only genuine snow (01) and cloud-contaminated
ice (11) are treated as bad. Cloud confidence bits are deliberately unused —
the cloud and cloud-shadow flags already carry that information.

**Median across observations, not across pixels.** Each scene is binned into
cells once, then the median is taken over those per-cell values. This keeps the
expensive work (read, mask, bin) proportional to scenes, and puts the temporal
robustness where the noise is.

## Compositing

Two seasons per year rather than one annual composite:

- **wet** — 1 May to 31 October, the growing season
- **dry** — 1 November to 31 December, when leaf-off and moisture stress
  separate evergreen mangrove from deciduous upland

Up to 12 scenes per season, ranked by scene cloud cover. Per band the temporal
**median** is reported. `MIN_VALID_FRACTION = 0.25` gates each cell: below that
the cell is stored as no-data rather than given a plausible-looking value.

## Spectral indices

**There is no classification anywhere in this pipeline.** No land-cover
labels, no endmember unmixing, no fractional cover, no hard classes. What the
bundle contains is six bands of surface reflectance and six ratios of those
bands, and nothing derived from a model.

That is a deliberate line. A categorical label has to be justified against
ground truth, and none is wired in here; an uncalibrated endmember library
produces confident nonsense that is hard to notice downstream. Anything
categorical belongs in a layer above this data, where it can be checked,
rather than baked into the ingest path.

The six indices are defined once, in `config.INDEX_SPECS`, and implemented
once, in `features.py`. Those specs travel in the bundle descriptor, so the
viewer's legends and control labels are generated from them rather than
duplicating a list of names.

| index | bands | domain |
|---|---|---|
| NDVI | NIR, Red | greenness |
| NDWI | Green, NIR | open water |
| MNDWI | Green, SWIR1 | open water, less noise-sensitive |
| NDMI | NIR, SWIR1 | canopy moisture |
| NBR | Red, SWIR2 | burn / bare substrate |
| SAVI (L=0.2) | NIR, Red | greenness, soil adjusted |

### The ratio guard

A ratio index is only defined when both of its bands carry signal clearly above
the noise floor. Collection 2 surface reflectance has a -0.2 offset and
quantises at about 2.75e-5, so over dark water one band can land near zero or
slightly negative while the other does not -- and `(a-c)/(a+c)` then returns
values far outside the index's own [-1, 1] range. NDVI of **-200** was
observed this way before the guard existed.

A ratio is therefore computed only where `|a+c| > 0.01` **and** both `|a|`
and `|c|` exceed 0.01. The first rejects a near-cancelling denominator; the
second rejects the case where one band has dropped into the noise and the
bound no longer holds. A naive `1e-6` guard admits denominators of 1e-3 -- a
hundred times the product's own precision -- and passes the garbage through.

With both conditions every index sits inside +-0.91 across 76,544
cell-composites, with zero values beyond +-1.05, and the largest
single-composite NDVI step anywhere in the AOI is 0.81. Before the guard it
was 1.80.

The cost is coverage: ratio indices are unavailable over 13-18% of
cell-composites, concentrated on open water where the C2 SR values are simply
too small to form a ratio. Those cells still render normally in the
reflectance layers. SAVI is barely affected (1.2%) because its denominator
keeps a `+L` term and so does not cancel -- which is the practical argument
for keeping it.

## Running it

```bash
python -m pip install h3 rasterio pystac-client planetary-computer numpy
export PYTHONPATH=$PWD/src

# build composites + export the viewer bundle
python -m h3lab.run --years 2013:2025 --resolutions 8,9,10 --export

# or a quick smoke test
python -m h3lab.run --years 2017:2018 --resolutions 9 --export
```

Composites are cached under `data/cache/` as compressed `.npz`, one file per
(resolution, season, year). An interrupted run resumes; `--force` ignores the
cache. Expect roughly 10–15 s per scene read, so a full 13-year build is
about an hour.

## Viewing it

```bash
cd h3lab
./serve.sh            # or: python -m http.server 8000
# open http://localhost:8000/viewer/
```

It must be served over HTTP — browsers block `fetch` on `file://`. `serve.sh`
also binds to `127.0.0.1` only; if you are opening the page from another
machine, a bind mismatch shows up as a hang rather than an error, which is why
the loader now has a hard timeout.

## Loading performance

Profiling in the browser showed the cost is **bytes on the wire**, not CPU:

| step (res 10, 62.5 MB JSON) | time |
|---|---|
| fetch | 160 ms |
| `JSON.parse` | 342 ms |
| build 20,607 hexagon geometries | 93 ms |

Total was well under a second locally, and `python -m http.server` ignores
`Accept-Encoding`, so all 62.5 MB went over the wire uncompressed. That is
fine on loopback and terrible anywhere else.

Two changes fixed it:

**1. Prebaked binary blocks.** Floats are quantised to `uint16` against each
field's own observed range (reflectance is 0..1, indices −1..1, so 65,534
steps is far finer than anything displayed) and packed with the ids, coords
and descriptor into a single `.bin`. Missing values are `0xFFFF`. Format and
rationale are documented in `src/h3lab/binary.py`.

**2. Pre-gzipped on disk.** The file is written as `.bin.gz` and inflated in
the browser with `DecompressionStream`, which sidesteps the fact that a plain
static server will not compress anything. Use `--no-gzip` to write plain
`.bin` if you need to support a browser without `DecompressionStream`.

| resolution | before (JSON) | after | served | cold load |
|---|---|---|---|---|
| res 8 | 1.3 MB | 0.4 MB | 0.3 MB | — |
| res 9 | 9.0 MB | 2.9 MB | 2.0 MB | **0.63 s** |
| res 10 | 62.5 MB | 17.1 MB | 11.9 MB | ~0.2 s (warm) |

Measured stage times on the default view: `fetch 165ms · inflate 93ms ·
decode 16ms · geometry 3ms · colour 3ms` — about **0.3 s** for res 9, and
0.9 s from a cold page load. Hexagon outlines are baked into the bundle
rather than recomputed with the H3 library on every load.

Everything shrank from 72.7 MB to 14.0 MB served, a 5.2x reduction.

### Diagnosing a slow load

Do not guess at this — run **`viewer/probe.html`** (same server, same URL
space). It times download and inflate for every block in isolation, with no
map and no viewer code, and prints MB/s:

```
res 9  download | 2.08MB |  56ms | 35.5 MB/s
res 9  inflate   | 2.8MB  |  51ms | ok
res 10 download | 13.15MB | 139ms | 90.2 MB/s
res 10 inflate   | 19.8MB  | 375ms | ok
```

Interpretation is printed at the bottom of the page. If both stages are fast
and the viewer is still slow, the problem is the map, not the data.

The viewer also carries a live stage readout (top right, hover for detail)
showing `fetch · inflate · decode · geometry · colour` in milliseconds.

### What cannot hang any more

- **Stall detection.** The fetch watchdog keys on *time since last byte*, not
  total duration, so a legitimately slow 13 MB transfer is never cut off but a
  dead socket is caught in 15 s. A single overall timeout conflates slow with
  stuck, which is the wrong distinction.
- **Basemap fails open.** Esri tiles are the only outbound dependency. If they
  are firewalled, proxied or slow, MapLibre can wait indefinitely for a style
  `load` event. After six tile errors the basemap is dropped, the hexagons stay,
  and the reason is surfaced. The hex layers are also no longer gated on that
  event, so blocked tiles can never leave the map blank.
- **Truncation check.** The decoder verifies it consumed exactly the whole
  buffer and fails loudly if not.

### The biography heatmap

The biography needs twelve series — six reflectance bands, six indices — across
26 composites, in a panel about 430 px wide. Overlaid line charts cannot do
this: none of the series agree on a y-scale, so whatever axis you pick hides
the others, and the later series sit on top of the earlier ones. Every
alternative that fits the space trades something away, but they do not trade
the same things:

| form | keeps | costs |
|---|---|---|
| overlaid lines | path between composites | occlusion, one shared y-scale |
| small multiples | path and per-series scale | six to twelve axes, more height |
| horizon plot | per-series scale in 28 px | folded magnitude has to be decoded |
| **heatmap** | per-series scale, no occlusion | continuity between composites |

The heatmap wins on breadth. Matrix position carries the series and the time,
so nothing is ever hidden behind anything else, and each row is normalised
against **its own** record mean and standard deviation. That is what makes
twelve series with ranges from 0.02 to 0.9 comparable without a shared axis.

Normalising by standard deviation rather than per-row min–max is deliberate. A
min–max scale would give a nearly flat series — SAVI moves about 0.02 across
this record — exactly the same visual drama as NDVI moving 0.4, which is a lie
about how much happened. The cost is that a genuinely flat row goes neutral
everywhere, which is the honest answer.

The ramp is quantised into **nine steps** for the same reason the legend shows
nine swatches: at 11 px per cell a smooth gradient makes neighbouring cells
indistinguishable, whereas discrete bands let you count deviations by eye.

Two palette mistakes were obvious the moment it rendered, and both are specific
to a dark panel:

- **The low end must be brighter than the neutral, not darker.** The first
  ramp ran from near-black blue through dark grey to red, so "low" and "no
  observation" were nearly the same colour. The extremes now brighten away
  from a dim neutral.
- **The neutral must stay clearly lighter than the unobserved fill.** Otherwise
  a run of quiet composites reads as a hole in the row. Near-black is reserved
  for cells that were never observed.

A third is structural: **events are drawn as ticks above the grid, never as a
translucent overlay across it.** A tint over the cells would corrupt the one
channel that carries the data.

Because these series are all driven by the same wet/dry cycle, the dominant
texture is a seasonal alternation — that is the record, not an artefact. The
signal the heatmap adds is the *vertical* one: a column where many rows read
high or low at once is a composite that was odd across the whole spectrum, not
just in one index.

Continuity is the thing given up. A heatmap says *when* a composite was
anomalous, not what happened between composites, which is why the current
composite is still printed as exact numbers above it and every cell carries a
tooltip with its value and deviation.

### The spectral profile

The heatmap normalises each row, which is what makes twelve series comparable
— and which also throws away every absolute value. The profile is where the
absolute numbers live, expressed as a shape rather than as a column of six
means.

It replaced a panel of six record means, each followed by a 26-segment strip.
Those strips were pure duplication: segment alpha was `0.18 + 0.82·v/max` and
heatmap colour was `(v − mean)/sd`, both strictly increasing in the same value,
so the strips repeated the heatmap's ordering exactly and added no shape
information of their own. The means did not — nothing else in the panel shows
absolute reflectance — so they were kept and re-plotted rather than deleted.

Three decisions in the plot are deliberate:

- **Logarithmic wavelength axis.** Blue, green and red sit inside 482–655 nm
  while SWIR runs to 2200. On a linear axis the three visible bands collapse
  into the first tenth of the plot.
- **The y axis starts at zero and is not fitted to the cell.** Auto-fitting
  would make a dark-water cell look as bright as a vegetated one, which is
  precisely the comparison the plot exists to support.
- **Points heavier than the line.** The line is an interpolant between band
  centres — Landsat samples six slices, it does not sweep a spectrometer — so
  the measured marks are what carry weight and the connecting stroke is
  visibly subordinate. Whiskers show the observed min–max across composites.

The axis ceiling uses a finer ladder than the usual 1/2/5 rounding. A 0.32
maximum rounded to 0.50 wasted a third of the plot and squashed every whisker
below one pixel.

**A sign error silently removed every whisker.** `Y()` is inverted, so
`Y(max)` is numerically *smaller* than `Y(min)`; the guard `yHi - yLo > 1.2`
was therefore always false and drew nothing. The plot looked finished without
them. Comparing the absolute gap fixed it. This is the same failure mode as the
others in this file — no exception, just a missing element that only a rendered
pixel check catches.

### Chart colours

Series identity is coloured the way spectral plots conventionally are —
**by the band's own wavelength**, with the visible bands getting their real
colour and the near-infrared continuing the hue wheel past red:

| band | nominal | colour |
|---|---|---|
| blue | 482 nm | blue |
| green | 562 nm | green |
| red | 655 nm | red |
| NIR | 865 nm | crimson |
| SWIR1 | 1610 nm | violet |
| SWIR2 | 2200 nm | indigo |

The same hue labels each band's heatmap row and tints its strip in the
mean-reflectance table, so the heatmap, the table and the map legend read as
one object. Nominal wavelengths live in `config.BAND_INFO` and travel in the
bundle metadata, so the viewer labels from them rather than duplicating a
table.

Index identity uses a **fixed** palette instead, because the map ramps cannot
separate them — NDWI and MNDWI are both blues, and SAVI sits close to NDVI in
hue. In the heatmap these hues appear only as the small swatch beside each
row: the cell colour is reserved for the anomaly, which is the channel that
has to stay legible.

### Three chart bugs worth remembering

**`(-x) % 16` is negative in JavaScript and non-negative in Python.** The
writer padded with Python's modulo, the reader advanced with JavaScript's, and
every field after the header was read from the wrong offset. Silent — no
exception, just wrong numbers.

**`f.shape[0] * f.shape[1]` is `NaN` for a 1-D field.** The baked geometry
fields are one-dimensional, so they came back empty and `new Array(NaN)` threw
`Invalid array length`. Shapes are now reduced generically.

A third was in the inspector: series colours were taken straight from the map
ramps as RGB triplets and interpolated into `stroke="${color}"`. A triplet is
not a CSS colour, so the index chart silently rendered black on a dark panel
while the reflectance chart — which used a hex string — worked fine. Index
chart colours are now a fixed, hand-spaced palette, because NDWI and MNDWI are
both blues and ramp midpoints are not separable. A related trap: `.rseg` has a
fixed height, so setting a smaller height on the ribbon container did nothing
and the segments overflowed onto the rows beneath.

All three were found by looking at the rendered pixels, not by reading the
code.

Two more things matter for feel rather than size:

- **Values dequantise lazily, one field at a time.** Opening a block is a
  header parse; the ~2 MB `Float32Array` per field is built the first time that
  field is needed, so switching colour mode costs one field, not fifteen.
- **The loader never hangs.** A 120 s `AbortController` timeout, a live
  byte-progress readout, and an error that names the fix. Missing-value
  handling moved from JSON `null` to `NaN`, and every guard that tested
  `== null` was rewritten — a `null` test silently passes on `NaN`, which would
  have painted missing data as if it were real.

### Verifying a rebuild

`cls`, `observed` and `nObs` are stored as raw integers, so they must be
byte-exact against the caches. The float fields are quantised, with a measured
worst-case error of **1.6e-5** across all 26 periods — one quarter of a
quantisation step.

After changing anything in `features.py` or `binary.py`, re-run the export and
check the *artifact on disk*, not just the format in memory. That distinction
caught a real bug here: an early `.bin.gz` had `cls` collapsed through
`int(bool(...))`, which survived a format-level round-trip test because that
test rebuilt the encoder rather than reading the shipped file.

The viewer has:

- **year/season scrubber** with a play button and Hurricane Irma / Ian markers
- **resolution switcher** — geometry is rebuilt and re-fitted per resolution
- **colour modes** — any index, any band's reflectance, change-versus-baseline,
  or spectral stability
- **spectral stability** layer, with two metrics (below)
- **rank cells by** panel — largest index change, most settled, least
  settled, or most churning. Every entry is clickable.
- **the biography panel** — an NDVI ribbon across the whole record, a heatmap
  of every band and index through time (below), a spectral profile of the
  cell's mean reflectance by wavelength, observation counts, and a written
  one-paragraph summary of what happened to that cell and when

Click a hexagon on the map, or click any entry in the swings panel.

### Spectral stability

Two metrics, both measured on the measurements rather than on derived labels:

| metric | definition | sees |
|---|---|---|
| **NDVI churn** | mean \|ΔNDVI\| between consecutive observed composites | greenness moving |
| **spectral angle** | mean angle, in degrees, between the 6-band reflectance vectors of consecutive observed composites | the *shape* of the spectrum changing, independent of brightness |

They answer different questions. A cell can double in reflectance without its
spectrum rotating: churn notices, angle does not. Conversely a small
reallocation between green and NIR leaves overall brightness almost unchanged
while the spectral shape moves. Both are derived in the browser from arrays
already resident, so no bundle rebuild is involved.

Two guards, because both would otherwise mislead:

- **Cells observed in fewer than 4 composites are left blank**, not scored. A
  stability estimate from two composites is not a stability estimate.
- **Spectral angle needs a brightness floor.** The Collection 2 offset drives
  open water near zero, where the denominator in `cos(angle)` collapses and
  tiny differences swing the result wildly — an angle of 24.9° on a dark cell
  was pure noise. Angles are only reported where summed reflectance across the
  six bands exceeds 0.10. Dark water cells are left blank rather than
  ranked as the most unstable in the AOI.

At Rookery Bay the NDVI churn map puts the unsettled cells along the **coastal
fringe** — the mangrove edge and the barrier island — rather than in the
interior. That is the pattern a storm-driven disturbance should leave, and it
is the opposite of where a class-stability map placed the instability when this
pipeline still had a classifier in it.

This is the map-level counterpart of the ROI stratification the proposal
describes in §3.3: the settled cells are the reference cohort against which
the unsettled ones should be read.

### How the viewer stays fast

Geometry is built once per resolution and cached; the per-period colour of
every cell is precomputed up front. Scrubbing the timeline is then an array
copy plus a `setData`, not a recomputation. Reported timings are shown in the
pill under the toolbar.

## Repository layout and what is tracked

Tracked, 14 MB total:

```
src/h3lab/*.py   the pipeline (9 modules, ~1450 lines)
viewer/*.html    viewer and load probe, no build step
data/*.bin.gz    the export bundle, 13.4 MB
data/*_meta.json its index, 1.5 kB
requirements.txt pinned direct dependencies
```

Ignored:

```
data/cache/   78 .npz, 14 MB -- pure build accelerator, regenerable
logs/         run logs
```

The bundle is derived, but tracking it is deliberate: at 13.4 MB it uses 13%
of GitHub's 100 MB per-file ceiling and 0.3% of the 5 GB repository limit, and
it means a fresh clone opens the viewer immediately instead of running a
one-hour network-dependent pipeline first.

**Do not track `data/cache/`.** It is 14 MB of intermediates that exist only so
a re-run can skip the download. Committing it doubles the repository to store
something with no value.

### Why the bundle is safe to commit

`gzip.compress` writes the current time into the gzip header by default, so two
runs over identical data produced **byte-different files**. Tracked, that turns
every re-export into a 14 MB phantom diff, and once committed each one is
permanent history growth.

The exporter now passes `mtime=0`, which makes the output a pure function of the
input:

```
$ python -m h3lab.run --export && md5sum data/*.bin.gz
$ python -m h3lab.run --export && md5sum data/*.bin.gz   # identical
```

So if `git status` shows these files changed, the data genuinely changed. Read
the diff before committing rather than reaching for `git add -A`. If the
bundle ever grows past the size limit, `git lfs` is the escape hatch -- at the
current size it is unnecessary.

## Layout

```
src/h3lab/
  config.py     AOI, resolutions, bands, seasons, index specs
  binary.py     quantised uint16 block format, baked geometry, gzip writer
  grid.py       H3 construction (nested coarse->fine) + rasterisation
  ingest.py     STAC search, QA masking, COG reads, per-cell binning
  composite.py  temporal median across observations, npz cache
  features.py   index computation only -- no classification
  export.py     JSON bundle, one file per resolution
  run.py        CLI
viewer/index.html
viewer/probe.html      isolated load probe — use this to diagnose a slow load
serve.sh        serve over HTTP (fetch() is blocked on file://)
data/cache/     78 per-composite .npz files
data/rookery_bay_h3_meta.json    index: bbox, palette, events, block filenames
data/rookery_bay_h3_res8.bin.gz  421 cells x 26 periods,  0.3 MB served
data/rookery_bay_h3_res9.bin.gz  2,944 cells x 26 periods,  2.0 MB served
data/rookery_bay_h3_res10.bin.gz 20,607 cells x 26 periods, 11.9 MB served
```

Blocks are split per resolution so the viewer fetches only what is on screen,
and each is a gzipped binary rather than JSON — see the performance section.

## Extending it

- **More years** — `--years 1990:2025`. Landsat C2 L2 goes back to 1982, so a
  pre-1990 baseline is available; the dry season gets sparse but not empty.
- **More sites** — change `AOI_BBOX` in `config.py`. Everything else is
  site-agnostic; the scene search re-derives coverage each time.
- **More indices** — add an entry to `config.INDEX_SPECS` and an arm in
  `features.compute_indices()`. The bundle carries the specs, so the viewer
  picks it up with no further change. Nothing else needs to know.
- **Classification** — deliberately absent, and it belongs above this data
  rather than inside it: use the bands and indices here as inputs to a
  classifier with real ground truth, and keep it a separate product.
- **A different cell system** — only `grid.py` knows about H3.

## What the record actually shows

Across the whole AOI the Hurricane Irma signal is **not** detectable in the
mean:

| composite | mean NDVI | mean NDWI |
|---|---|---|
| 2017 wet (Irma is 10 Sept 2017) | +0.3953 | -0.3118 |
| 2018 wet | +0.4315 | -0.3523 |

That is +0.036 NDVI -- NDVI went *up*, and a well-vegetated strip four months
later is unremarkable. Broken out per cell the change is real but incoherent:
227 cells dropped by more than 0.10 NDVI and 560 rose by more than 0.10, with
1,511 of 2,944 cells moving by less than 0.05.

So this pilot demonstrates the *machinery* -- the hexagon record, the change
view, the biography, the spectral stability layer -- and it demonstrates that
a basin-mean summary would have reported "no change" while missing every cell
that moved. It does **not** establish a hurricane impact at this site. To get
one you would want either a site inside the wind field, or the cell-level
change distribution compared against a null built from non-event years, the
same way a change-point test needs a background rate.

That comparison is the natural next step and it is cheap: the record for all
three resolutions is already built. Note that with the classification removed
there is no longer a per-cell "cover type" to attribute a change to, only the
index that moved and by how much -- which is a constraint worth being explicit
about rather than working around.

## Honest limitations

- **No land cover is produced.** Six bands and six ratios are not enough to
  say "mangrove"; they are enough to say "this spectrum moved this much, in
  these bands, between these composites". Any cover-type claim needs a layer
  above this.
- Ratio indices are missing over 13-18% of cell-composites (dark water, where
  Collection 2 SR is too small to form a ratio). This is a property of the
  product, not a gap in the archive.
- 30 m Landsat cannot resolve Everglades sawgrass / slough / tree islands, and
  says nothing about seagrass or coral.
- The ROI is ~319 km² and one scene path (016/042) covers it. Absolute coverage
  will differ elsewhere.
- **Hurricane Irma is present in the record but does not produce a detectable
  basin-wide signal here** (see above). Treat the 2017/2018 composites as a
  worked example of the change view, not as evidence of impact.
- `res 11` (144,249 cells) is built by `grid.py` and exercised by the pipeline,
  but is not in the default export: 26 periods at that cell count is not
  practical to hand a browser. Use it for zonal statistics in Python.
- Compositing is per-season with up to 12 scenes. Nothing here is gap-filled,
  so a cell missing a whole season is absent rather than interpolated — which
  is why the viewer reports observation counts and flags gaps explicitly.