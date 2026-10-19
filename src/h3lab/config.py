"""Configuration for the Rookery Bay H3 hexagon time-series lab."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

# --------------------------------------------------------------------------
# Geography
# --------------------------------------------------------------------------
# Rookery Bay National Estuarine Research Reserve, Collier County, Florida.
# Centre ~25.97 N, 81.70 W. Kept deliberately small so a full multi-year run
# stays tractable on a laptop; widen AOI_BBOX to scale up.
AOI_NAME = "Rookery Bay NERR, SW Florida"
AOI_BBOX = (-81.80, 25.92, -81.62, 26.08)  # (minx, miny, maxx, maxy) EPSG:4326

# Analysis grid. UTM 17N has a central meridian of 81 W, which puts the AOI
# almost exactly on it, so scale distortion across the box is negligible.
ANALYSIS_CRS = "EPSG:32617"

# Pixel size of the working raster used to map H3 cells to pixels. 30 m matches
# Landsat Collection 2 Level-2, so no resampling is introduced.
PIXEL_SIZE = 30

# --------------------------------------------------------------------------
# H3 resolutions
# --------------------------------------------------------------------------
# Resolutions are chosen around the 30 m Landsat sampling scale so that the
# multi-resolution comparison spans from many-pixels-per-cell down to
# sub-pixel cells (where the fractional-cover estimates become the primary
# signal rather than the hard class).
#   res  8  ~0.74 km2  ~ 230 px  -> robust regional aggregation
#   res  9  ~0.11 km2  ~  39 px  -> site level (default)
#   res 10  ~0.015 km2  ~   5 px  -> near-native, sparse
#   res 11  ~0.002 km2  ~   1 px  -> sub-pixel, fractional cover only
H3_RESOLUTIONS = (8, 9, 10, 11)
DEFAULT_RESOLUTION = 9

# --------------------------------------------------------------------------
# Data source
# --------------------------------------------------------------------------
# Microsoft's Planetary Computer mirrors NASA/USGS Landsat Collection 2
# Level-2 surface reflectance as public, anonymously-readable COGs. No Earthdata
# Login token is required, which keeps the whole pipeline reproducible from a
# clean checkout.
#
# Note: Planetary Computer also hosts HLSL30 (NASA's HLS Landsat branch), but
# that collection starts 2020-01-01 and therefore cannot see Hurricane Irma
# (Sept 2017). Landsat C2 L2 spans 1982-present and is the correct choice for
# this site; HLSL30 is a reasonable alternative for post-2020 work.
STAC_ENDPOINT = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "landsat-c2-l2"

# Surface reflectance assets, keyed by our internal band name -> STAC asset.
# Landsat 4-7 (TM/ETM+) expose "nir"; Landsat 8-9 (OLI-2) expose "nir08".
BAND_ASSETS = {
    "blue": ("blue", "coastal"),
    "green": ("green",),
    "red": ("red",),
    "nir": ("nir08", "nir"),
    "swir1": ("swir16",),
    "swir2": ("swir22",),
}
BANDS = tuple(BAND_ASSETS)
NIR_BAND = "nir"

# Nominal Landsat 8/9 OLI band centres, in nanometres. Landsat 5 TM and 7 ETM+
# appear in the same archive at marginally different wavelengths; the OLI values
# are close enough for labelling, and they are what a reader expects to see.
# These travel in the bundle metadata so the viewer can label band charts
# without duplicating a table.
BAND_INFO = {
    "blue":  {"label": "blue",  "nm": 482,  "range": (450, 515)},
    "green": {"label": "green", "nm": 562,  "range": (525, 600)},
    "red":   {"label": "red",   "nm": 655,  "range": (630, 680)},
    "nir":   {"label": "NIR",   "nm": 865,  "range": (845, 885)},
    "swir1": {"label": "SWIR1", "nm": 1610, "range": (1565, 1655)},
    "swir2": {"label": "SWIR2", "nm": 2200, "range": (2105, 2295)},
}

# Landsat Collection 2 Level-2 scaling: reflectance = DN * 0.0000275 - 0.2.
# Applied post-hoc in float32. Note this yields small negative values over
# dark water, which is expected rather than an error.
SR_SCALE = 0.0000275
SR_OFFSET = -0.2

# Quality mask assets, in order of preference.
QA_PIXEL_ASSETS = ("qa_pixel",)

# --------------------------------------------------------------------------
# Temporal
# --------------------------------------------------------------------------
YEAR_START = 2013
YEAR_END = 2025

# Landsat Collection 2 Level-2 surface reflectance is only produced for
# acquisitions below a solar zenith threshold, so mid-year coverage over a
# humid subtropical site is patchy in practice. Two seasonal composites per
# year are more robust than one annual composite: "wet" captures the growing
# season and "dry" captures the leaf-off signal that separates evergreen
# mangrove from deciduous upland.
SEASONS = {
    "wet": ((5, 1), (10, 31)),  # May 1 - Oct 31
    "dry": ((11, 1), (12, 31)),  # Nov 1 - Dec 31 (+ Jan-Feb folded in)
}
DEFAULT_SEASON = "wet"

# Known event dates annotated on the timeline and shaded in the charts.
# `label` must stay short: with a 13-year axis, adjacent markers are only a
# few tens of pixels apart. The full text goes in the tooltip.
EVENTS = [
    {"date": "2017-09-10", "label": "Irma", "full": "Hurricane Irma (Cat 5, 6 Sep–13 Oct 2017)",
     "kind": "hurricane"},
    {"date": "2020-05-04", "label": "COVID-19", "full": "COVID-19 shutdown; reduced boat traffic and dredging",
     "kind": "context"},
    {"date": "2022-09-28", "label": "Ian", "full": "Hurricane Ian (Cat 4, 26 Sep–30 Oct 2022)",
     "kind": "hurricane"},
]

# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------
# Minimum fraction of a cell's pixels that must survive QA masking in a given
# composite for the cell to be reported as observed. Below this the cell is
# stored as null rather than silently reporting noise.
MIN_VALID_FRACTION = 0.25

# Median is the default temporal statistic: it is far more robust than a mean
# when a single composite is contaminated by haze, cloud shadow or sunglint.
# "mean" is offered for sensitivity checks against Mélin et al. (2016).
COMPOSITE_STAT = "median"

# Cap on scenes read per composite. A median over 12 acquisitions is already
# robust to a couple of hazy frames, and Landsat 8-9 deliver well over this in
# a six-month season, so the extra reads buy little and dominate runtime.
MAX_SCENES_PER_COMPOSITE = 12

# Cloud/score cutoffs. CLOUD_COVER is the scene-level fraction; PIXEL_CLOUD is
# a further per-pixel screen applied inside ingest.
MAX_SCENE_CLOUD = 85.0
MAX_PIXEL_CLOUD = 90.0

# --------------------------------------------------------------------------
# Spectral indices
# --------------------------------------------------------------------------
# This is the single canonical definition of every index the pipeline emits.
# The specs travel in the bundle descriptor, so the viewer labels its own
# legends from these rather than hard-coding names, and the index formulas have
# exactly one implementation to keep correct.
#
#   kind "ratio"  (A - B) / (A + B)
#   kind "savi"   ((NIR - Red) * (1 + L)) / (NIR + Red + L * (1 - Red))
INDEX_SPECS = {
    "ndvi": {
        "label": "NDVI", "kind": "ratio", "bands": ("nir", "red"),
        "range": (-1, 1), "domain": "greenness",
        "desc": "Normalised Difference Vegetation Index (McBeth & Jordan)",
    },
    "ndwi": {
        "label": "NDWI", "kind": "ratio", "bands": ("green", "nir"),
        "range": (-1, 1), "domain": "open water",
        "desc": "Normalised Difference Water Index (McFeeters 1996)",
    },
    "mndwi": {
        "label": "MNDWI", "kind": "ratio", "bands": ("green", "swir1"),
        "range": (-1, 1), "domain": "open water",
        "desc": "Modified NDWI, less sensitive to atmospheric noise (Xu 2006)",
    },
    "ndmi": {
        "label": "NDMI", "kind": "ratio", "bands": ("nir", "swir1"),
        "range": (-1, 1), "domain": "canopy moisture",
        "desc": "Normalised Difference Moisture Index",
    },
    "nbr": {
        "label": "NBR", "kind": "ratio", "bands": ("red", "swir2"),
        "range": (-1, 1), "domain": "burn / bare substrate",
        "desc": "Normalised Burn Ratio (Key & Benson)",
    },
    "savi": {
        "label": "SAVI", "kind": "savi", "bands": ("nir", "red"), "L": 0.2,
        "range": (-1.5, 1.5), "domain": "greenness, soil adjusted",
        "desc": "Soil-Adjusted Vegetation Index, L=0.2",
    },
}
INDICES = tuple(INDEX_SPECS)

# --------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------
DATA_DIR = "data"
VIEWER_DIR = "viewer"
EXPORT_FILENAME = "rookery_bay_h3.json"


@dataclass(frozen=True)
class Config:
    aoi_name: str = AOI_NAME
    aoi_bbox: tuple = AOI_BBOX
    analysis_crs: str = ANALYSIS_CRS
    pixel_size: int = PIXEL_SIZE
    resolutions: tuple = H3_RESOLUTIONS
    default_resolution: int = DEFAULT_RESOLUTION
    stac_endpoint: str = STAC_ENDPOINT
    collection: str = COLLECTION
    bands: tuple = BANDS
    year_start: int = YEAR_START
    year_end: int = YEAR_END
    seasons: dict = field(default_factory=lambda: SEASONS)
    default_season: str = DEFAULT_SEASON
    events: list = field(default_factory=lambda: EVENTS)
    min_valid_fraction: float = MIN_VALID_FRACTION
    composite_stat: str = COMPOSITE_STAT
    max_scenes: int = MAX_SCENES_PER_COMPOSITE
    index_specs: dict = field(default_factory=lambda: dict(INDEX_SPECS))
    data_dir: str = DATA_DIR
    export_filename: str = EXPORT_FILENAME

    def to_dict(self) -> dict:
        d = asdict(self)
        d["aoi_bbox"] = list(self.aoi_bbox)
        d["resolutions"] = list(self.resolutions)
        d["bands"] = list(self.bands)
        d["seasons"] = {k: list(v) for k, v in self.seasons.items()}
        d["index_specs"] = self.index_specs
        d["events"] = self.events
        return d