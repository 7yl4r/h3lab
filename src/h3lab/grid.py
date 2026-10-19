"""H3 hexagon grid construction for the analysis AOI.

Two things here are worth knowing about, because they drive the rest of the
pipeline:

1. The grid is generated *at the coarsest requested resolution first* and then
   refined with `cell_to_children`. H3's cell hierarchy guarantees that a
   res-10 child sits inside its res-9 parent, so generating each resolution
   independently can leave ragged coverage along the AOI edge. Descending from
   the coarsest resolution keeps every finer level exactly nested.

2. Cells are rasterised into a label image once per resolution, and that
   raster is the join key between the pixel grid and the hexagon grid. See
   `build_label_raster`.
"""

from __future__ import annotations

from dataclasses import dataclass

import h3
import numpy as np
from shapely.geometry import Polygon, box, shape
from shapely.prepared import prep

from .config import AOI_BBOX, ANALYSIS_CRS, H3_RESOLUTIONS, PIXEL_SIZE


@dataclass
class HexGrid:
    """A set of H3 cells at one resolution covering the AOI."""

    resolution: int
    cells: np.ndarray          # (N,) H3 cell ids as strings
    centroids_lonlat: np.ndarray  # (N, 2)
    centroids_proj: np.ndarray   # (N, 2) in ANALYSIS_CRS
    index_of: dict             # h3 cell id -> row index
    areas_km2: np.ndarray        # (N,)
    aoi_geom: Polygon
    crs: str
    pixel_size: int

    def __len__(self) -> int:
        return len(self.cells)

    @property
    def aoi_area_km2(self) -> float:
        # geodesic, via h3's own area for the union of cells
        return float(h3.cell_area(self.cells[0], unit="km^2")) * 0 + self._aoi_area

    def subset(self, mask: np.ndarray) -> "HexGrid":
        keep = np.flatnonzero(mask)
        return HexGrid(
            resolution=self.resolution,
            cells=self.cells[keep],
            centroids_lonlat=self.centroids_lonlat[keep],
            centroids_proj=self.centroids_proj[keep],
            index_of={self.cells[i]: k for k, i in enumerate(keep)},
            areas_km2=self.areas_km2[keep],
            aoi_geom=self.aoi_geom,
            crs=self.crs,
            pixel_size=self.pixel_size,
        )

    _aoi_area: float = 0.0


def _centroids_proj(cells: np.ndarray, crs: str) -> np.ndarray:
    """Cell centres in the analysis CRS, without importing pyproj per-cell."""
    from pyproj import Transformer

    ll = np.array([h3.cell_to_latlng(c) for c in cells], dtype=float)  # (lat, lng)
    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x, y = tr.transform(ll[:, 1], ll[:, 0])
    return np.column_stack([x, y])


def build_grid(
    aoi_bbox: tuple = AOI_BBOX,
    resolutions: tuple = H3_RESOLUTIONS,
    crs: str = ANALYSIS_CRS,
    pixel_size: int = PIXEL_SIZE,
) -> dict[int, HexGrid]:
    """Build one HexGrid per resolution, strictly nested from coarse to fine.

    Cells whose centre falls outside the AOI are dropped. We accept the small
    edge bias this introduces rather than clipping cells to the polygon,
    because a clipped hexagon is no longer an H3 cell and would break the
    parent/child relationships the multi-resolution view depends on.
    """
    minx, miny, maxx, maxy = aoi_bbox
    aoi = box(minx, miny, maxx, maxy)
    aoi_prep = prep(aoi)

    grids: dict[int, HexGrid] = {}
    coarsest = min(resolutions)

    for res in sorted(resolutions):
        if res == coarsest:
            seed = h3.latlng_to_cell((miny + maxy) / 2, (minx + maxx) / 2, res)
            k = _disk_radius(res)
            candidates = h3.grid_disk(seed, k)
        else:
            # refine the next-coarser grid, keeping only nested children
            parent = grids[res - 1]
            children = []
            for c in parent.cells:
                children.extend(h3.cell_to_children(c, res))
            candidates = children

        cells, cents_ll, cents_pr = [], [], []
        for cid in candidates:
            lat, lng = h3.cell_to_latlng(cid)
            if not aoi_prep.contains(shape(_hex_ring(cid))):
                continue
            cells.append(cid)
            cents_ll.append((lng, lat))
        if not cells:
            raise ValueError(f"no cells at resolution {res}")

        cells = np.array(cells, dtype=object)
        cents_ll = np.array(cents_ll, dtype=float)
        cents_pr = _centroids_proj(cells, crs)

        grids[res] = HexGrid(
            resolution=res,
            cells=cells,
            centroids_lonlat=cents_ll,
            centroids_proj=cents_pr,
            index_of={c: i for i, c in enumerate(cells)},
            areas_km2=np.array([h3.cell_area(c, unit="km^2") for c in cells], dtype=float),
            aoi_geom=aoi,
            crs=crs,
            pixel_size=pixel_size,
        )
        grids[res]._aoi_area = _geodesic_area_km2(aoi)
        print(
            f"  res {res:>2}: {len(cells):>7,} cells  "
            f"mean {grids[res].areas_km2.mean()*1e6:>9,.0f} m2  "
            f"AOI {grids[res]._aoi_area:,.1f} km2"
        )
    return grids


def _hex_ring(cell: str):
    """Shapely polygon for an H3 cell boundary (lng, lat order)."""
    return Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(cell)])


def _disk_radius(res: int) -> int:
    """Cells to seed from the AOI centre at `res` to cover the AOI with slack."""
    span_deg = max(AOI_BBOX[2] - AOI_BBOX[0], AOI_BBOX[3] - AOI_BBOX[1])
    # edge length in km shrinks ~ 1/sqrt(7^n) per resolution step
    edge_km = h3.average_hexagon_edge_length(res, unit="km")
    cells_across = max((span_deg * 111.0) / (edge_km * 1.5), 1)
    return int(np.ceil(cells_across)) + 2


def _geodesic_area_km2(aoi: Polygon) -> float:
    """Equal-area geodesic area (Karney), correct for a lat/lon box."""
    import pyproj

    geod = pyproj.Geod(ellps="WGS84")
    area, _ = geod.geometry_area_perimeter(aoi)
    return abs(area) / 1e6


# --------------------------------------------------------------------------
# Rasterising hexagons
# --------------------------------------------------------------------------
def analysis_grid(aoi_bbox: tuple = AOI_BBOX, crs: str = ANALYSIS_CRS,
                  pixel_size: int = PIXEL_SIZE):
    """Canonical analysis raster for the AOI.

    Every H3 resolution is rasterised onto *this* grid, so a scene can be read
    once and binned into every resolution with the same pixel array. Returns
    (transform, (height, width), (minx, miny, maxx, maxy)).
    """
    from pyproj import Transformer
    from rasterio.transform import from_origin

    minx, miny, maxx, maxy = aoi_bbox
    # A lat/lon box is not a rectangle in UTM, so densify the boundary before
    # transforming or the projected bounds come out too small and the edges of
    # the AOI get clipped off the raster.
    tr = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    edges = []
    for t in np.linspace(0, 1, 200, endpoint=False):
        edges.append((minx + t * (maxx - minx), miny))
        edges.append((minx + t * (maxx - minx), maxy))
        edges.append((minx, miny + t * (maxy - miny)))
        edges.append((maxx, miny + t * (maxy - miny)))
    xs, ys = tr.transform([e[0] for e in edges], [e[1] for e in edges])
    minx, maxx = float(np.min(xs)), float(np.max(xs))
    miny, maxy = float(np.min(ys)), float(np.max(ys))

    w = int(np.ceil((maxx - minx) / pixel_size))
    h = int(np.ceil((maxy - miny) / pixel_size))
    maxx = minx + w * pixel_size
    miny = maxy - h * pixel_size

    return from_origin(minx, maxy, pixel_size, pixel_size), (h, w), (minx, miny, maxx, maxy)


def build_label_raster(grid: HexGrid, target=None):
    """Rasterise `grid` into an integer label image on the canonical grid.

    Returns (labels, transform, bounds). `labels` holds the row index of the
    cell plus one; 0 means "no cell". Every resolution shares one raster, so
    arrays read once can be binned into all resolutions.

    Pass `target` (as returned by `analysis_grid`) to force a specific grid;
    otherwise one is derived from the HexGrid.
    """
    from rasterio.features import rasterize
    from pyproj import Transformer

    if target is None:
        target = analysis_grid(
            (grid.aoi_geom.bounds[0], grid.aoi_geom.bounds[1],
             grid.aoi_geom.bounds[2], grid.aoi_geom.bounds[3]),
            grid.crs, grid.pixel_size,
        )
    transform, (h, w), (minx, miny, maxx, maxy) = target
    tr = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)

    shapes, values = [], []
    for i, cell in enumerate(grid.cells):
        xy = [tr.transform(lng, lat) for lat, lng in h3.cell_to_boundary(cell)]
        shapes.append({"type": "Polygon", "coordinates": [xy]})
        values.append(i + 1)

    labels = rasterize(
        list(zip(shapes, values)),
        out_shape=(h, w),
        transform=transform,
        fill=0,
        dtype="int32",
        all_touched=False,
    )
    return labels, transform, (minx, miny, maxx, maxy)