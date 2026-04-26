#!/usr/bin/env python3
"""Figure out why CaseAngledD's GCD collapses to 1."""
import os
from functools import reduce
from math import gcd

from bitmap import (
    _collect_grid_dimensions,
    _parse_obstacles_raw,
    _parse_warehouse_coords,
    _load_placements,
)
from solver import parse_bay_types, parse_ceiling

case_dir = 'Cases/CaseAngledD'
sol_csv = '/tmp/angledD.csv'

wh = _parse_warehouse_coords(os.path.join(case_dir, 'warehouse.csv'))
obs = _parse_obstacles_raw(os.path.join(case_dir, 'obstacles.csv'))
bts = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
ceil = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))
bt_map = {b.id: b for b in bts}
placements = _load_placements(sol_csv, bt_map)

nums = _collect_grid_dimensions(
    wh, obs,
    [(b.width, b.depth, b.gap) for b in bts],
    [(int(p.x), int(p.y), 0, 0, 0) for p in placements],
    ceiling_breakpoints=ceil.breakpoints,
)

# Incremental GCD — find the first number that drops it
running = 0
for i, n in enumerate(nums):
    prev = running
    running = gcd(running, n)
    if running != prev:
        print(f"[{i:3d}] n={n:>8}  gcd so far = {running}")
    if running == 1:
        print(f"    -> GCD collapsed to 1 after number #{i} = {n}")
        # Print some source hints
        print(f"    numbers list length = {len(nums)}")
        break

# Also dump what classes of numbers are in there
from collections import Counter
print("\nsource samples:")
print(f"  warehouse coords: {wh[:5]}... ({len(wh)})")
print(f"  obstacles: {obs}")
print(f"  bay dims (w,d,g):")
for b in bts:
    print(f"    id={b.id} w={b.width} d={b.depth} g={b.gap}")
print(f"  ceiling bps: {ceil.breakpoints}")
print(f"  placement x/y sample: {[(int(p.x), int(p.y)) for p in placements[:10]]}")

# Check placements y coords for oddness
ys = [int(p.y) for p in placements]
xs = [int(p.x) for p in placements]
print(f"\n  placement xs gcd: {reduce(gcd, xs)}")
print(f"  placement ys gcd: {reduce(gcd, ys)}")
print(f"  placement xs sample sorted: {sorted(set(xs))[:20]}")
print(f"  placement ys sample sorted: {sorted(set(ys))[:20]}")
