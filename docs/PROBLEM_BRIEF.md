# Warehouse Bay Placement Optimization (HackUPC 2026 — Mecalux)

This document provides a detailed description of the contents of [Problem Statement Mecalux 2026.pdf](Problem%20Statement%20Mecalux%202026.pdf)


## Overview

Place storage shelves ("bays") inside an axis-aligned warehouse to minimize a cost metric $Q$. Bays are rectangles chosen from a catalog of types (each type can be used unlimited times and rotated). The warehouse may be a non-convex rectilinear polygon, contains rectangular obstacles, and has a variable ceiling height along the X axis. Each bay requires a free "gap" (aisle) on one of its sides to access its contents.

There is a time limit of 30s of execution per case.

Function to minimize is:

$$Q = \left( \frac{\sum_{\text{bay}} \text{price}}{\sum_{\text{bay}} \text{loads}} \right)^{2 - \text{PercentageAreaUsed}}$$


## Inputs (CSV files per case)

1. **warehouse.csv**: list of `(x, y)` vertices defining a closed axis-aligned rectilinear polygon (edges are horizontal or vertical). Example (L-shape):
   ```csv
   0,0
   10000,0
   10000,3000
   3000,3000
   3000,10000
   0,10000
   ```

2. **obstacles.csv** — rectangles `(x, y, width_x, depth_y)` representing columns/walls where bays (and gaps) cannot be placed. May be empty. Example:
   ```csv
   750, 750, 750, 750
   8000, 2500, 1500, 300
   1500, 4200, 200, 4600
   ```

3. **ceiling.csv** — piecewise-constant height along X as `(x_start, height)` pairs. The height at X is given by the last entry with `x_start ≤ X`. Example:
   ```csv
   0, 3000
   3000, 2000
   6000, 3000
   ```
   means `height=3000` for `x ∈ [0,3000)`, `height=2000` for `x ∈ [3000,6000)`, `height=3000` for `x ≥ 6000`.

4. **types_of_bays.csv** — catalog `(id, width, depth, height, gap, nLoads, price)`. Example:
   ```csv
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
- `rotation ∈ [0, 360]` degrees. Two "rotations" determine orientation — the gap side rotates too.

Bays of the same type can be placed unlimited times.


## Output

CSV, one line per placed bay: `id, x, y, rotation`. Example:
```csv
5, 1600, 0, 0
5, 4000, 0, 0
5, 6400, 0, 0
3, 8800, 0, 0
5, 1600, 1150, 180
...
```
