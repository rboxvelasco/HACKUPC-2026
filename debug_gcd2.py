#!/usr/bin/env python3
"""What cell size would we get WITHOUT the placements (scene-only)?"""
import os
from functools import reduce
from math import gcd

from bitmap import (
    _collect_grid_dimensions,
    _parse_obstacles_raw,
    _parse_warehouse_coords,
)
from solver import parse_bay_types, parse_ceiling

case_dir = 'Cases/CaseAngledD'

wh = _parse_warehouse_coords(os.path.join(case_dir, 'warehouse.csv'))
obs = _parse_obstacles_raw(os.path.join(case_dir, 'obstacles.csv'))
bts = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
ceil = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))

nums_scene_only = _collect_grid_dimensions(
    wh, obs,
    [(b.width, b.depth, b.gap) for b in bts],
    [],  # no placements
    ceiling_breakpoints=ceil.breakpoints,
)
gcd_scene = reduce(gcd, nums_scene_only)
print(f"scene-only GCD = {gcd_scene} mm")
print(f"  bitmap shape would be {40000 // gcd_scene} × {40000 // gcd_scene}")
