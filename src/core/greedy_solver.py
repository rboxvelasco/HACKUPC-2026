#!/usr/bin/env python3
"""
Warehouse Bay Optimizer — Simple Multi-Criteria Greedy
HackUPC 2026 — Mecalux Challenge

A deliberately simple, deterministic solver with two complementary paths:

1. Whole-warehouse pass:
   For each candidate (row-depth × start-rotation × greedy-criterion ×
   horizontal/vertical orientation), do one left-to-right strip-pack with
   alternating-gap rows. Keep the combination with lowest Q.

2. Region decomposition pass:
   Slice the warehouse (minus obstacles) into maximal axis-aligned
   rectangles. Pack each region independently with its own best
   orientation.

Return whichever pass produced the lower Q. The anchor-based gap-filler
that used to close out each path was removed after benchmarking showed
LNS+SA downstream recovers its gains for a fraction of the cost
(see traces/filler_vs_sa.csv).

No simulated annealing. No bitmaps. No local search. Deterministic.

Usage: python3 greedy_solver.py <case_directory> [output_file]
"""

import sys
import os
import time
from dataclasses import dataclass
from typing import List, Tuple, Optional, Callable

from shapely.geometry import Polygon, box
from shapely.prepared import prep
from shapely import STRtree

# Reuse parsing and data structures from the existing solver to avoid drift.
from .solver import (
    BayType, Ceiling, PlacedBay,
    parse_warehouse, parse_obstacles, parse_ceiling, parse_bay_types,
    compute_score, usable_area, compute_free_space,
)


# ─────────────────────────────────────────────
# Lightweight collision engine (axis-aligned, no shapely per-check)
# ─────────────────────────────────────────────

@dataclass
class AABB:
    """Axis-aligned bounding box."""
    x0: float
    y0: float
    x1: float
    y1: float

    def overlaps_strict(self, other: 'AABB') -> bool:
        """Strict overlap (touching is OK)."""
        return not (
            self.x1 <= other.x0 or other.x1 <= self.x0 or
            self.y1 <= other.y0 or other.y1 <= self.y0
        )


def bay_body_aabb(pb: PlacedBay) -> AABB:
    w, d = pb.get_body_dims()
    return AABB(pb.x, pb.y, pb.x + w, pb.y + d)


def bay_gap_aabb(pb: PlacedBay) -> AABB:
    w, d = pb.get_body_dims()
    gap = pb.bay_type.gap
    if pb.rotation == 0:
        return AABB(pb.x, pb.y, pb.x + w, pb.y + d + gap)
    elif pb.rotation == 90:
        return AABB(pb.x - gap, pb.y, pb.x + w, pb.y + d)
    elif pb.rotation == 180:
        return AABB(pb.x, pb.y - gap, pb.x + w, pb.y + d)
    elif pb.rotation == 270:
        return AABB(pb.x, pb.y, pb.x + w + gap, pb.y + d)
    return AABB(pb.x, pb.y, pb.x + w, pb.y + d)


class FastCollisionEngine:
    """Fast AABB-based collision checks for axis-aligned bays.

    Feasibility checks (in order of cost):
      1. Bay body + body_with_gap inside warehouse polygon (shapely, prepared).
      2. Body & body_with_gap don't intersect any obstacle (AABB).
      3. Ceiling: bay.height ≤ min ceiling across body's x-range.
      4. Body doesn't overlap any placed bay's body_with_gap.
      5. Body_with_gap doesn't overlap any placed bay's body.
    """

    def __init__(self, warehouse: Polygon, obstacles: List[Polygon], ceiling: Ceiling):
        self.warehouse = warehouse
        self.warehouse_prep = prep(warehouse)
        self.obstacles = obstacles
        self.obstacle_aabbs = [AABB(*o.bounds) for o in obstacles]
        self.ceiling = ceiling
        self.placed_bays: List[PlacedBay] = []
        self.body_aabbs: List[AABB] = []
        self.gap_aabbs: List[AABB] = []

    def _body_gap_in_warehouse(self, body: AABB, body_gap: AABB) -> bool:
        # Check corners of both rectangles + midpoints. For axis-aligned
        # rectilinear warehouses, this is sufficient and fast.
        for rect in (body, body_gap):
            poly = box(rect.x0, rect.y0, rect.x1, rect.y1)
            if not self.warehouse_prep.contains(poly):
                return False
        return True

    def can_place(self, c: PlacedBay) -> bool:
        body = bay_body_aabb(c)
        body_gap = bay_gap_aabb(c)

        # 1. Warehouse containment (most expensive check, but essential)
        if not self._body_gap_in_warehouse(body, body_gap):
            return False

        # 2. Obstacles
        for obs in self.obstacle_aabbs:
            if body.overlaps_strict(obs):
                return False
            if body_gap.overlaps_strict(obs):
                return False

        # 3. Ceiling
        if c.bay_type.height > self.ceiling.min_height_in_range(body.x0, body.x1):
            return False

        # 4–5. Other bays
        for ob, og in zip(self.body_aabbs, self.gap_aabbs):
            if body.overlaps_strict(og):
                return False
            if body_gap.overlaps_strict(ob):
                return False

        return True

    def place(self, c: PlacedBay):
        self.placed_bays.append(c)
        self.body_aabbs.append(bay_body_aabb(c))
        self.gap_aabbs.append(bay_gap_aabb(c))


# ─────────────────────────────────────────────
# Greedy scoring criteria
# ─────────────────────────────────────────────

# A criterion is a function (bay_type, effective_width_on_row) -> float.
# Higher is better. The row packer picks the candidate with the highest score
# among all that fit at the current cursor.

def crit_efficiency_x_width(bt: BayType, ew: float) -> float:
    """area * loads / price, weighted by width. Current solver's default."""
    if bt.price <= 0:
        return 0.0
    return (bt.area * bt.n_loads / bt.price) * ew


def crit_coverage_heavy(bt: BayType, ew: float) -> float:
    """Emphasise coverage: prefer wider bays even if slightly less efficient."""
    if bt.price <= 0:
        return 0.0
    return ew * ew * bt.n_loads / bt.price


def crit_loads_per_price(bt: BayType, ew: float) -> float:
    """Pure loads/price. Ignores width — fills with cheapest-per-load."""
    if bt.price <= 0:
        return 0.0
    return bt.n_loads / bt.price


def crit_width(bt: BayType, ew: float) -> float:
    """Widest bay wins. Maximises raw coverage, ignores price."""
    return ew


def crit_biggest_then_cheap(bt: BayType, ew: float) -> float:
    """Biggest bay first; tiebreak by loads/price.
    Score packs them in the right order: width is dominant, price/load is
    tie-breaker at ~1e-6 magnitude.
    """
    tie = (bt.n_loads / bt.price) if bt.price > 0 else 0.0
    return ew * 1000.0 + tie


def crit_area_per_price(bt: BayType, ew: float) -> float:
    """area / price. Coverage per currency — ignores loads."""
    if bt.price <= 0:
        return 0.0
    return bt.area / bt.price


# Pruned set ("moderate" option from benchmark_criteria.py).
# Benchmark over 17 cases (traces/criteria_benchmark.csv) showed:
#   * 'eff×width' wins or ties on 16 / 17 cases.
#   * 'loads/$'   is the only criterion needed to recover Case15_mega.
#   * 'cov-heavy', 'big-first' are redundant with 'eff×width' everywhere.
#   * 'width', 'area/$' never win and sometimes hurt Q a lot.
# Cutting from 6 → 2 criteria saves ~34% wall time with no Q regression.
# Unpruned definitions remain above if we ever want to re-enable them.
CRITERIA: List[Tuple[str, Callable[[BayType, float], float]]] = [
    ('eff×width', crit_efficiency_x_width),
    ('loads/$',   crit_loads_per_price),
]


# ─────────────────────────────────────────────
# Row packing
# ─────────────────────────────────────────────

def pack_row(
    x_start: float, x_end: float, y: float,
    depth: int, rotation_base: int,
    bay_types: List[BayType],
    engine: FastCollisionEngine,
    ceiling: Ceiling,
    criterion: Callable[[BayType, float], float],
) -> None:
    """Fill one row left-to-right using the given criterion.

    rotation_base is 0 (gap up) or 180 (gap down). Bays whose *width* equals
    the row depth are also allowed rotated 90°/270° so they fit the row
    with the gap pointing up or down correspondingly.
    """
    # Candidates: (bay_type, rotation, effective_width_on_row)
    candidates: List[Tuple[BayType, int, float]] = []
    for bt in bay_types:
        if bt.depth == depth:
            candidates.append((bt, rotation_base, bt.width))
        if bt.width == depth:
            # Rotated 90° puts the gap on +X (rot=270 in our model). We want
            # the gap pointing in the same Y direction as rotation_base for
            # alternating-gap packing to work, but since obstacles are
            # irregular a 90°/270° fallback is still useful in narrow
            # slots. We pick whichever matches rotation_base's Y-gap-side.
            #
            # Actually: when the bay is rotated 90°/270°, the gap is on the
            # X-axis, not the Y-axis. That breaks alternating-gap tightness.
            # So we only consider 90°/270° for fallback, not primary picks.
            alt_rot = 270 if rotation_base == 0 else 90
            candidates.append((bt, alt_rot, bt.depth))

    if not candidates:
        return

    x = x_start
    # Safety: prevent infinite loops on degenerate cases
    max_iters = 10000
    iters = 0

    while x < x_end - 0.5 and iters < max_iters:
        iters += 1

        best: Optional[PlacedBay] = None
        best_score = -float('inf')

        # Pass 1: try all criterion-ranked candidates at current cursor
        for bt, rot, ew in candidates:
            if x + ew > x_end + 0.5:
                continue
            # Quick ceiling check before building PlacedBay
            if bt.height > ceiling.min_height_in_range(x, x + ew):
                continue
            score = criterion(bt, ew)
            if score <= best_score:
                continue
            c = PlacedBay(bay_type=bt, x=x, y=y, rotation=rot)
            if engine.can_place(c):
                best = c
                best_score = score

        # Pass 2: if the criterion picked nothing, try narrowest-fits as a
        # fallback so we don't leave tiny fillable gaps.
        if best is None:
            for bt, rot, ew in sorted(candidates, key=lambda c: c[2]):
                if x + ew > x_end + 0.5:
                    continue
                if bt.height > ceiling.min_height_in_range(x, x + ew):
                    continue
                c = PlacedBay(bay_type=bt, x=x, y=y, rotation=rot)
                if engine.can_place(c):
                    best = c
                    break

        if best is None:
            # Can't fit any bay at this x. Advance by the smallest candidate
            # width so we skip past the unusable spot.
            min_w = min(ew for _, _, ew in candidates)
            x += min_w
            continue

        engine.place(best)
        w, _ = best.get_body_dims()
        x += w


# ─────────────────────────────────────────────
# Alternating row Y positions
# ─────────────────────────────────────────────

def alternating_row_ys(
    y_start: float, y_end: float, depth: int, gap: int, start_rot: int
) -> List[Tuple[float, int]]:
    """Compute (y, rotation) for alternating-gap rows.

    start_rot=0  → rows go [rot0 @ y, rot180 @ y+D+G, rot0 @ y+2D+G, …]
    start_rot=180 → rows go [rot180 @ y+G, rot0 @ y+D+G, rot180 @ y+2D+2G, …]
    """
    rows: List[Tuple[float, int]] = []
    if start_rot == 0:
        y = y_start
        rot = 0
        while y + depth <= y_end + 0.5:
            rows.append((y, rot))
            if rot == 0:
                y += depth + gap  # next row's gap points down toward us
                rot = 180
            else:
                y += depth  # gap of previous row pointed down; we're tight to it
                rot = 0
    else:
        # start with gap-down so the first bay's gap sits at [y, y+G]
        y = y_start + gap
        if y + depth > y_end + 0.5:
            # Not enough room for gap-down; fall back to start_rot=0
            return alternating_row_ys(y_start, y_end, depth, gap, 0)
        rot = 180
        while y + depth <= y_end + 0.5:
            rows.append((y, rot))
            if rot == 180:
                y += depth
                rot = 0
            else:
                y += depth + gap
                rot = 180
    return rows


# ─────────────────────────────────────────────
# One-pass strip-pack for a fixed (depth, start_rot, criterion)
# ─────────────────────────────────────────────

def strip_pack_one_pass(
    depth: int,
    gap: int,
    start_rot: int,
    criterion: Callable[[BayType, float], float],
    bay_types: List[BayType],
    warehouse: Polygon,
    obstacles: List[Polygon],
    ceiling: Ceiling,
    free_space,
) -> FastCollisionEngine:
    """Run a single greedy strip-packing pass and return the engine."""
    engine = FastCollisionEngine(warehouse, obstacles, ceiling)
    minx, miny, maxx, maxy = free_space.bounds

    usable_bays = [bt for bt in bay_types if bt.depth == depth or bt.width == depth]
    if not usable_bays:
        return engine

    for y_pos, rot in alternating_row_ys(miny, maxy, depth, gap, start_rot):
        # Find X intervals at this Y row by intersecting with free space
        strip = box(minx, y_pos, maxx, y_pos + depth)
        intersection = free_space.intersection(strip)
        if intersection.is_empty:
            continue

        geoms = []
        if intersection.geom_type == 'Polygon':
            geoms = [intersection]
        elif hasattr(intersection, 'geoms'):
            geoms = list(intersection.geoms)

        for geom in geoms:
            if geom.is_empty or geom.area == 0:
                continue
            gminx, gminy, gmaxx, gmaxy = geom.bounds
            # Only process segments that actually have the full row depth
            if gmaxy - gminy < depth - 0.5:
                continue
            pack_row(
                gminx, gmaxx, y_pos, depth, rot,
                usable_bays, engine, ceiling, criterion,
            )

    return engine


# ─────────────────────────────────────────────
# Vertical variant: transpose the problem, pack, then un-transpose
# ─────────────────────────────────────────────

def _transpose_ceiling(ceiling: Ceiling, warehouse: Polygon) -> Ceiling:
    """For vertical packing we transpose X ↔ Y, which makes the ceiling
    profile run along Y instead of X. Since the ceiling is constrained to
    be piecewise in X only by the input format, a transposed ceiling no
    longer corresponds to a valid input. To handle this correctly we model
    the transposed case as 'ceiling is the MIN ceiling across the whole
    warehouse' — conservative: may disqualify some tall bays in areas that
    would in fact be legal. Acceptable in practice because ceiling drops
    tend to affect only 1–2 cases and this keeps the code simple.
    """
    _, _, _, _ = warehouse.bounds
    # Use a flat ceiling at the minimum observed height as a safe
    # over-approximation. This only affects Case 0 (variable ceiling).
    min_h = min(h for _, h in ceiling.breakpoints)
    return Ceiling(breakpoints=[(-(10**9), min_h)])


def _transpose_placed(pb: PlacedBay) -> PlacedBay:
    """Swap X and Y of a placed bay, and swap rotation accordingly.
    0° ↔ 270°, 90° ↔ 180° (this swaps axes so the gap side becomes the
    correct transposed side).
    """
    rot_map = {0: 270, 270: 0, 90: 180, 180: 90}
    return PlacedBay(
        bay_type=pb.bay_type,
        x=pb.y,
        y=pb.x,
        rotation=rot_map[pb.rotation],
    )


# ─────────────────────────────────────────────
# Deterministic gap-filler — REMOVED
# ─────────────────────────────────────────────
#
# Previously this module implemented fill_gaps_deterministic, an anchor-based
# post-processing step that tried to squeeze additional bays into leftover
# space after the strip-pack matrix. It was removed because
# traces/filler_vs_sa.csv showed:
#   * It cost ~75s total across all 17 cases.
#   * It gained +1664 Q total (Case0 +1632, Case13 +32, rest 0).
#   * LNS+SA applied on the filler-less greedy output recovers those Q pts
#     and then some (-4211 Q total vs the filler baseline, -4067 on
#     Case15_mega alone). Running LNS after the filler vs after no-filler
#     differs by -20 Q total — statistically indistinguishable.
# Conclusion: the filler was redundant with LNS+SA downstream. Removing it
# keeps greedy purely constructive (matrix of strip-pack passes).


# ─────────────────────────────────────────────


# Top-level solver
# ─────────────────────────────────────────────

@dataclass
class RunResult:
    score: float
    n_bays: int
    coverage: float
    label: str
    placed: List[PlacedBay]


def solve_one_case(
    warehouse: Polygon,
    obstacles: List[Polygon],
    ceiling: Ceiling,
    bay_types: List[BayType],
    time_limit: float = 8.0,
    verbose: bool = False,
) -> Tuple[List[PlacedBay], List[RunResult]]:
    """Run the full search matrix and return the best placement + all results."""
    total_start = time.time()
    usable_area_val = usable_area(warehouse, obstacles)
    free_space = compute_free_space(warehouse, obstacles)

    # Search matrix: candidate row-depths
    # A "row depth" is either bt.depth or bt.width (if the bay is rotated 90°
    # to lie sideways across the row).
    depths = set()
    for bt in bay_types:
        depths.add(bt.depth)
        # Only include bt.width as a depth if some other bay's depth matches
        # — otherwise the row can only hold that one bay rotated and won't
        # pack densely. Actually in practice even that is worth trying.
        depths.add(bt.width)

    # Per-depth gap: smallest gap among bays that can sit in that row.
    gap_for_depth: dict = {}
    for bt in bay_types:
        for d in (bt.depth, bt.width):
            gap_for_depth[d] = min(gap_for_depth.get(d, bt.gap), bt.gap)

    # Order depths by "row packing potential" — total width capacity of bays
    # that can fit the row, as a proxy for how much a pass with this depth
    # could cover.
    def depth_potential(d: int) -> float:
        return sum(bt.width + bt.depth for bt in bay_types
                   if bt.depth == d or bt.width == d)
    sorted_depths = sorted(depths, key=depth_potential, reverse=True)

    results: List[RunResult] = []
    best: Optional[Tuple[float, FastCollisionEngine, str]] = None

    # Build a vertical (transposed) version of inputs
    minx, miny, maxx, maxy = warehouse.bounds

    # Transposed warehouse polygon: swap x and y of every vertex
    t_coords = [(y, x) for (x, y) in warehouse.exterior.coords]
    t_warehouse = Polygon(t_coords)
    t_obstacles = [box(oy0, ox0, oy1, ox1)
                   for (ox0, oy0, ox1, oy1)
                   in [o.bounds for o in obstacles]]
    t_ceiling = _transpose_ceiling(ceiling, warehouse)
    t_free_space = compute_free_space(t_warehouse, t_obstacles)

    orientations = [
        ('H', warehouse, obstacles, ceiling, free_space, False),
        ('V', t_warehouse, t_obstacles, t_ceiling, t_free_space, True),
    ]

    total_runs = len(sorted_depths) * 2 * len(CRITERIA) * len(orientations)
    budget_per_run = (time_limit * 0.75) / max(1, total_runs)

    for depth in sorted_depths:
        gap = gap_for_depth[depth]
        for orient_name, wh, obs, ceil, fs, is_transposed in orientations:
            for start_rot in (0, 180):
                for crit_name, crit in CRITERIA:
                    if time.time() - total_start > time_limit * 0.85:
                        break
                    run_start = time.time()
                    eng = strip_pack_one_pass(
                        depth, gap, start_rot, crit,
                        bay_types, wh, obs, ceil, fs,
                    )
                    placed = eng.placed_bays
                    if is_transposed:
                        placed = [_transpose_placed(p) for p in placed]

                    if not placed:
                        continue

                    score = compute_score(placed, usable_area_val)
                    coverage = sum(p.bay_type.area for p in placed) / usable_area_val
                    label = f"{orient_name} d={depth:>5} s{start_rot:>3} {crit_name:>10}"
                    results.append(RunResult(score, len(placed), coverage, label, placed))

                    if best is None or score < best[0]:
                        best = (score, placed, label)
                        if verbose:
                            elapsed = time.time() - total_start
                            print(f"    [greedy] {label}  bays={len(placed):>3} "
                                  f"cov={coverage:.1%} Q={score:>9.2f} ★ t={elapsed:.2f}s")

    # Re-run the best configuration to produce a live engine for gap filling
    if best is None:
        return [], results

    best_score, best_placed, best_label = best

    # Rebuild an engine with those placements (non-transposed coords) so we
    # can run gap-filling on it.
    final_engine = FastCollisionEngine(warehouse, obstacles, ceiling)
    for pb in best_placed:
        # All cached placements are already in original warehouse coords
        if final_engine.can_place(pb):
            final_engine.place(pb)

    # Gap-filler removed: benchmark_filler_vs_sa.py showed LNS+SA downstream
    # already recovers the +1664 Q pts the filler contributed, and the filler
    # cost ~75s across all cases. See traces/filler_vs_sa.csv.

    final_score = compute_score(final_engine.placed_bays, usable_area_val)
    if verbose:
        print(f"    [greedy] best: {best_label} Q={best_score:.2f}")
        print(f"    [greedy] final: Q={final_score:.2f} "
              f"({len(final_engine.placed_bays)} bays)")

    return final_engine.placed_bays, results


# ─────────────────────────────────────────────
# Output & CLI
# ─────────────────────────────────────────────

def write_output(placed_bays: List[PlacedBay], filepath: str):
    with open(filepath, 'w') as f:
        for pb in placed_bays:
            f.write(f"{pb.bay_type.id}, {int(pb.x)}, {int(pb.y)}, {pb.rotation}\n")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 greedy_solver.py <case_directory> [output_file]")
        sys.exit(1)

    case_dir = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else 'solution.csv'
    verbose = os.environ.get('GREEDY_VERBOSE', '0') == '1'

    total_start = time.time()
    print(f"[1] Parsing {case_dir}...")
    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    ceiling = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))
    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))

    wh_area = warehouse.area
    ua = usable_area(warehouse, obstacles)
    print(f"    WH={wh_area:.0f}  Obs={wh_area-ua:.0f}  Usable={ua:.0f}  "
          f"BayTypes={len(bay_types)}  CeilingBPs={len(ceiling.breakpoints)}")

    print(f"[2] Multi-criteria greedy search (whole-warehouse)...")
    placed_whole, _ = solve_one_case(
        warehouse, obstacles, ceiling, bay_types,
        time_limit=4.0, verbose=verbose,
    )
    q_whole = compute_score(placed_whole, ua)
    cov_whole = sum(p.bay_type.area for p in placed_whole) / ua if ua > 0 else 0
    print(f"    whole: {len(placed_whole)} bays, cov={cov_whole:.1%}, Q={q_whole:.2f}")

    print(f"[3] Region decomposition search...")
    placed_regional, rects = solve_one_case_regional(
        warehouse, obstacles, ceiling, bay_types,
        time_limit=4.0, verbose=verbose,
    )
    q_regional = compute_score(placed_regional, ua) if placed_regional else float('inf')
    cov_regional = (sum(p.bay_type.area for p in placed_regional) / ua
                    if ua > 0 and placed_regional else 0)
    print(f"    regional: {len(placed_regional)} bays in {len(rects)} region(s), "
          f"cov={cov_regional:.1%}, Q={q_regional:.2f}")

    # Pick the better of the two
    if q_regional < q_whole:
        placed = placed_regional
        winner = 'regional'
    else:
        placed = placed_whole
        winner = 'whole'

    write_output(placed, output_file)

    final_score = compute_score(placed, ua)
    coverage = sum(p.bay_type.area for p in placed) / ua if ua > 0 else 0
    total_time = time.time() - total_start

    print(f"\n{'='*60}")
    print(f"  {output_file}: {len(placed)} bays, "
          f"cov={coverage:.1%}, Q={final_score:.2f}, {total_time:.2f}s "
          f"[winner: {winner}]")
    print(f"{'='*60}")


# ─────────────────────────────────────────────
# Region decomposition — rectilinear polygon → axis-aligned rectangles
# ─────────────────────────────────────────────

def decompose_into_rectangles(
    warehouse: Polygon,
    obstacles: List[Polygon],
) -> List[Tuple[float, float, float, float]]:
    """Decompose the warehouse (minus obstacles) into axis-aligned rectangles.

    Algorithm: take all unique X and Y coordinates from warehouse vertices
    and obstacle corners. These create a grid of cells. Each cell is either
    entirely inside the usable region or entirely outside. Then merge
    adjacent inside-cells into maximal rectangles via a greedy row-sweep.

    This produces at most O(n·m) rectangles (n=unique X, m=unique Y). For
    our 4 cases, this yields 1-5 rectangles per warehouse.

    Returns: list of (x0, y0, x1, y1) tuples.
    """
    # Collect all unique X and Y coordinates from warehouse and obstacles
    xs = set()
    ys = set()
    for (px, py) in warehouse.exterior.coords:
        xs.add(px)
        ys.add(py)
    for obs in obstacles:
        x0, y0, x1, y1 = obs.bounds
        xs.add(x0); xs.add(x1); ys.add(y0); ys.add(y1)

    xs_sorted = sorted(xs)
    ys_sorted = sorted(ys)

    # Classify each cell: inside usable region (inside warehouse, outside obstacles)?
    # We use the cell's centroid for an unambiguous point-in-polygon test.
    warehouse_prep = prep(warehouse)

    def cell_usable(x0: float, y0: float, x1: float, y1: float) -> bool:
        cx = (x0 + x1) / 2
        cy = (y0 + y1) / 2
        from shapely.geometry import Point
        pt = Point(cx, cy)
        if not warehouse_prep.contains(pt):
            return False
        for obs in obstacles:
            if obs.contains(pt):
                return False
        return True

    n_cols = len(xs_sorted) - 1
    n_rows = len(ys_sorted) - 1
    # grid[row][col] = True if cell is usable
    grid: List[List[bool]] = []
    for r in range(n_rows):
        row = []
        for c in range(n_cols):
            x0, x1 = xs_sorted[c], xs_sorted[c + 1]
            y0, y1 = ys_sorted[r], ys_sorted[r + 1]
            # Skip degenerate cells
            if x1 - x0 < 1 or y1 - y0 < 1:
                row.append(False)
            else:
                row.append(cell_usable(x0, y0, x1, y1))
        grid.append(row)

    # Greedy rectangle extraction: for each row, find contiguous True runs,
    # then try to extend downward while the columns stay True.
    rectangles: List[Tuple[float, float, float, float]] = []
    used = [[False] * n_cols for _ in range(n_rows)]

    for r in range(n_rows):
        c = 0
        while c < n_cols:
            if not grid[r][c] or used[r][c]:
                c += 1
                continue
            # Extend horizontally first
            c_end = c
            while c_end < n_cols and grid[r][c_end] and not used[r][c_end]:
                c_end += 1
            # Extend downward while ALL columns in [c, c_end) remain True
            r_end = r + 1
            while r_end < n_rows:
                if all(grid[r_end][cc] and not used[r_end][cc]
                       for cc in range(c, c_end)):
                    r_end += 1
                else:
                    break
            # Mark these cells as used
            for rr in range(r, r_end):
                for cc in range(c, c_end):
                    used[rr][cc] = True
            # Record rectangle in world coords
            x0 = xs_sorted[c]
            x1 = xs_sorted[c_end]
            y0 = ys_sorted[r]
            y1 = ys_sorted[r_end]
            rectangles.append((x0, y0, x1, y1))
            c = c_end

    return rectangles


# ─────────────────────────────────────────────
# Per-region greedy pack
# ─────────────────────────────────────────────

def _pack_region(
    region: Tuple[float, float, float, float],
    bay_types: List[BayType],
    ceiling: Ceiling,
    existing_engine: FastCollisionEngine,
    placed_so_far: List[PlacedBay],
    time_budget: float,
) -> List[PlacedBay]:
    """Pack one rectangular region, treating already-placed bays and their
    gaps as obstacles. Try both orientations and pick the one with better
    per-region coverage × price/loads efficiency.
    """
    rx0, ry0, rx1, ry1 = region
    r_width = rx1 - rx0
    r_height = ry1 - ry0
    if r_width < 500 or r_height < 500:
        return []

    # Build a synthetic warehouse for this region: the rectangle
    # Obstacles inside the region: any obstacle that overlaps + gap zones of
    # already-placed bays (we model bodies as hard obstacles; gaps we treat
    # as obstacles too because they occupy space that new bays can't take).
    region_poly = box(rx0, ry0, rx1, ry1)

    synthetic_obstacles: List[Polygon] = []
    for obs in existing_engine.obstacles:
        if obs.intersects(region_poly) and not obs.touches(region_poly):
            synthetic_obstacles.append(obs.intersection(region_poly))

    # Add already-placed bay bodies and gaps as obstacles
    for pb in placed_so_far:
        body = pb.get_body_polygon()
        gap = pb.get_body_with_gap_polygon()
        if body.intersects(region_poly) and not body.touches(region_poly):
            synthetic_obstacles.append(body.intersection(region_poly))
        # gap is trickier: other bays' gaps can overlap, but NEW bay bodies
        # can't be in another bay's gap. For simplicity, treat the gap as
        # a no-go for new bay bodies. We're a bit conservative — may miss
        # valid placements where a new bay's own gap would share with this
        # gap — but that's rare and easy to recover with the post-fill step.
        if gap.intersects(region_poly) and not gap.touches(region_poly):
            gap_in_region = gap.intersection(region_poly)
            if not gap_in_region.is_empty:
                synthetic_obstacles.append(gap_in_region)

    # Flatten MultiPolygons to Polygons
    flat_obs: List[Polygon] = []
    for so in synthetic_obstacles:
        if so.is_empty:
            continue
        if so.geom_type == 'Polygon':
            flat_obs.append(so)
        elif hasattr(so, 'geoms'):
            for g in so.geoms:
                if g.geom_type == 'Polygon' and not g.is_empty:
                    flat_obs.append(g)

    # Try both orientations on this region
    best_placements: List[PlacedBay] = []
    best_q_contribution = float('inf')

    for orient in ('H', 'V'):
        region_results = _pack_region_oriented(
            region_poly, flat_obs, ceiling, bay_types, orient,
        )
        if not region_results:
            continue
        # Score this region's contribution independently: just the ratio
        # we'd see if these were the only bays placed.
        total_price = sum(p.bay_type.price for p in region_results)
        total_loads = sum(p.bay_type.n_loads for p in region_results)
        total_area = sum(p.bay_type.area for p in region_results)
        region_area = r_width * r_height
        local_cov = total_area / region_area
        # Local quality: price/load weighted by how much area it gave us
        if total_loads <= 0 or total_price <= 0:
            continue
        local_quality = (total_price / total_loads) / (local_cov + 0.01)
        if local_quality < best_q_contribution:
            best_q_contribution = local_quality
            best_placements = region_results

    return best_placements


def _pack_region_oriented(
    region_poly: Polygon,
    obstacles: List[Polygon],
    ceiling: Ceiling,
    bay_types: List[BayType],
    orientation: str,
) -> List[PlacedBay]:
    """Do a multi-criteria greedy strip-pack inside a rectangular region.

    orientation = 'H' (horizontal strips) or 'V' (vertical strips, achieved
    by transposing the region).
    """
    if orientation == 'V':
        # Transpose: swap x and y of region and obstacles
        minx, miny, maxx, maxy = region_poly.bounds
        t_region = box(miny, minx, maxy, maxx)
        t_obs = [box(oy0, ox0, oy1, ox1)
                 for (ox0, oy0, ox1, oy1) in [o.bounds for o in obstacles]]
        t_ceiling = _transpose_ceiling(ceiling, region_poly)
        fs = compute_free_space(t_region, t_obs)
    else:
        t_region = region_poly
        t_obs = obstacles
        t_ceiling = ceiling
        fs = compute_free_space(t_region, t_obs)

    if fs.is_empty:
        return []

    # Try 2 depths × 2 start_rot × 3 criteria = 12 fast passes per region
    depths = set()
    for bt in bay_types:
        depths.add(bt.depth)
        depths.add(bt.width)
    gap_for_depth: dict = {}
    for bt in bay_types:
        for d in (bt.depth, bt.width):
            gap_for_depth[d] = min(gap_for_depth.get(d, bt.gap), bt.gap)

    # Reduced criteria set for per-region search. After pruning CRITERIA
    # down to 2, slicing keeps both; left as a slice so re-enabling more
    # criteria doesn't blow up region search time.
    region_criteria = CRITERIA[:3]

    best_eng: Optional[FastCollisionEngine] = None
    best_score = float('inf')

    for depth in sorted(depths, reverse=True):
        gap = gap_for_depth[depth]
        for start_rot in (0, 180):
            for crit_name, crit in region_criteria:
                eng = strip_pack_one_pass(
                    depth, gap, start_rot, crit,
                    bay_types, t_region, t_obs, t_ceiling, fs,
                )
                if not eng.placed_bays:
                    continue
                # Local score
                placed = eng.placed_bays
                total_price = sum(p.bay_type.price for p in placed)
                total_loads = sum(p.bay_type.n_loads for p in placed)
                total_area = sum(p.bay_type.area for p in placed)
                region_area = t_region.area
                local_cov = total_area / region_area if region_area > 0 else 0
                if total_loads <= 0:
                    continue
                local_q = (total_price / total_loads) ** (2 - local_cov)
                if local_q < best_score:
                    best_score = local_q
                    best_eng = eng

    if best_eng is None:
        return []

    # Un-transpose if needed
    if orientation == 'V':
        return [_transpose_placed(p) for p in best_eng.placed_bays]
    else:
        return list(best_eng.placed_bays)


# ─────────────────────────────────────────────
# Region-based solver entry point
# ─────────────────────────────────────────────

def solve_one_case_regional(
    warehouse: Polygon,
    obstacles: List[Polygon],
    ceiling: Ceiling,
    bay_types: List[BayType],
    time_limit: float = 8.0,
    verbose: bool = False,
) -> Tuple[List[PlacedBay], List[Tuple[float, float, float, float]]]:
    """Decompose the warehouse into rectangles, pack each independently.

    Try multiple processing orders and keep the best.
    """
    total_start = time.time()
    usable_area_val = usable_area(warehouse, obstacles)

    rectangles = decompose_into_rectangles(warehouse, obstacles)
    if verbose:
        print(f"    [region] decomposed into {len(rectangles)} rectangles")
        for i, r in enumerate(rectangles):
            print(f"    [region]   #{i}: ({r[0]:.0f},{r[1]:.0f})-({r[2]:.0f},{r[3]:.0f}) "
                  f"size={r[2]-r[0]:.0f}×{r[3]-r[1]:.0f}")

    # Candidate orderings: largest-first and by position (Y then X)
    def area_of(r):
        return (r[2] - r[0]) * (r[3] - r[1])

    orderings = [
        sorted(range(len(rectangles)), key=lambda i: -area_of(rectangles[i])),
        sorted(range(len(rectangles)), key=lambda i: (rectangles[i][1], rectangles[i][0])),
        list(range(len(rectangles))),  # as-given
    ]
    # Dedup orderings
    seen = set()
    unique_orderings = []
    for o in orderings:
        t = tuple(o)
        if t not in seen:
            seen.add(t)
            unique_orderings.append(o)

    best_placements: List[PlacedBay] = []
    best_q = float('inf')

    per_ordering_budget = (time_limit * 0.7) / max(1, len(unique_orderings))

    for ordering in unique_orderings:
        if time.time() - total_start > time_limit * 0.85:
            break

        # Start with an engine containing no placements
        engine = FastCollisionEngine(warehouse, obstacles, ceiling)
        placed: List[PlacedBay] = []

        order_start = time.time()
        for ri in ordering:
            if time.time() - order_start > per_ordering_budget:
                break
            region = rectangles[ri]
            region_placements = _pack_region(
                region, bay_types, ceiling, engine, placed,
                per_ordering_budget / len(ordering),
            )
            # Filter: double-check each against the live engine
            for pb in region_placements:
                if engine.can_place(pb):
                    engine.place(pb)
                    placed.append(pb)

        if not placed:
            continue

        q = compute_score(placed, usable_area_val)
        if verbose:
            cov = sum(p.bay_type.area for p in placed) / usable_area_val
            elapsed = time.time() - total_start
            marker = '★' if q < best_q else ' '
            print(f"    [region] order={ordering} "
                  f"bays={len(placed)} cov={cov:.1%} Q={q:.2f} t={elapsed:.2f}s {marker}")

        if q < best_q:
            best_q = q
            best_placements = placed

    # Gap-filler removed (see solve_one_case). Just rebuild a clean engine
    # with the winning placements and return.
    if best_placements:
        final_engine = FastCollisionEngine(warehouse, obstacles, ceiling)
        for pb in best_placements:
            if final_engine.can_place(pb):
                final_engine.place(pb)
        return final_engine.placed_bays, rectangles

    return best_placements, rectangles



if __name__ == '__main__':
    main()
