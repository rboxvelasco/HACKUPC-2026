#!/usr/bin/env python3
"""
Warehouse Bay Optimizer — Greedy + Hill-Climbing Pipeline
HackUPC 2026 — Mecalux Challenge

Usage: python3 solver.py <case_directory> [output_file]
Example: python3 solver.py Cases/Case0 solution.csv
"""

import sys
import os
import time
import math
import random
from dataclasses import dataclass
from typing import List, Tuple, Optional

from shapely.geometry import Polygon, box
from shapely.prepared import prep
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
        """Higher = better. Bays with lots of area and low price/load ratio are best."""
        return self.area * self.n_loads / self.price if self.price > 0 else 0


@dataclass
class Ceiling:
    """Step function: ceiling height by X intervals."""
    breakpoints: List[Tuple[int, int]]  # [(x_start, height), ...]

    def height_at(self, x: float) -> int:
        """Get ceiling height at a given X coordinate."""
        h = self.breakpoints[0][1]
        for bx, bh in self.breakpoints:
            if x >= bx:
                h = bh
            else:
                break
        return h

    def min_height_in_range(self, x_min: float, x_max: float) -> int:
        """Get minimum ceiling height across an X range."""
        # Start with height at x_min
        min_h = self.height_at(x_min)
        # Check all breakpoints within the range
        for bx, bh in self.breakpoints:
            if bx > x_min and bx <= x_max:
                min_h = min(min_h, bh)
            if bx > x_max:
                break
        # Also check height at x_max (in case it falls in a different interval)
        min_h = min(min_h, self.height_at(x_max))
        return min_h


@dataclass
class PlacedBay:
    bay_type: BayType
    x: float
    y: float
    rotation: int  # 0, 90, 180, 270

    def get_body_dims(self) -> Tuple[float, float]:
        """Get (effective_width_x, effective_depth_y) after rotation."""
        if self.rotation in (0, 180):
            return self.bay_type.width, self.bay_type.depth
        else:  # 90, 270
            return self.bay_type.depth, self.bay_type.width

    def get_body_polygon(self) -> Polygon:
        """Get the solid body rectangle."""
        w, d = self.get_body_dims()
        return box(self.x, self.y, self.x + w, self.y + d)

    def get_body_with_gap_polygon(self) -> Polygon:
        """
        Get body + gap rectangle.
        Gap is on the "depth" side (top) of the unrotated bay.

        Rotation 0:   gap extends in +Y
        Rotation 90:  gap extends in -X
        Rotation 180: gap extends in -Y
        Rotation 270: gap extends in +X
        """
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
        """Get the X range occupied by the body."""
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
# Score calculation
# ─────────────────────────────────────────────

def compute_score(placed_bays: List[PlacedBay], warehouse_area: float) -> float:
    """
    Q = (total_price / total_loads) ^ (2 - coverage)
    where coverage = sum(bay_area) / warehouse_area
    Lower is better.
    """
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
# Collision engine
# ─────────────────────────────────────────────

class CollisionEngine:
    """Manages placed bays and fast collision checks."""

    def __init__(self, warehouse: Polygon, obstacles: List[Polygon], ceiling: Ceiling):
        self.warehouse = warehouse
        self.warehouse_prep = prep(warehouse)
        self.obstacles = obstacles
        self.ceiling = ceiling

        if obstacles:
            self.obstacle_tree = STRtree(obstacles)
        else:
            self.obstacle_tree = None

        self.placed_bays: List[PlacedBay] = []
        self.placed_bodies: List[Polygon] = []
        self.placed_bodies_gap: List[Polygon] = []

    def _overlaps(self, a: Polygon, b: Polygon) -> bool:
        """Check if two polygons overlap (touching/sharing boundary is OK)."""
        return a.intersects(b) and not a.touches(b)

    def can_place(self, candidate: PlacedBay) -> bool:
        """Full validation of a candidate placement."""
        body = candidate.get_body_polygon()
        body_gap = candidate.get_body_with_gap_polygon()

        # 1. Body inside warehouse
        if not self.warehouse_prep.contains(body):
            return False

        # 2. Body+gap inside warehouse
        if not self.warehouse_prep.contains(body_gap):
            return False

        # 3. No obstacle overlap (body and body+gap)
        if self.obstacle_tree is not None:
            for idx in self.obstacle_tree.query(body):
                if self._overlaps(body, self.obstacles[idx]):
                    return False
            for idx in self.obstacle_tree.query(body_gap):
                if self._overlaps(body_gap, self.obstacles[idx]):
                    return False

        # 4. Ceiling check
        x_min, x_max = candidate.get_x_range()
        min_ceiling = self.ceiling.min_height_in_range(x_min, x_max)
        if candidate.bay_type.height > min_ceiling:
            return False

        # 5. No overlap with existing bays
        #    - new body vs existing body+gap
        #    - new body+gap vs existing body
        for i in range(len(self.placed_bays)):
            if self._overlaps(body, self.placed_bodies_gap[i]):
                return False
            if self._overlaps(body_gap, self.placed_bodies[i]):
                return False

        return True

    def can_place_ignoring(self, candidate: PlacedBay, ignore_idx: int) -> bool:
        """Validate placement ignoring one existing bay (for swaps)."""
        body = candidate.get_body_polygon()
        body_gap = candidate.get_body_with_gap_polygon()

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

        x_min, x_max = candidate.get_x_range()
        if candidate.bay_type.height > self.ceiling.min_height_in_range(x_min, x_max):
            return False

        for i in range(len(self.placed_bays)):
            if i == ignore_idx:
                continue
            if self._overlaps(body, self.placed_bodies_gap[i]):
                return False
            if self._overlaps(body_gap, self.placed_bodies[i]):
                return False

        return True

    def place(self, bay: PlacedBay):
        """Add a bay to the placed list."""
        self.placed_bays.append(bay)
        self.placed_bodies.append(bay.get_body_polygon())
        self.placed_bodies_gap.append(bay.get_body_with_gap_polygon())

    def replace(self, idx: int, bay: PlacedBay):
        """Replace a bay at index."""
        self.placed_bays[idx] = bay
        self.placed_bodies[idx] = bay.get_body_polygon()
        self.placed_bodies_gap[idx] = bay.get_body_with_gap_polygon()

    def remove(self, idx: int):
        """Remove a bay at index."""
        self.placed_bays.pop(idx)
        self.placed_bodies.pop(idx)
        self.placed_bodies_gap.pop(idx)


# ─────────────────────────────────────────────
# Greedy Packing (Phase A) — Row-based
# ─────────────────────────────────────────────

def _generate_x_coords(bay_types: List[BayType], minx: int, maxx: int) -> List[int]:
    """Generate X coordinates that align with bay boundaries."""
    dims = set()
    for bt in bay_types:
        dims.add(bt.width)
        dims.add(bt.depth)

    # Use the GCD of all dimensions as step for a compact grid
    from math import gcd
    from functools import reduce
    all_dims = list(dims)
    step = reduce(gcd, all_dims) if all_dims else 100

    # Ensure step isn't too small for large warehouses
    span = maxx - minx
    max_coords = 600
    if span // step > max_coords:
        step = max(step, span // max_coords)

    coords = []
    x = minx
    while x <= maxx:
        coords.append(x)
        x += step
    return coords


def _generate_y_coords(bay_types: List[BayType], miny: int, maxy: int) -> List[int]:
    """Generate Y coordinates that align with bay boundaries."""
    dims = set()
    for bt in bay_types:
        dims.add(bt.width)
        dims.add(bt.depth)

    from math import gcd
    from functools import reduce
    all_dims = list(dims)
    step = reduce(gcd, all_dims) if all_dims else 100

    span = maxy - miny
    max_coords = 600
    if span // step > max_coords:
        step = max(step, span // max_coords)

    coords = []
    y = miny
    while y <= maxy:
        coords.append(y)
        y += step
    return coords


def greedy_pack(
    bay_types: List[BayType],
    engine: CollisionEngine,
    warehouse: Polygon,
    warehouse_area: float,
    time_limit: float,
) -> None:
    """
    Phase A: Maximize coverage.
    Generate candidate positions aligned to bay dimension multiples
    so bays pack tightly without gaps between them.
    """
    start_time = time.time()

    # Sort bay types: best efficiency first
    sorted_types = sorted(bay_types, key=lambda b: b.efficiency, reverse=True)

    minx, miny, maxx, maxy = warehouse.bounds
    minx, miny, maxx, maxy = int(minx), int(miny), int(maxx), int(maxy)

    x_coords = _generate_x_coords(bay_types, minx, maxx)
    y_coords = _generate_y_coords(bay_types, miny, maxy)

    rotations = [0, 180, 90, 270]  # Prefer 0/180 first (gap up/down)

    # Pre-compute prepared warehouse for fast point checks
    from shapely.geometry import Point
    warehouse_prep_local = prep(warehouse)

    # Pre-compute the smallest bay body dimensions to use as a buffer
    min_w = min(min(bt.width, bt.depth) for bt in bay_types)

    for y in y_coords:
        for x in x_coords:
            if time.time() - start_time > time_limit:
                return

            # Quick reject: check if a small box around (x,y) intersects warehouse
            probe = box(x, y, x + min_w, y + min_w)
            if not warehouse.intersects(probe):
                continue

            for bt in sorted_types:
                placed = False
                for rot in rotations:
                    candidate = PlacedBay(bay_type=bt, x=x, y=y, rotation=rot)
                    if engine.can_place(candidate):
                        engine.place(candidate)
                        placed = True
                        break
                if placed:
                    break


# ─────────────────────────────────────────────
# Fill gaps — second pass with offset grid
# ─────────────────────────────────────────────

def fill_gaps(
    bay_types: List[BayType],
    engine: CollisionEngine,
    warehouse: Polygon,
    warehouse_area: float,
    time_limit: float,
) -> None:
    """Try to fill remaining gaps using edge-aligned positions. Runs multiple passes."""
    start_time = time.time()

    sorted_types = sorted(bay_types, key=lambda b: b.efficiency, reverse=True)
    rotations = [0, 180, 90, 270]

    current_score = compute_score(engine.placed_bays, warehouse_area)

    while time.time() - start_time < time_limit:
        placed_this_pass = 0

        # Generate candidate positions from edges of existing bays
        candidate_positions = set()
        for pb in engine.placed_bays:
            body = pb.get_body_polygon()
            body_gap = pb.get_body_with_gap_polygon()
            for poly in [body, body_gap]:
                bminx, bminy, bmaxx, bmaxy = poly.bounds
                # Positions at edges + offset by bay dims
                for bt in bay_types:
                    for w, d in [(bt.width, bt.depth), (bt.depth, bt.width)]:
                        for px in [bminx, bmaxx, bminx - w, bmaxx - w]:
                            for py in [bminy, bmaxy, bminy - d, bmaxy - d]:
                                candidate_positions.add((int(px), int(py)))

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
                    candidate = PlacedBay(bay_type=bt, x=x, y=y, rotation=rot)
                    if engine.can_place(candidate):
                        test_score = compute_score(
                            engine.placed_bays + [candidate], warehouse_area
                        )
                        if test_score < current_score:
                            engine.place(candidate)
                            current_score = test_score
                            placed = True
                            placed_this_pass += 1
                            break
                if placed:
                    break

        if placed_this_pass == 0:
            break


# ─────────────────────────────────────────────
# Hill Climbing (Phase B)
# ─────────────────────────────────────────────

def hill_climb(
    bay_types: List[BayType],
    engine: CollisionEngine,
    warehouse_area: float,
    time_limit: float,
) -> None:
    """Phase B: Improve Q by swapping types and removing bad bays."""
    start_time = time.time()
    best_score = compute_score(engine.placed_bays, warehouse_area)
    rotations = [0, 90, 180, 270]

    while time.time() - start_time < time_limit:
        improved = False

        # Strategy 1: Swap bay types for better price/loads
        indices = list(range(len(engine.placed_bays)))
        random.shuffle(indices)

        for i in indices:
            if time.time() - start_time > time_limit:
                return

            old_bay = engine.placed_bays[i]

            for bt in bay_types:
                if bt.id == old_bay.bay_type.id and old_bay.rotation == 0:
                    continue

                for rot in rotations:
                    candidate = PlacedBay(
                        bay_type=bt, x=old_bay.x, y=old_bay.y, rotation=rot
                    )

                    if not engine.can_place_ignoring(candidate, i):
                        continue

                    # Evaluate
                    old_saved = engine.placed_bays[i]
                    engine.placed_bays[i] = candidate
                    new_score = compute_score(engine.placed_bays, warehouse_area)

                    if new_score < best_score:
                        engine.replace(i, candidate)
                        best_score = new_score
                        improved = True
                        break
                    else:
                        engine.placed_bays[i] = old_saved

                if improved:
                    break
            if improved:
                break

        # Strategy 2: Remove worst bay if it helps
        if not improved and len(engine.placed_bays) > 1:
            worst_idx = max(
                range(len(engine.placed_bays)),
                key=lambda i: engine.placed_bays[i].bay_type.price_per_load
            )
            old_bay = engine.placed_bays[worst_idx]
            engine.remove(worst_idx)
            new_score = compute_score(engine.placed_bays, warehouse_area)

            if new_score < best_score:
                best_score = new_score
                improved = True
            else:
                # Put it back
                engine.placed_bays.insert(worst_idx, old_bay)
                engine.placed_bodies.insert(worst_idx, old_bay.get_body_polygon())
                engine.placed_bodies_gap.insert(worst_idx, old_bay.get_body_with_gap_polygon())

        if not improved:
            break


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

    # ── Step 1: Parse ──
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

    # ── Step 2: Setup engine ──
    engine = CollisionEngine(warehouse, obstacles, ceiling)

    parse_time = time.time() - total_start
    remaining = 9.5 - parse_time

    # ── Step 3: Greedy (Phase A) ──
    greedy_budget = remaining * 0.4
    print(f"[2] Greedy packing ({greedy_budget:.1f}s)...")
    greedy_pack(bay_types, engine, warehouse, warehouse_area, greedy_budget)

    score = compute_score(engine.placed_bays, warehouse_area)
    area = sum(pb.bay_type.area for pb in engine.placed_bays)
    cov = area / warehouse_area
    print(f"    → {len(engine.placed_bays)} bays, cov={cov:.1%}, Q={score:.2f}")

    # ── Step 4: Fill gaps ──
    elapsed = time.time() - total_start
    fill_budget = min((9.5 - elapsed) * 0.4, 3.0)
    if fill_budget > 0.3:
        print(f"[3] Filling gaps ({fill_budget:.1f}s)...")
        fill_gaps(bay_types, engine, warehouse, warehouse_area, fill_budget)
        score = compute_score(engine.placed_bays, warehouse_area)
        area = sum(pb.bay_type.area for pb in engine.placed_bays)
        cov = area / warehouse_area
        print(f"    → {len(engine.placed_bays)} bays, cov={cov:.1%}, Q={score:.2f}")

    # ── Step 5: Hill climb (Phase B) ──
    elapsed = time.time() - total_start
    hill_budget = 9.5 - elapsed
    if hill_budget > 0.3:
        print(f"[4] Hill climbing ({hill_budget:.1f}s)...")
        hill_climb(bay_types, engine, warehouse_area, hill_budget)
        score = compute_score(engine.placed_bays, warehouse_area)
        area = sum(pb.bay_type.area for pb in engine.placed_bays)
        cov = area / warehouse_area
        print(f"    → {len(engine.placed_bays)} bays, cov={cov:.1%}, Q={score:.2f}")

    # ── Output ──
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
