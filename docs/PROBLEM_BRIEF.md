# Warehouse Bay Placement Optimization (HackUPC 2026 — Mecalux)

## Overview

Place storage shelves ("bays") inside an axis-aligned warehouse to minimize a cost metric `Q`. Bays are rectangles chosen from a catalog of types (each type can be used unlimited times and rotated). The warehouse may be a non-convex rectilinear polygon, contains rectangular obstacles, and has a variable ceiling height along the X axis. Each bay requires a free "gap" (aisle) on one of its sides to access its contents.

**Hard time budget:** ~10s per case, ~30s total across 4 cases.

## Inputs (CSV files per case)

1. **warehouse.csv** — list of `(x, y)` vertices defining a closed axis-aligned rectilinear polygon (edges are horizontal or vertical). Example (L-shape):
   ```
   0,0
   10000,0
   10000,3000
   3000,3000
   3000,10000
   0,10000
   ```

2. **obstacles.csv** — rectangles `(x, y, width_x, depth_y)` representing columns/walls where bays (and gaps) cannot be placed. May be empty. Example:
   ```
   750, 750, 750, 750
   8000, 2500, 1500, 300
   1500, 4200, 200, 4600
   ```

3. **ceiling.csv** — piecewise-constant height along X as `(x_start, height)` pairs. The height at X is given by the last entry with `x_start ≤ X`. Example:
   ```
   0, 3000
   3000, 2000
   6000, 3000
   ```
   means `height=3000` for `x ∈ [0,3000)`, `height=2000` for `x ∈ [3000,6000)`, `height=3000` for `x ≥ 6000`.

4. **types_of_bays.csv** — catalog `(id, width, depth, height, gap, nLoads, price)`. Example:
   ```
   0,  800, 1200, 2800, 200,  4, 2000
   1, 1600, 1200, 2800, 200,  8, 2500
   2, 2400, 1200, 2800, 200, 12, 2800
   3,  800, 1000, 1800, 150,  3, 1800
   4, 1600, 1000, 1800, 150,  6, 2300
   5, 2400, 1000, 1800, 150,  9, 2600
   ```

   - `width` is along the bay's local X, `depth` is along local Y.
   - `gap` is a mandatory aisle extending from the `+Y` side of the bay (in local coordinates) with the same width as the bay and depth `gap`. When rotated, the gap rotates with the bay.
   - `height` is the vertical (Z) extent of the bay; must not exceed ceiling height anywhere in its footprint.
   - `nLoads` is capacity, `price` is cost.

## Decision variables

For each placed bay: a tuple `(type_id, x, y, rotation)` where:
- `(x, y)` is the bay's reference corner in warehouse coordinates.
- `rotation ∈ {0, 90, 180, 270}` degrees. Two "rotations" determine orientation — the gap side rotates too.

Bays of the same type can be placed unlimited times.

## Constraints

1. **Ceiling:** `bay.height ≤ ceiling(x)` for **every** X in the bay's footprint (i.e. ≤ min ceiling across the footprint).
2. **Inside warehouse:** the full bay rectangle and its gap must lie inside the warehouse polygon.
3. **Bay–bay no overlap:** two bay rectangles cannot overlap.
4. **Gap–gap overlap allowed:** gaps may overlap each other.
5. **Gaps must exist and be respected:** the gap rectangle of each bay must be fully valid (inside warehouse, not intersecting obstacles or other bays — but can share space with other gaps).
6. **Bay–obstacle no overlap:** bay rectangles cannot intersect any obstacle. (Gaps also cannot intersect obstacles.)
7. Bays may touch the warehouse boundary (e.g. start at `(0,0)`).
8. Warehouse is axis-aligned.
9. Bays and obstacles are strictly rectangular.
10. Each catalog `id` is a *type*; instances may be repeated.
11. Gaps cannot lie outside the warehouse.

## Objective

Minimize

```
Q = (sum_price / sum_loads) ^ (2 - coverage)
```

where `coverage = sum(bay_area) / warehouse_area` and areas consider only the bay footprint (not the gap). Lower `Q` is better. Intuition: push the price-per-load down and the coverage up.

## Output

CSV, one line per placed bay: `id, x, y, rotation`. Example (Case 0):
```
5, 1600, 0, 0
5, 4000, 0, 0
5, 6400, 0, 0
3, 8800, 0, 0
5, 1600, 1150, 180
...
```

## Performance target

Solve each case in ~10s on a laptop. For reference, a known baseline achieves:

| Case | Bays | Coverage | Q     |
|------|------|----------|-------|
| 0    | 16   | 64%      | 2,363 |
| 1    | 24   | 79%      | 1,340 |
| 2    | 20   | 58%      | 4,945 |
| 3    | 44   | 59%      | 4,135 |

## Notes / approach ideas already explored

- **Strip packing with alternating gaps:** pack rows of bays alternating rotation `0°` (gap up) and `180°` (gap down) so aisles can share space and row pitch alternates between `depth` and `depth + gap`.
- **Ceiling-aware placement:** when the ceiling drops below a type's height over part of the footprint, fall back to shorter types.
- **Gap filling:** generate candidate anchors from bay edges, obstacle borders, and warehouse vertices; insert additional bays if they improve Q.
- **LNS + Simulated Annealing over bitmaps:** represent occupancy (`MO`), gaps (`MG`), and ceiling heights (`MH`) as bitmaps. Destroy a region, then repair using:
  1. Convolutional candidate generation (kernels for bay+gap and bay-only at the 4 rotations; valid positions are where `Conv(K_{BG}, MO) == 0` and `Conv(K_B, MG) == 0`, plus a ceiling check `min(MH in footprint) ≥ bay.height`).
  2. Morphological pruning / greedy non-overlapping selection.
  3. Bitmask ops for fast placement/removal (`mask & shifted`, `mask |= shifted`, `mask ^= shifted`).
- Accept worse solutions probabilistically per SA; update temperature per iteration.

## What an LLM-proposed solution should include

1. A concrete algorithm (pseudocode) that fits the 10s budget.
2. Data structures for: warehouse polygon, obstacle set, ceiling function, occupancy/gap bitmaps.
3. Feasibility checks for all 11 constraints above.
4. An objective evaluator computing `Q` given a set of placements.
5. An output writer matching the `id, x, y, rotation` CSV format.
6. Handling of rotations (including gap side rotation).
7. Optional: multi-start, diversification, SA schedule, and how coverage vs price-per-load are traded off.
