"""Region sharding by H3 res-4 parent.

Groups cells by H3 res-4 parent (region). Each region shard carries res-8 cells inside it.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

import h3
import numpy as np

from .config import REGION_RES


def get_region_parent(cell_id: str, region_res: int = REGION_RES) -> str:
    return h3.cell_to_parent(cell_id, region_res)


def group_cells_by_region(grid, region_res: int = REGION_RES) -> dict:
    groups = defaultdict(list)
    for idx, cell in enumerate(grid.cells):
        parent = get_region_parent(str(cell), region_res)
        groups[parent].append(idx)
    return dict(groups)


def compute_content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]
