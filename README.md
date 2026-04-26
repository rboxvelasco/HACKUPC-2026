# HackUPC 2026 — Mecalux Warehouse Optimizer

Place storage shelves ("bays") inside a warehouse to minimize

```
Q = (total_price / total_loads) ^ (2 − coverage)
coverage = total_bay_area / (warehouse_area − obstacle_area)
```

Lower Q is better. Because `coverage` lives in the exponent, pushing coverage
from 60% to 80% can halve Q — the algorithm spends most of its effort on that
lever, not on picking slightly cheaper bays.

The warehouse is an axis-aligned rectilinear polygon. It may contain
rectangular obstacles and a piecewise-constant ceiling that varies along X.
Each bay requires an adjacent "gap" (aisle) on one side, and bays can be
rotated in 90° increments. Full problem statement: `PROBLEM_BRIEF.md`.

---

## Executive Summary

We used a greedy algorithm that evaluates several criteria and picks the most favorable one to generate an initial solution. From there, we apply a local search algorithm that combines **Large Neighborhood Search** (LNS) with **simulated annealing**, letting us explore the solution space efficiently and avoid getting stuck in local optima.

To operate on the full state nimbly, we went with a bitmap-based representation, which lets us carry out every operation via bitmasks and standard **computer-vision** techniques, simplifying how the overall state is handled. Additionally, within LNS, the region to destroy and rebuild is selected via **convolutions** that identify areas with higher or lower shelf density, thereby guiding the solution-improvement process.

---

## 1. Quickstart

```bash
pip install -r requirements.txt

# End-to-end pipeline on one case: greedy → LNS+SA → HTML comparison
python3 pipeline.py Cases/Case0

# Same, with a longer SA budget (default 30s)
python3 pipeline.py Cases/Case0 --lns-time 60

# All cases at once
python3 pipeline.py --all

# Individual stages
python3 greedy_solver.py Cases/Case0 solution.csv
python3 lns_sa.py        Cases/Case0 solution.csv solution_lns.csv
python3 visualize.py     Cases/Case0 solution_lns.csv -o solution.html
python3 validate.py      Cases/Case0 solution_lns.csv
```

The pipeline writes CSVs and HTMLs to `solutions/` by default and opens the
compare HTML in a browser unless `--no-open` is passed.

---

## 2. Repository layout

| File | What it does |
|------|--------------|
| `pipeline.py` | Orchestrator: greedy → LNS+SA → compare HTML, per case |
| `greedy_solver.py` | Constructive phase (deterministic, ~1 s per case) |
| `lns_sa.py` | Refinement phase: Large Neighborhood Search + Simulated Annealing over bitmaps |
| `bitmap.py` | Rasterizes a solution into boolean bitmaps used by `lns_sa.py` |
| `solver.py` | Problem data model, parsers, `compute_score`, legacy solver (kept as baseline) |
| `validate.py` | Full Shapely-based constraint check on a solution CSV |
| `visualize.py` | Interactive HTML renderer (supports a side-by-side compare) |
| `generate_cases.py` | Synthetic test cases |
| `Cases/CaseN/` | Input: `warehouse.csv`, `obstacles.csv`, `ceiling.csv`, `types_of_bays.csv` |
| `solutions/` | Output CSVs and HTMLs from `pipeline.py` |
| `traces/` | Benchmark output used to justify design decisions |

### Input file formats

- `warehouse.csv` — `x, y` vertices of the rectilinear polygon, in order.
- `obstacles.csv` — `x, y, width, height` per row (axis-aligned rectangles).
- `ceiling.csv` — `x_start, height` breakpoints; piecewise-constant along X.
- `types_of_bays.csv` — `id, width, depth, height, gap, n_loads, price`.

### Solution CSV

One bay per row: `id, x, y, rotation` where `(x, y)` is the bay body's
bottom-left corner and `rotation ∈ {0, 90, 180, 270}`.

---

## 3. The greedy solver (`greedy_solver.py`)

Purely constructive, deterministic, ~1 s per case. It runs **two independent
packing paths** and keeps whichever produced the lower Q.

### 3.1 Core building block — alternating-gap strip packing

A single strip-pack pass:

1. **Pick a row depth `D`** (one of the bay dimensions in the catalog) and
   the smallest gap `G` among bays that can sit in a row of that depth.
2. **Lay out row Y-positions with alternating gap directions.** Row 0 has
   its gap pointing up (rotation 0°), row 1 points down (180°), and so on.
   Because two rows with opposing gaps can share aisle space, the pitch
   alternates `D+G, D, D+G, D, …` instead of a fixed `D+G`. This roughly
   halves the aisle overhead.
3. **Intersect each row with the free space** (warehouse minus obstacles)
   to get valid X-intervals. Obstacles split a row into segments that are
   packed independently.
4. **Within each segment, pack left-to-right.** At the cursor, score every
   candidate (bay type × rotation) that fits under the local ceiling and
   doesn't collide. Place the one with the highest score, advance the
   cursor, repeat.

### 3.2 The two paths

**Path A — whole-warehouse pack.** Treat the whole polygon as one strip
grid. Wins on regular shapes and obstacle-heavy layouts where region
decomposition over-fragments.

**Path B — region-decomposition pack.** Slice the warehouse-minus-obstacles
into maximal axis-aligned rectangles using every warehouse and obstacle
X/Y coordinate as grid lines, then merge usable cells greedily into
larger rectangles. Pack each region independently — each one picks its
own best orientation. Placements from already-processed regions are fed
to the next region as hard obstacles. Try a few region orderings
(largest-first, Y-then-X, as-given) and keep the best result. Wins when
the warehouse decomposes cleanly into large rectangles that want
different strip orientations (Case 3 cross, Case 10 donut).

### 3.3 Search matrix

For each path, every pass is parametrised by:

```
orientation    ∈ { horizontal, vertical }        ← "vertical" transposes the warehouse
row-depth      ∈ { each depth/width in catalog }
start-rotation ∈ { 0°, 180° }                    ← gap-up-first or gap-down-first
criterion      ∈ { 2 greedy scoring functions }  ← how to pick the next bay
```

Roughly 2 × 3–4 × 2 × 2 ≈ 30 combinations per case, each a few ms. The
vertical orientation transposes the entire problem (warehouse, obstacles,
ceiling approximated as its minimum) and un-transposes the placements
afterward. This single trick captures most of the Case 0 win, where the
L-shape packs better when strips run along Y instead of X.

### 3.4 The greedy criteria (the "how to pick the next bay" step)

At each cursor position the row-packer enumerates every bay that fits
under the local ceiling and doesn't collide. It scores each one and
places the highest-scoring candidate. Six scoring functions were
prototyped; benchmarking on 17 cases (`traces/criteria_benchmark.csv`)
kept only two — the remaining four were either redundant or regressive.

**Active criteria**

| Name | Formula | Intent |
|------|---------|--------|
| `eff×width` (default) | `(area · n_loads / price) · ew` | Balanced. Efficiency (value per mm² of floor) weighted by how wide the bay is along the row. Wins or ties on 16/17 cases. |
| `loads/$` | `n_loads / price` | Cheapest-load-per-euro. The only criterion that recovers Case15_mega, where the default over-indexes on efficiency and picks the wrong bay mix. |

`ew` is the bay's **effective width along the row**, i.e. the dimension
that actually advances the cursor. It's either `bt.width` (bay placed at
rotation 0°/180°) or `bt.depth` (bay rotated 90°/270° to lie sideways
across the row). Multiplying by `ew` biases the default toward bays that
cover more of the row per placement — coverage is the exponent of Q, so
filling the row densely is worth a lot.

**Why these two and not the others.** The dropped criteria were:

- `cov-heavy` (`ew² · loads / price`) — strictly dominated by `eff×width`.
- `width` (`ew` alone) — ignores price entirely; hurts Q on price-sensitive cases.
- `big-first` (`ew · 1000 + tie`) — equivalent to `width` with a tiebreak.
- `area/$` (`area / price`) — ignores `n_loads`, which is a first-class term in Q.

See `traces/criteria_benchmark.csv`. The two kept criteria are
complementary: one dominates nearly everywhere, the other recovers the
one case where "stuff the row full of efficient bays" is the wrong move.

**Fallback pass.** If the scored pass picks nothing at the current cursor
(no candidate fits), the row-packer retries with the narrowest candidate
first so small gaps still get filled. If even that finds nothing, the
cursor advances by the minimum candidate width to skip past the
unusable spot.

### 3.5 Candidate set per row

For a row of depth `D`, the usable candidates are:

- Every bay `bt` with `bt.depth == D`, at `rotation = start_rot` (0° or
  180° — the row's base rotation).
- Every bay `bt` with `bt.width == D`, at rotation 90°/270° (lying
  sideways across the row). These break alternating-gap tightness
  because their gap is on the X-axis, not Y, so they're fallback
  options, useful in narrow slots left by obstacles.

---

## 4. The LNS+SA refiner (`lns_sa.py`)

Starts from the greedy solution and destroys-and-repairs sub-regions
under Simulated Annealing for a fixed wall-clock budget (30 s by
default). The inner loop runs entirely on **boolean bitmaps** so each
iteration is O(N log N) via FFT convolution rather than O(n_bays²)
Shapely checks.

### 4.1 State

Two mutable bitmaps carry the world:

```
MO (occupied): obstacles ∪ every placed bay's body
MG (gap):      union of every placed bay's gap zone (bodies excluded)
```

Plus read-only scene descriptors:

```
inside:          True for cells inside the warehouse polygon
obstacles_occ:   occupied bitmap from obstacles only (never cleared)
ceiling_profile: per-column minimum ceiling height
```

Kernels for each (bay type, rotation) are precomputed once: a `body_mask`,
a `bodygap_mask` (body + its gap), and a `gap_only_mask` (bodygap minus
body). The last one is precomputed to avoid millions of per-candidate
array copies during repair.

### 4.2 The outer loop

```
for each iteration until time budget exhausted:
    snapshot state
    rect, removed = destroy(state)        # pick sparsest region, eject its bays
    if removed is empty: continue
    candidates     = generate_candidates(state, rect)   # FFT-based
    greedy_repair(state, candidates)                    # highest-score-first fill
    ΔQ = Q_new − Q_current
    if SA_accept(ΔQ, temperature): keep, update best if improved
    else:                          rollback to snapshot
    temperature = linear cooling from initial to final over elapsed fraction
```

Cooling is linear in elapsed wall-clock: `T(t) = T₀ + (T_f − T₀) · (t / budget)`
with `T₀ = 500`, `T_f = 1` by default. `sa_accept(Δ, T)` always accepts
improvements; worsening moves are accepted with probability `exp(−Δ / T)`.

### 4.3 Destroy — selecting the zone to erase

Two-step process:

#### Step 1 — locate the sparsest window

Build a **density bitmap**:

```
dense = occupied | gap | ~inside
```

Cells outside the warehouse are treated as fully occupied so the minimum
naturally avoids polygon boundaries without a separate boundary-distance
term. A flat `K × K` box kernel is convolved (via `scipy.signal.fftconvolve`,
mode `'valid'`) across `dense`:

- `K` is the **largest body dimension across all bay types and rotations,
  in cells**. That's the "natural neutral" window size: one bay's worth
  of neighbourhood. Smaller `K` resolves smaller pockets; larger smooths
  the field further.

The convolution cell `(r, c)` equals the count of occupied-or-outside
cells inside the `K × K` window anchored at `(r, c)`. **The minimum
over this grid is the sparsest window** — i.e. the one with the most
free, in-warehouse cells. Ties are broken uniformly at random, which is
the main source of iteration-to-iteration diversity in the SA. The
window's centre becomes the destroy centre.

#### Step 2 — build the rectangle, then expand to contain intersecting bays

1. Place a **square initial rectangle** centred at the sparsest-window
   centre, with half-side `radius = destroy_radius_factor × max_bay_dim_cells`
   (default `radius_factor = 5.0`). At this factor the rectangle is
   comfortably wider than any single bay, which is what you want — a
   destroy radius smaller than a bay removes no bays at all, defeating
   the move.
2. **One-shot expansion:** find every placed bay whose body+gap bounding
   box intersects the initial rectangle, and expand the rectangle to
   envelop each one in full. Bays added by the expansion do **not**
   trigger further expansion. This bounded growth keeps destroyed zones
   predictable in size while guaranteeing every partially-covered bay is
   removed (removing a bay by clearing only part of its footprint from
   the bitmaps would leave the state inconsistent with `state.placed`).
3. Clip the rectangle to the bitmap bounds, erase the chosen bays from
   both bitmaps (`state.remove`), and hand the rectangle to the repair
   phase.

If step 2 finds no bays at all (the sparse window happens to be in a
purely-empty region), the iteration is skipped without spending FFT
time on a no-op repair.

### 4.4 Repair — generating candidates

For each (bay type, rotation) the repair phase must find every legal
anchor position whose body bottom-left cell lies inside the destroyed
rectangle. Done via three FFT convolutions, each giving the count of
conflicting cells a candidate placement would have:

```
conv_bg_occ = conv(bodygap_mask, occupied)   # must be 0: BG footprint clear of obstacles / other bay bodies
conv_bg_out = conv(bodygap_mask, ~inside)    # must be 0: BG stays inside the warehouse
conv_b_gap  = conv(body_mask,   gap)         # must be 0: body doesn't clobber an existing gap zone
```

An anchor is **legal** iff all three are zero at that cell. The three
outputs are aligned to the body-gap-anchor grid (rotations 90° and 180°
have the body offset inside the bodygap bbox; anchor offsets track this)
and then restricted to:

- The body bottom-left cell is inside the destroyed rectangle.
- The ceiling filter passes: for the body's column span, the minimum
  ceiling height must be `≥ bt.height`. A vectorised rolling-min over
  the `ceiling_profile` gives a per-column boolean in one shot.

Every surviving anchor becomes a `Candidate(bay_type_id, rotation, row, col, score)`
with `score = bt.efficiency = area · n_loads / price` — the greedy
criterion of the refiner. One bay type can contribute many candidates;
across all types / rotations a busy rectangle typically yields
hundreds-to-thousands of candidates.

### 4.5 Repair — selecting which candidates to place

Greedy, highest-score-first, with a small random jitter on ties:

```python
candidates.sort(key=lambda c: (-c.score, rng.random()))
```

The random tiebreak is where diversity between SA iterations comes from —
same sparsest window, same candidate set, but a different accepted
subset depending on how ties fall.

Iterate through the sorted list. For each candidate:

1. Re-check feasibility against the **live** bitmaps (not the pre-convolution
   snapshot). Earlier accepted candidates in the same repair tighten the
   occupancy, so a formerly-legal anchor may be invalid now.
   - Body footprint must not overlap `occupied`.
   - Body footprint must not overlap `gap` (can't clobber an existing gap zone).
   - Body+gap footprint must not overlap `occupied` (accounting for the
     body cells via the precomputed `gap_only_mask` so only gap-only
     cells are tested, i.e. the bay's own body is exempt).
2. If all three checks pass, stamp the bay onto `occupied` and `gap` and
   append to `state.placed`.

Ordering candidates by `efficiency` and checking against the live state
amounts to a morphological greedy filter: the convolutions provide the
set of placements that would be legal in isolation, and the sequential
stamping picks a maximal-efficiency packing inside that set — without
backtracking. Backtracking is unnecessary here because the SA outer
loop is the backtrack mechanism: a bad repair just gets rejected and
rolled back.

### 4.6 Why this works

- **Destruction targets the part of the solution most in need of work.**
  The low-density window is exactly where coverage is lacking, which is
  the exponent of Q.
- **Candidate generation is exhaustive and cheap.** FFT convolution
  enumerates every legal anchor for every bay type × rotation in
  O(N log N). Nothing is missed.
- **Repair's greedy selection is fast and good enough** because the
  outer SA loop provides the exploration. Random tiebreaking on scores
  is all the diversity the loop needs.
- **Bitmap state keeps per-iteration work flat in the number of bays.**
  A Shapely-based LNS on the same design would be ~100× slower and
  would limit the budget to ~10 iterations per case instead of hundreds.

### 4.7 Configuration knobs

| Parameter | Default | Effect |
|-----------|---------|--------|
| `time_limit` | 30 s (pipeline), 25 s (direct) | Wall-clock budget |
| `initial_temperature` | 500 | Higher → more worsening moves accepted early |
| `final_temperature` | 1 | Low final T makes the tail nearly-greedy |
| `destroy_radius_factor` | 5.0 | `rect_half_side = factor × max_bay_dim_cells` |
| `rng_seed` | 42 | Reproducibility |

---

## 5. The pipeline (`pipeline.py`)

Per case, in order:

1. `greedy_solver.py <case> solutions/<case>.csv`
2. In-process `lns_sa.run_lns_sa(<case>, solutions/<case>.csv, time_limit=…)`,
   writing `solutions/<case>_lns.csv`.
3. `visualize.py <case> <greedy_csv> --compare <lns_csv> --labels Greedy,SA -o <case>_compare.html`

Per-case results are isolated — a failure in one case doesn't abort the
rest. The summary printout shows greedy Q, LNS Q, ΔQ, and each stage's
wall time. `--no-open` skips launching the browser; `--skip-greedy`
reuses a previously-generated greedy CSV.

---

## 6. Results

### Original 4 cases (Mecalux brief)

| Case | `solver.py` Q | `greedy_solver.py` Q | Δ |
|------|---:|---:|---:|
| 0 | 3,349 | **1,951** | −42% |
| 1 | 1,340 | 1,340 | tie |
| 2 | 2,551 | 2,551 | tie |
| 3 | 3,328 | **2,679** | −19% |
| **Total** | 10,568 | **9,170** | −13% |

### 7 synthetic cases (`generate_cases.py`)

| Case | Geometry | Greedy Q | Coverage |
|------|----------|---:|---:|
| Case4_T_shape | T-shape (8 vertices) | 2,273 | 74.6% |
| Case5_U_shape | U-shape (8 vertices) | 2,397 | 73.2% |
| Case6_obstacle_field | 12k×12k + 6 small columns | 3,179 | 68.8% |
| Case7_variable_ceiling | rectangle with 4 ceiling drops | 608 | 87.5% |
| Case8_corridor | 30k×4k + 3 pillars | 2,928 | 69.9% |
| Case9_complex | 8-vertex polygon + 5 obstacles + ceiling drops | 2,012 | 71.4% |
| Case10_donut | square with large central obstacle | 2,534 | 72.6% |

Total across all 11 cases: **greedy = 24,453** vs baseline 28,339 (−14%).
With LNS+SA on top, the totals drop further (see
`traces/lns_improvements.csv`).

### Runtime

- Greedy: ≈1 s per case.
- LNS+SA: 30 s per case by default, scales linearly with `--lns-time`.

---

## 7. Validation

`validate.py <case> <solution.csv>` runs the full Shapely-based constraint
check (warehouse containment, obstacle non-overlap, ceiling, bay↔bay,
bay↔gap, gap↔bay). `lns_sa.py` also runs this at the end of its run as a
safety net against bitmap/Shapely rounding disagreements.

---

## 8. Further reading

- `PROBLEM_BRIEF.md` — full problem statement as provided by Mecalux.
- `PROBLEM_MODELIZATION.md` — modelling notes.
- `GREEDY_EXPLICADO.md` — extended walkthrough of the greedy (Catalan/Spanish).
- `mvp.md` — original LNS+SA design discussion.
- `traces/` — benchmarking CSVs behind each design decision.
