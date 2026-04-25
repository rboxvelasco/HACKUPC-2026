#!/usr/bin/env python3
"""
Warehouse Bay Optimizer v2 — Strip Packing + Simulated Annealing
HackUPC 2026 — Mecalux Challenge

Usage: python3 solver.py <case_directory> [output_file]
"""

import sys
import os
import time
import math
import random
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Set
from functools import reduce
from math import gcd

from shapely.geometry import Polygon, box, MultiPolygon
from shapely.prepared import prep
from shapely.ops import unary_union
from shapely import STRtree


# ─────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────

@dataclass
class BayType:
    id: int
    width: int
    depth: int
    height: int
    gap: int
    n_loads: int
    price: int

    @property
    def area(self) -> int:
        return self.width * self.depth

    @property
    def price_per_load(self) -> float:
        return self.price / self.n_loads

    @property
    def efficiency(self) -> float:
        return self.area * self.n_loads / self.price if self.price > 0 else 0


@dataclass
class Ceiling:
    breakpoints: List[Tuple[int, int]]

    def height_at(self, x: float) -> int:
        h = self.breakpoints[0][1]
        for bx, bh in self.breakpoints:
            if x >= bx:
                h = bh
            else:
                break
        return h

    def min_height_in_range(self, x_min: float, x_max: float) -> int:
        min_h = self.height_at(x_min)
        for bx, bh in self.breakpoints:
            if bx > x_min and bx <= x_max:
                min_h = min(min_h, bh)
            if bx > x_max:
                break
        min_h = min(min_h, self.height_at(x_max))
        return min_h


@dataclass
class PlacedBay:
    bay_type: BayType
    x: float
    y: float
    rotation: int  # 0, 90, 180, 270

    def get_body_dims(self) -> Tuple[float, float]:
        if self.rotation in (0, 180):
            return self.bay_type.width, self.bay_type.depth
        else:
            return self.bay_type.depth, self.bay_type.width

    def get_body_polygon(self) -> Polygon:
        w, d = self.get_body_dims()
        return box(self.x, self.y, self.x + w, self.y + d)

    def get_body_with_gap_polygon(self) -> Polygon:
        w, d = self.get_body_dims()
        gap = self.bay_type.gap
        bx, by = self.x, self.y
        if self.rotation == 0:
            return box(bx, by, bx + w, by + d + gap)
        elif self.rotation == 90:
            return box(bx - gap, by, bx + w, by + d)
        elif self.rotation == 180:
            return box(bx, by - gap, bx + w, by + d)
        elif self.rotation == 270:
            return box(bx, by, bx + w + gap, by + d)
        return box(bx, by, bx + w, by + d)

    def get_x_range(self) -> Tuple[float, float]:
        w, _ = self.get_body_dims()
        return self.x, self.x + w


# ─────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────

def parse_warehouse(filepath: str) -> Polygon:
    coords = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(',')
            coords.append((int(parts[0].strip()), int(parts[1].strip())))
    return Polygon(coords)


def parse_obstacles(filepath: str) -> List[Polygon]:
    obstacles = []
    try:
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(',')
                if len(parts) < 4:
                    continue
                ox, oy = int(parts[0].strip()), int(parts[1].strip())
                ow, od = int(parts[2].strip()), int(parts[3].strip())
                obstacles.append(box(ox, oy, ox + ow, oy + od))
    except FileNotFoundError:
        pass
    return obstacles


def parse_ceiling(filepath: str) -> Ceiling:
    breakpoints = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(',')
            breakpoints.append((int(parts[0].strip()), int(parts[1].strip())))
    breakpoints.sort(key=lambda b: b[0])
    return Ceiling(breakpoints=breakpoints)


def parse_bay_types(filepath: str) -> List[BayType]:
    bays = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(',')
            if len(parts) < 7:
                continue
            bays.append(BayType(
                id=int(parts[0].strip()),
                width=int(parts[1].strip()),
                depth=int(parts[2].strip()),
                height=int(parts[3].strip()),
                gap=int(parts[4].strip()),
                n_loads=int(parts[5].strip()),
                price=int(parts[6].strip()),
            ))
    return bays


# ─────────────────────────────────────────────
# Score
# ─────────────────────────────────────────────

def compute_score(placed_bays: List[PlacedBay], warehouse_area: float) -> float:
    if not placed_bays:
        return float('inf')
    total_price = sum(b.bay_type.price for b in placed_bays)
    total_loads = sum(b.bay_type.n_loads for b in placed_bays)
    total_bay_area = sum(b.bay_type.area for b in placed_bays)
    coverage = total_bay_area / warehouse_area
    if total_loads <= 0:
        return float('inf')
    base = total_price / total_loads
    exponent = 2.0 - coverage
    return base ** exponent


# ─────────────────────────────────────────────
# Collision engine with spatial index
# ─────────────────────────────────────────────

class CollisionEngine:
    def __init__(self, warehouse: Polygon, obstacles: List[Polygon], ceiling: Ceiling):
        self.warehouse = warehouse
        self.warehouse_prep = prep(warehouse)
        self.obstacles = obstacles
        self.ceiling = ceiling
        self.obstacle_tree = STRtree(obstacles) if obstacles else None

        self.placed_bays: List[PlacedBay] = []
        self.bodies: List[Polygon] = []
        self.bodies_gap: List[Polygon] = []

    def _overlaps(self, a: Polygon, b: Polygon) -> bool:
        return a.intersects(b) and not a.touches(b)

    def can_place(self, c: PlacedBay, ignore: int = -1) -> bool:
        body = c.get_body_polygon()
        body_gap = c.get_body_with_gap_polygon()

        if not self.warehouse_prep.contains(body):
            return False
        if not self.warehouse_prep.contains(body_gap):
            return False

        if self.obstacle_tree is not None:
            for idx in self.obstacle_tree.query(body):
                if self._overlaps(body, self.obstacles[idx]):
                    return False
            for idx in self.obstacle_tree.query(body_gap):
                if self._overlaps(body_gap, self.obstacles[idx]):
                    return False

        x_min, x_max = c.get_x_range()
        if c.bay_type.height > self.ceiling.min_height_in_range(x_min, x_max):
            return False

        for i in range(len(self.placed_bays)):
            if i == ignore:
                continue
            if self._overlaps(body, self.bodies_gap[i]):
                return False
            if self._overlaps(body_gap, self.bodies[i]):
                return False

        return True

    def place(self, bay: PlacedBay):
        self.placed_bays.append(bay)
        self.bodies.append(bay.get_body_polygon())
        self.bodies_gap.append(bay.get_body_with_gap_polygon())

    def replace(self, idx: int, bay: PlacedBay):
        self.placed_bays[idx] = bay
        self.bodies[idx] = bay.get_body_polygon()
        self.bodies_gap[idx] = bay.get_body_with_gap_polygon()

    def remove(self, idx: int):
        self.placed_bays.pop(idx)
        self.bodies.pop(idx)
        self.bodies_gap.pop(idx)

    def score(self, warehouse_area: float) -> float:
        return compute_score(self.placed_bays, warehouse_area)


# ─────────────────────────────────────────────
# Strip decomposition
# ─────────────────────────────────────────────

def compute_free_space(warehouse: Polygon, obstacles: List[Polygon]) -> Polygon:
    """Subtract obstacles from warehouse to get free space."""
    free = warehouse
    for obs in obstacles:
        free = free.difference(obs)
    return free


def get_horizontal_strips(
    free_space, bay_types: List[BayType], ceiling: Ceiling
) -> List[dict]:
    """
    Decompose free space into horizontal strips.
    Each strip: {y_start, y_end, x_intervals: [(x_start, x_end), ...], max_height}
    
    We scan Y at bay-depth intervals and find the X ranges available.
    """
    if free_space.is_empty:
        return []

    minx, miny, maxx, maxy = free_space.bounds
    minx, miny, maxx, maxy = int(minx), int(miny), int(maxx), int(maxy)

    # Get all unique depths (body dimensions that could be strip height)
    depths = set()
    for bt in bay_types:
        depths.add(bt.depth)
        depths.add(bt.width)  # rotated

    strips = []

    # For each possible Y position, find horizontal X intervals
    # Use a fine step based on GCD of depths + gaps
    all_dims = []
    for bt in bay_types:
        all_dims.extend([bt.depth, bt.width, bt.gap])
    step = reduce(gcd, all_dims) if all_dims else 100

    # Cap step for large warehouses
    span_y = maxy - miny
    if span_y // step > 1000:
        step = max(step, span_y // 1000)

    y = miny
    while y < maxy:
        # For each depth, check what X range is available
        for depth in sorted(depths):
            if y + depth > maxy + 1:
                continue

            # Create a thin horizontal strip and intersect with free space
            strip_box = box(minx, y, maxx, y + depth)
            intersection = free_space.intersection(strip_box)

            if intersection.is_empty:
                continue

            # Extract X intervals from the intersection
            x_intervals = []
            geoms = [intersection] if intersection.geom_type == 'Polygon' else list(intersection.geoms) if hasattr(intersection, 'geoms') else []

            for geom in geoms:
                if geom.is_empty or geom.area == 0:
                    continue
                gminx, gminy, gmaxx, gmaxy = geom.bounds
                # Only count if the strip is fully contained in height
                if gmaxy - gminy >= depth - 1:  # tolerance
                    x_intervals.append((gminx, gmaxx))

            if x_intervals:
                # Get ceiling height for this strip
                max_h = float('inf')
                for x_start, x_end in x_intervals:
                    max_h = min(max_h, ceiling.min_height_in_range(x_start, x_end))

                strips.append({
                    'y': y,
                    'depth': depth,
                    'x_intervals': x_intervals,
                    'max_height': max_h,
                })

        y += step

    return strips


# ─────────────────────────────────────────────
# Row packing — fill a horizontal strip with bays
# ─────────────────────────────────────────────

def pack_row(
    x_start: float, x_end: float, y: float,
    depth: int, max_height: int, rotation: int,
    bay_types: List[BayType],
    engine: CollisionEngine,
    ceiling: Ceiling = None,
) -> List[PlacedBay]:
    """
    Pack bays left-to-right in a horizontal strip.
    Uses ceiling-aware type selection: picks the best bay type for each
    X segment based on the local ceiling height.
    Uses a 1D knapsack-like approach to maximize width utilization.
    """
    available_width = x_end - x_start
    if available_width < 1:
        return []

    # Build candidates: (bay_type, rotation, effective_width) that match this depth
    candidates = []
    for bt in bay_types:
        # rotation 0 or 180: width along X, depth along Y
        if bt.depth == depth:
            candidates.append((bt, rotation, bt.width))
        # rotation 90 or 270: depth along X, width along Y
        if bt.width == depth:
            rot_alt = 90 if rotation == 0 else 270 if rotation == 180 else 90
            candidates.append((bt, rot_alt, bt.depth))

    if not candidates:
        return []

    placed = []
    x = x_start

    while x < x_end - 1:
        # Get local ceiling height at current X position
        best = None
        best_score_val = -1

        for bt, rot, ew in candidates:
            if x + ew > x_end + 0.5:
                continue

            # Check ceiling for this specific bay at this X
            if ceiling is not None:
                local_ceil = ceiling.min_height_in_range(x, x + ew)
                if bt.height > local_ceil:
                    continue
            elif bt.height > max_height:
                continue

            c = PlacedBay(bay_type=bt, x=x, y=y, rotation=rot)
            if not engine.can_place(c):
                continue

            # Score: prefer bays that maximize area * loads / price
            # But also prefer wider bays to reduce wasted space
            score_val = bt.efficiency * (ew / available_width)
            if score_val > best_score_val:
                best_score_val = score_val
                best = c

        if best is None:
            # Try any bay that fits, smallest first (fill remaining space)
            for bt, rot, ew in sorted(candidates, key=lambda c: c[2]):
                if x + ew > x_end + 0.5:
                    continue
                if ceiling is not None:
                    local_ceil = ceiling.min_height_in_range(x, x + ew)
                    if bt.height > local_ceil:
                        continue
                elif bt.height > max_height:
                    continue
                c = PlacedBay(bay_type=bt, x=x, y=y, rotation=rot)
                if engine.can_place(c):
                    best = c
                    break

        if best is None:
            # Skip this position and try next aligned position
            min_w = min(ew for _, _, ew in candidates)
            x += min_w
            continue

        engine.place(best)
        placed.append(best)
        w, _ = best.get_body_dims()
        x += w

    return placed


# ─────────────────────────────────────────────
# Alternating row placement
# ─────────────────────────────────────────────

def compute_row_positions(
    y_start: int, y_end: int, depth: int, gap: int
) -> List[Tuple[int, int]]:
    """
    Compute Y positions and rotations for alternating gap directions.
    Returns [(y, rotation), ...]
    
    Pattern: rot=0 (gap up), rot=180 (gap down), alternating.
    This allows tighter packing because gaps overlap.
    
    Row 0: y=y_start, rot=0 -> body [y_start, y_start+depth], gap extends to y_start+depth+gap
    Row 1: y=y_start+depth+gap, rot=180 -> body [y1, y1+depth], gap extends to y1-gap
           gap of row1 = [y1-gap, y1+depth] = [y_start+depth, y_start+2*depth+gap]
           body of row0 = [y_start, y_start+depth] -> gap of row1 starts at y_start+depth, touches. OK!
    Row 2: y=y_start+2*depth+gap, rot=0 -> body [y2, y2+depth]
           gap of row2 = [y2, y2+depth+gap]
           body of row1 = [y1, y1+depth] = [y_start+depth+gap, y_start+2*depth+gap]
           gap of row2 starts at y2 = y_start+2*depth+gap, touches body of row1 end. OK!
    
    So the pattern is: pitch = depth+gap for first, then depth, then depth+gap, ...
    Actually: positions are y_start, y_start+depth+gap, y_start+2*depth+gap, y_start+3*depth+2*gap, ...
    
    Let me recalculate:
    Row 0: y=0, rot=0 -> body[0, D], gap_zone[0, D+G]
    Row 1: y=D+G, rot=180 -> body[D+G, 2D+G], gap_zone[D, 2D+G]
      Check: body[D+G, 2D+G] vs gap_zone_row0[0, D+G] -> touch at D+G. OK
      Check: gap_zone[D, 2D+G] vs body_row0[0, D] -> touch at D. OK
    Row 2: y=2D+G, rot=0 -> body[2D+G, 3D+G], gap_zone[2D+G, 3D+2G]
      Check: body[2D+G, 3D+G] vs gap_zone_row1[D, 2D+G] -> touch at 2D+G. OK
      Check: gap_zone[2D+G, 3D+2G] vs body_row1[D+G, 2D+G] -> touch at 2D+G. OK
    Row 3: y=3D+2G, rot=180 -> body[3D+2G, 4D+2G], gap_zone[3D+G, 4D+2G]
      Check: body[3D+2G, 4D+2G] vs gap_zone_row2[2D+G, 3D+2G] -> touch at 3D+2G. OK
    
    Pattern: 0, D+G, 2D+G, 3D+2G, 4D+2G, 5D+3G, ...
    Even rows (rot=0): y = k*(D+G) where k = row//2 * 2 ... 
    Actually: y_n = n*D + (n//2 + n%2) * G for rot=0, or n*D + n//2 * G for rot=180
    
    Simpler: just alternate and compute positions.
    """
    positions = []
    y = y_start
    rot = 0  # Start with gap up

    while y + depth <= y_end + 0.5:
        positions.append((y, rot))

        if rot == 0:
            # Next row: gap down, starts at y + depth + gap
            y = y + depth + gap
            rot = 180
        else:
            # Next row: gap up, starts at y + depth (gap of this row goes down, 
            # so next row body starts right after this body)
            y = y + depth
            rot = 0

    return positions


def compute_row_positions_reverse(
    y_start: int, y_end: int, depth: int, gap: int
) -> List[Tuple[int, int]]:
    """Same but starting with rot=180 (gap down first)."""
    positions = []
    y = y_start + gap  # Leave room for gap going down
    rot = 180

    if y + depth > y_end + 0.5:
        # Can't even fit one row with gap down, try gap up
        return compute_row_positions(y_start, y_end, depth, gap)

    while y + depth <= y_end + 0.5:
        positions.append((y, rot))

        if rot == 180:
            y = y + depth
            rot = 0
        else:
            y = y + depth + gap
            rot = 180

    return positions


# ─────────────────────────────────────────────
# Main strip packing strategy
# ─────────────────────────────────────────────

def strip_pack(
    bay_types: List[BayType],
    engine: CollisionEngine,
    warehouse: Polygon,
    obstacles: List[Polygon],
    ceiling: Ceiling,
    warehouse_area: float,
    time_limit: float,
) -> None:
    """
    Main packing strategy:
    1. Compute free space (warehouse - obstacles)
    2. For each unique depth, compute alternating row positions
    3. For each row, pack bays left-to-right
    4. Try both gap-up-first and gap-down-first, keep the better one
    """
    start_time = time.time()

    free_space = compute_free_space(warehouse, obstacles)
    if free_space.is_empty:
        return

    minx, miny, maxx, maxy = free_space.bounds

    # Get unique depths and their associated bay types
    depth_groups: Dict[int, List[BayType]] = {}
    for bt in bay_types:
        # depth as strip height (rotation 0/180)
        if bt.depth not in depth_groups:
            depth_groups[bt.depth] = []
        depth_groups[bt.depth].append(bt)
        # width as strip height (rotation 90/270)
        if bt.width not in depth_groups:
            depth_groups[bt.width] = []
        depth_groups[bt.width].append(bt)

    # Get unique gaps per depth
    gap_by_depth: Dict[int, int] = {}
    for bt in bay_types:
        for d in [bt.depth, bt.width]:
            if d not in gap_by_depth:
                gap_by_depth[d] = bt.gap
            else:
                gap_by_depth[d] = min(gap_by_depth[d], bt.gap)

    # Try each depth and pick the best result
    best_engine_state = None
    best_score = float('inf')

    # Sort depths: try the most common depth first (most bay types use it)
    depth_popularity = {}
    for bt in bay_types:
        for d in [bt.depth, bt.width]:
            depth_popularity[d] = depth_popularity.get(d, 0) + bt.efficiency

    sorted_depths = sorted(depth_groups.keys(), key=lambda d: depth_popularity.get(d, 0), reverse=True)

    for depth in sorted_depths:
        if time.time() - start_time > time_limit * 0.7:
            break

        gap = gap_by_depth[depth]

        # Filter bays that can work with this depth
        usable_bays = [bt for bt in bay_types if bt.depth == depth or bt.width == depth]
        if not usable_bays:
            continue

        # Try multiple start patterns
        position_fns = [compute_row_positions, compute_row_positions_reverse]

        for positions_fn in position_fns:
            if time.time() - start_time > time_limit * 0.85:
                break

            # Create a temporary engine
            temp_engine = CollisionEngine(warehouse, obstacles, ceiling)

            row_positions = positions_fn(int(miny), int(maxy), depth, gap)

            for y_pos, rot in row_positions:
                if time.time() - start_time > time_limit * 0.9:
                    break

                # Find X intervals at this Y position
                strip_box = box(minx, y_pos, maxx, y_pos + depth)
                intersection = free_space.intersection(strip_box)

                if intersection.is_empty:
                    continue

                # Extract X intervals
                geoms = []
                if intersection.geom_type == 'Polygon':
                    geoms = [intersection]
                elif hasattr(intersection, 'geoms'):
                    geoms = list(intersection.geoms)

                for geom in geoms:
                    if geom.is_empty or geom.area == 0:
                        continue
                    gminx, gminy, gmaxx, gmaxy = geom.bounds
                    if gmaxy - gminy < depth - 1:
                        continue

                    # Get ceiling height for this interval
                    max_h = ceiling.min_height_in_range(gminx, gmaxx)

                    pack_row(
                        gminx, gmaxx, y_pos, depth, max_h, rot,
                        usable_bays, temp_engine, ceiling,
                    )

            # Evaluate
            temp_score = temp_engine.score(warehouse_area)
            if temp_score < best_score:
                best_score = temp_score
                best_engine_state = (
                    list(temp_engine.placed_bays),
                    list(temp_engine.bodies),
                    list(temp_engine.bodies_gap),
                )

    # Apply best result
    if best_engine_state:
        engine.placed_bays = best_engine_state[0]
        engine.bodies = best_engine_state[1]
        engine.bodies_gap = best_engine_state[2]


# ─────────────────────────────────────────────
# Fill gaps — edge-based second pass
# ─────────────────────────────────────────────

def fill_gaps(
    bay_types: List[BayType],
    engine: CollisionEngine,
    warehouse: Polygon,
    warehouse_area: float,
    time_limit: float,
) -> None:
    """Fill remaining gaps using positions derived from placed bay edges."""
    start_time = time.time()

    sorted_types = sorted(bay_types, key=lambda b: b.efficiency, reverse=True)
    rotations = [0, 180, 90, 270]

    current_score = engine.score(warehouse_area)

    while time.time() - start_time < time_limit:
        placed_this_pass = 0

        # Generate candidate positions from edges of placed bays
        candidate_positions = set()
        for i, pb in enumerate(engine.placed_bays):
            body = engine.bodies[i]
            body_gap = engine.bodies_gap[i]
            for poly in [body, body_gap]:
                bminx, bminy, bmaxx, bmaxy = poly.bounds
                for bt in bay_types:
                    for w, d in [(bt.width, bt.depth), (bt.depth, bt.width)]:
                        for px in [bminx, bmaxx, bminx - w, bmaxx]:
                            for py in [bminy, bmaxy, bminy - d, bmaxy]:
                                candidate_positions.add((int(px), int(py)))

        # Obstacle edges
        for obs in engine.obstacles:
            ominx, ominy, omaxx, omaxy = obs.bounds
            for bt in bay_types:
                for w, d in [(bt.width, bt.depth), (bt.depth, bt.width)]:
                    for px in [omaxx, ominx - w]:
                        for py in [omaxy, ominy - d]:
                            candidate_positions.add((int(px), int(py)))

        # Warehouse vertices
        wx, wy = warehouse.exterior.xy
        for i in range(len(wx)):
            candidate_positions.add((int(wx[i]), int(wy[i])))

        for (x, y) in sorted(candidate_positions):
            if time.time() - start_time > time_limit:
                return

            for bt in sorted_types:
                placed = False
                for rot in rotations:
                    c = PlacedBay(bay_type=bt, x=x, y=y, rotation=rot)
                    if engine.can_place(c):
                        test_bays = engine.placed_bays + [c]
                        test_score = compute_score(test_bays, warehouse_area)
                        if test_score < current_score:
                            engine.place(c)
                            current_score = test_score
                            placed = True
                            placed_this_pass += 1
                            break
                if placed:
                    break

        if placed_this_pass == 0:
            break


# ─────────────────────────────────────────────
# Simulated Annealing (Phase B)
# ─────────────────────────────────────────────

def simulated_annealing(
    bay_types: List[BayType],
    engine: CollisionEngine,
    warehouse: Polygon,
    warehouse_area: float,
    time_limit: float,
) -> None:
    """
    SA with moves: swap type, remove bay, add bay at edge position.
    """
    start_time = time.time()
    if not engine.placed_bays:
        return

    current_score = engine.score(warehouse_area)
    best_score = current_score
    best_state = (
        list(engine.placed_bays),
        list(engine.bodies),
        list(engine.bodies_gap),
    )

    rotations = [0, 90, 180, 270]
    T0 = current_score * 0.1  # Initial temperature
    iterations = 0

    while time.time() - start_time < time_limit:
        iterations += 1
        elapsed_frac = (time.time() - start_time) / time_limit
        T = T0 * (1.0 - elapsed_frac)  # Linear cooling
        if T <= 0:
            T = 0.01

        n = len(engine.placed_bays)
        move = random.random()

        if move < 0.5 and n > 0:
            # Swap type at random position
            idx = random.randint(0, n - 1)
            old_bay = engine.placed_bays[idx]
            bt = random.choice(bay_types)
            rot = random.choice(rotations)
            candidate = PlacedBay(bay_type=bt, x=old_bay.x, y=old_bay.y, rotation=rot)

            if engine.can_place(candidate, ignore=idx):
                old_saved = (engine.placed_bays[idx], engine.bodies[idx], engine.bodies_gap[idx])
                engine.replace(idx, candidate)
                new_score = engine.score(warehouse_area)
                delta = new_score - current_score

                if delta < 0 or random.random() < math.exp(-delta / T):
                    current_score = new_score
                    if new_score < best_score:
                        best_score = new_score
                        best_state = (
                            list(engine.placed_bays),
                            list(engine.bodies),
                            list(engine.bodies_gap),
                        )
                else:
                    # Revert
                    engine.placed_bays[idx] = old_saved[0]
                    engine.bodies[idx] = old_saved[1]
                    engine.bodies_gap[idx] = old_saved[2]

        elif move < 0.7 and n > 1:
            # Remove a random bay
            idx = random.randint(0, n - 1)
            old_saved = (engine.placed_bays[idx], engine.bodies[idx], engine.bodies_gap[idx])
            engine.remove(idx)
            new_score = engine.score(warehouse_area)
            delta = new_score - current_score

            if delta < 0 or random.random() < math.exp(-delta / T):
                current_score = new_score
                if new_score < best_score:
                    best_score = new_score
                    best_state = (
                        list(engine.placed_bays),
                        list(engine.bodies),
                        list(engine.bodies_gap),
                    )
            else:
                # Revert
                engine.placed_bays.insert(idx, old_saved[0])
                engine.bodies.insert(idx, old_saved[1])
                engine.bodies_gap.insert(idx, old_saved[2])

        else:
            # Try to add a bay at a random edge position
            if n > 0:
                ref_idx = random.randint(0, n - 1)
                ref = engine.placed_bays[ref_idx]
                ref_body = engine.bodies[ref_idx]
                bminx, bminy, bmaxx, bmaxy = ref_body.bounds

                bt = random.choice(bay_types)
                rot = random.choice(rotations)
                w, d = bt.width, bt.depth
                if rot in (90, 270):
                    w, d = d, w

                # Try positions adjacent to the reference bay
                positions = [
                    (bmaxx, bminy), (bminx - w, bminy),
                    (bminx, bmaxy), (bminx, bminy - d),
                    (bmaxx, bmaxy), (bmaxx, bminy - d),
                    (bminx - w, bmaxy), (bminx - w, bminy - d),
                ]
                random.shuffle(positions)

                for px, py in positions:
                    candidate = PlacedBay(bay_type=bt, x=px, y=py, rotation=rot)
                    if engine.can_place(candidate):
                        engine.place(candidate)
                        new_score = engine.score(warehouse_area)
                        delta = new_score - current_score

                        if delta < 0 or random.random() < math.exp(-delta / T):
                            current_score = new_score
                            if new_score < best_score:
                                best_score = new_score
                                best_state = (
                                    list(engine.placed_bays),
                                    list(engine.bodies),
                                    list(engine.bodies_gap),
                                )
                            break
                        else:
                            engine.remove(len(engine.placed_bays) - 1)
                            break

    # Restore best state
    engine.placed_bays = best_state[0]
    engine.bodies = best_state[1]
    engine.bodies_gap = best_state[2]


# ─────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────

def write_output(placed_bays: List[PlacedBay], filepath: str):
    with open(filepath, 'w') as f:
        for pb in placed_bays:
            f.write(f"{pb.bay_type.id}, {int(pb.x)}, {int(pb.y)}, {pb.rotation}\n")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def solve(case_dir: str, output_file: str):
    total_start = time.time()

    print(f"[1] Parsing {case_dir}...")
    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    ceiling = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))
    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))

    warehouse_area = warehouse.area
    print(f"    Area={warehouse_area:.0f}  Obstacles={len(obstacles)}  "
          f"BayTypes={len(bay_types)}  CeilingBPs={len(ceiling.breakpoints)}")

    for bt in bay_types:
        print(f"    Bay {bt.id}: {bt.width}x{bt.depth} h={bt.height} gap={bt.gap} "
              f"p/l={bt.price_per_load:.0f} eff={bt.efficiency:.0f}")

    engine = CollisionEngine(warehouse, obstacles, ceiling)

    parse_time = time.time() - total_start
    remaining = 7.0 - parse_time

    # Phase A: Strip packing
    strip_budget = remaining * 0.45
    print(f"[2] Strip packing ({strip_budget:.1f}s)...")
    strip_pack(bay_types, engine, warehouse, obstacles, ceiling, warehouse_area, strip_budget)

    score = engine.score(warehouse_area)
    area = sum(pb.bay_type.area for pb in engine.placed_bays)
    cov = area / warehouse_area if warehouse_area > 0 else 0
    print(f"    → {len(engine.placed_bays)} bays, cov={cov:.1%}, Q={score:.2f}")

    # Fill gaps
    elapsed = time.time() - total_start
    fill_budget = min((7.0 - elapsed) * 0.4, 2.0)
    if fill_budget > 0.3:
        print(f"[3] Filling gaps ({fill_budget:.1f}s)...")
        fill_gaps(bay_types, engine, warehouse, warehouse_area, fill_budget)
        score = engine.score(warehouse_area)
        area = sum(pb.bay_type.area for pb in engine.placed_bays)
        cov = area / warehouse_area
        print(f"    → {len(engine.placed_bays)} bays, cov={cov:.1%}, Q={score:.2f}")

    # Phase B: Simulated Annealing
    elapsed = time.time() - total_start
    sa_budget = 7.0 - elapsed
    if sa_budget > 0.5:
        print(f"[4] Simulated annealing ({sa_budget:.1f}s)...")
        simulated_annealing(bay_types, engine, warehouse, warehouse_area, sa_budget)
        score = engine.score(warehouse_area)
        area = sum(pb.bay_type.area for pb in engine.placed_bays)
        cov = area / warehouse_area
        print(f"    → {len(engine.placed_bays)} bays, cov={cov:.1%}, Q={score:.2f}")

    write_output(engine.placed_bays, output_file)

    total_time = time.time() - total_start
    print(f"\n{'='*50}")
    print(f"  {output_file}: {len(engine.placed_bays)} bays, "
          f"cov={cov:.1%}, Q={score:.2f}, {total_time:.2f}s")
    print(f"{'='*50}")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 solver.py <case_directory> [output_file]")
        sys.exit(1)

    case_dir = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'solution.csv'
    solve(case_dir, output_file)
