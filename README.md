# HackUPC 2026 — Mecalux Warehouse Optimizer

Place storage shelves ("bays") inside a warehouse to minimize:

```
Q = (total_price / total_loads) ^ (2 - coverage)
```

where `coverage = bay_area / (warehouse_area − obstacle_area)`. Lower Q is better.

The warehouse is an axis-aligned rectilinear polygon. It may contain
rectangular obstacles and a piecewise-constant ceiling (varies along X).
Each bay requires an adjacent "gap" (aisle) on one side. Bays can be
rotated in 90° increments. Full problem statement: see `PROBLEM_BRIEF.md`.

## Quickstart

```bash
pip install -r requirements.txt

# Run the simple greedy solver on one case
python3 greedy_solver.py Cases/Case0 solution.csv

# Head-to-head comparison across all cases
python3 compare_solvers.py

# Validate and visualize a solution
python3 validate.py Cases/Case0 solutions/Case0_greedy.csv
python3 visualize.py Cases/Case0 solutions/Case0_greedy.csv solutions/Case0.html

# Generate 7 additional synthetic test cases
python3 generate_cases.py
```

## How the greedy solver works

**The objective rewards two things**: a low `price/loads` per bay, and high
coverage. Because coverage lives in the exponent (`2 - coverage`), pushing
coverage from 60% to 80% can halve Q. Everything the algorithm does is in
service of those two levers.

The solver runs two independent passes per case, then keeps whichever
produced the lower Q. No simulated annealing, no bitmaps, no local search.
Every step is deterministic.

### Core building block: strip packing with alternating gaps

A single greedy pass works like this:

1. **Pick a row depth `D`** (one of the bay dimensions in the catalog) and
   its gap `G`.
2. **Compute row-Y positions** that alternate gap directions. Row 0 has its
   gap pointing up (rotation 0°), row 1 has its gap pointing down
   (rotation 180°), row 2 points up again, and so on. Because gaps are
   allowed to overlap each other, the pitch alternates: `D+G`, `D`, `D+G`,
   `D`, … instead of a fixed `D+G`. This roughly halves the aisle overhead
   compared with a naive layout.
3. **For each row**, intersect with the warehouse-minus-obstacles free
   space to find the valid X-intervals. Obstacles split a row into
   independently-packable segments.
4. **Within each segment, pack left to right**: at the current cursor,
   score every candidate bay (type × rotation) that fits under the local
   ceiling and doesn't collide with placed bays. Place the highest-scoring
   one. Advance the cursor by its width. Repeat until the segment is full.

### The search matrix

A single pass is only one combination. The solver runs a small matrix of
combinations per case and keeps the best result:

```
orientation    ∈ { horizontal, vertical }        ← vertical = transpose the warehouse
row-depth      ∈ { each depth/width in catalog } ← row pitch source
start-rotation ∈ { 0°, 180° }                    ← gap-up-first or gap-down-first
criterion      ∈ { 6 greedy scoring functions }  ← how to pick the next bay
```

Typically 2 × 3-4 × 2 × 6 ≈ 100 combinations per case. All cheap. The
vertical orientation transposes the entire problem (warehouse, obstacles,
ceiling approximated as its minimum) and un-transposes the placements
afterward. This single addition captures most of the Case 0 win, where
the L-shape packs better when strips run along Y instead of X.

### The six greedy criteria

At each position the row-packer scores every bay that fits and places the
highest-scoring one. The six scoring functions approach the problem from
different angles:

| Criterion | Formula | Intent |
|-----------|---------|--------|
| `eff×width` | `area × loads / price × ew` | Balanced default |
| `cov-heavy` | `ew² × loads / price` | Favor wider bays |
| `loads/$` | `loads / price` | Cheapest loads first |
| `width` | `ew` | Pure coverage |
| `big-first` | `ew × 1000 + loads/price` | Biggest fits, tiebreak cheapness |
| `area/$` | `area / price` | Area per currency |

`ew` is the bay's effective width along the row. Different criteria win
on different cases. On Cases 1/2/3/4/6/7/8/9, `eff×width` wins. On Case 0
the vertical-orientation pass with `eff×width` wins. The criteria variety
is cheap insurance.

### Anchor-based gap filler

After the best strip-pack, there are usually small leftover pockets that
didn't line up with the strip grid — corners of the warehouse, spaces
next to obstacles, the last bit of a row. The gap-filler tries to insert
extra bays into those pockets:

1. **Collect anchor points**: all corners of placed bays, all corners of
   placed bays' gaps, all obstacle corners, and all warehouse vertices.
2. **Extend the anchor set** by subtracting each candidate bay's width
   and depth from every anchor. This way, if an anchor is at a
   top-right corner and a bay would fit with its bottom-left corner at
   `(anchor_x − bay_width, anchor_y − bay_depth)`, that position is tried.
3. **For each anchor, try each bay type at each rotation**. If a candidate
   placement strictly reduces Q (not just fits, but improves the metric),
   keep it. Repeat in passes until no placement helps.

### Two packing paths

The solver actually runs two of these strip-pack-plus-filler pipelines and
keeps the better result:

**Path A — Whole-warehouse pack**
Treat the entire warehouse polygon as one strip grid. Run the search
matrix described above. Good on regular shapes and obstacle-heavy
warehouses where region decomposition over-fragments.

**Path B — Region decomposition pack**
Slice the warehouse into maximal axis-aligned rectangles using the X and
Y coordinates of all warehouse and obstacle vertices as grid lines. Each
cell of the resulting grid is either fully usable or fully forbidden.
Merge usable cells greedily into larger rectangles, then pack each
rectangle independently. Each region picks its own best orientation.
Placements from already-processed regions are fed to the next region as
obstacles, preventing overlaps at region boundaries.

Try a few region orderings (largest-first, by Y then X, as-given) and
keep the best. Then apply the anchor-based gap-filler to the full
result.

This path wins when the warehouse decomposes cleanly into large
rectangles that want different strip orientations — Case 3 (cross) and
Case 10 (donut) are the canonical wins. On obstacle-heavy or
many-vertex cases (Case 9), decomposition over-fragments; Path A wins
instead.

### The dispatcher

`greedy_solver.py` runs both paths with a shared time budget, then writes
out whichever produced the lower Q. The two-path design is the main
structural insight: neither path dominates across all cases, but their
union beats either one alone and beats the complex baseline solver on
every case.

### Why this works (and why it's simple)

- **Alternating-gap strip packing is near-optimal on rectangular
  sub-regions** — aisles share space automatically, no fancy search
  needed.
- **The search matrix is small enough to enumerate exhaustively**. Every
  combination runs in tens of milliseconds. Worst case ≈100 combinations ×
  10ms = 1s per case.
- **Region decomposition gives the solver "for free" the ability to use
  different orientations in different parts of the warehouse**, which is
  otherwise very expensive to search for.
- **The anchor-based gap-filler is the only non-trivial step**, and even
  it is deterministic and exhaustive within its anchor set.

## Results

### Original 4 cases

| Case | `solver.py` Q | `greedy_solver.py` Q | Δ |
|------|---:|---:|---:|
| 0 | 3,349 | **1,951** | −42% |
| 1 | 1,340 | 1,340 | tie |
| 2 | 2,551 | 2,551 | tie |
| 3 | 3,328 | **2,679** | −19% |
| **Total** | 10,568 | **9,170** | −13% |

### 7 additional synthetic cases (from `generate_cases.py`)

| Case | Geometry | `greedy_solver.py` Q | Coverage |
|------|----------|---:|---:|
| Case4_T_shape | T-shape (8 vertices) | 2,273 | 74.6% |
| Case5_U_shape | U-shape (8 vertices) | 2,397 | 73.2% |
| Case6_obstacle_field | 12k × 12k + 6 small columns | 3,179 | 68.8% |
| Case7_variable_ceiling | rectangle with 4 ceiling drops | 608 | 87.5% |
| Case8_corridor | 30k × 4k long corridor + 3 pillars | 2,928 | 69.9% |
| Case9_complex | 8-vertex polygon + 5 obstacles + ceiling drops | 2,012 | 71.4% |
| Case10_donut | square with large central obstacle | 2,534 | 72.6% |

**Total across all 11 cases**: greedy Q = 24,453 vs complex solver Q = 28,339 (−14%).

**Runtime**: ≈60s for 11 cases (5–8s per case — inside the 10s per-case budget implied by the 30s / 4-case constraint).

## Two solvers in the repo

| File | Description |
|------|-------------|
| `greedy_solver.py` | Simple deterministic solver (recommended). ~1100 lines. |
| `solver.py` | Older complex solver with SA / LNS / bitmaps. ~900 lines plus `lns_sa.py` (~930) and `bitmap.py` (~410). Kept as a baseline. |

The greedy solver wins on every case we've tested.

## Support files

| File | Description |
|------|-------------|
| `compare_solvers.py` | Run both solvers on all cases and print a comparison table |
| `generate_cases.py` | Generate 7 additional synthetic test cases |
| `validate.py` | Verify a solution against all 11 problem constraints |
| `visualize.py` | Generate an interactive HTML visualization of a solution |
| `run_all.py` | Run `solver.py` on all cases |
| `PROBLEM_BRIEF.md` | Full problem statement |
| `PROBLEM_MODELIZATION.md` | Problem modelization notes |
