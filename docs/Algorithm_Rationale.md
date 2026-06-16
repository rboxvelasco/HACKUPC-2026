# Algorithm Rationale

1. [Constraints](#constraints)
1. [Considerations and Discarded Approaches](#considerations-and-discarded-approaches)
1. [General Overview](#general-overview)
1. [Greedy](#greedy)
1. [LNS - Simulated Annealing](#lns---simulated-annealing)


## Constraints

1. **Ceiling:** `bay.height ≤ ceiling(x)` for every $x$ in the bay's footprint.
1. **Boundaries:** the full bay rectangle and its gap must lie inside the warehouse polygon.
1. **Bay–bay no overlap:** two bay rectangles cannot overlap.
1. **Gap–gap overlap allowed:** gaps may overlap each other.
1. **Bay–obstacle no overlap:** bay rectangles cannot intersect any obstacle. (Gaps also cannot intersect obstacles.)
1. **Gaps must exist and be respected:** the gap rectangle of each bay must be fully valid (inside warehouse, not intersecting obstacles or other bays — but can share space with other gaps).
1. Bays may touch the warehouse boundary (e.g. start at `(0,0)`).
1. Warehouse is axis-aligned.
1. Bays and obstacles are strictly rectangular.
1. Each catalog `id` is a _type_; instances may be repeated.


## Considerations and Discarded Approaches

Initially, we started modeling the problem as a Constraint Logic Programming (CLP) formulation in `Prolog` with the intention of solving it using a SAT solver (`Kissat`). However, we abandoned this approach because it generated an excessively large number of constraints.

We also considered a Mixed-Integer Linear Programming (MILP) formulation. Nevertheless, this alternative was rapidly discarded as well, since the number of constraints would grow significantly, making the approach impractical within the given time limits.

In addition, we began developing a simplified genetic algorithm. However, the preliminary results were not sufficiently promising, so we decided to focus our efforts on the greedy + local search approach instead. This strategy appeared more promising and was already producing encouraging results during the early stages of development.


## General Overview

Because the coverage term appears in the exponent, it has a dominant impact on the final score. Increasing coverage from 60% to 80% can roughly halve $Q$, so our algorithm prioritizes spatial utilization over marginal cost improvements.

Our final algorithm follows a hybrid **greedy + local search** pipeline composed of the following stages:

* **Strip packing with alternating gaps:** Rows of bays are packed using alternating orientations (`0°` and `180°`). This arrangement allows adjacent aisles to share gap. When the ceiling height over part of a bay's footprint is insufficient for a given bay type, the algorithm automatically considers shorter bay types that satisfy the height constraints.

* **Large Neighborhood Search (LNS) with Simulated Annealing (SA):** Occupancy (`MO`), gaps (`MG`), and ceiling heights (`MH`) are represented as bitmaps. At each iteration, a region of the current solution is destroyed and subsequently repaired through:

  1. **Convolution-based candidate generation:** Candidate positions are generated using bay-and-gap kernels (`K_BG`) and bay-only kernels (`K_B`) for the four possible rotations. A placement is considered feasible when `Conv(K_BG, MO) = 0`, `Conv(K_B, MG) = 0`, and the ceiling constraint `min(MH within footprint) ≥ bay.height` is satisfied.

  2. **Morphological pruning and greedy selection:** Invalid or redundant candidates are removed, and a greedy procedure selects a set of non-overlapping placements.

  3. **Bitmask-based updates:** Fast insertion and removal operations are performed through bitwise manipulations (`mask & shifted`, `mask |= shifted`, and `mask ^= shifted`).

This makes the constructive step deterministic and the refinement step stocastic.


## Greedy

A naïve greedy strategy would simply be: *find any available location, place the first bay that fits, and repeat*. In practice, this approach produces poor layouts because it tends to create fragmented and unusable gaps.

Our approach is more structured. We perform **row-based strip packing**, placing bays from left to right within horizontal rows, while exploiting a simple mechanism that allows adjacent rows to share aisle space.

### Alternating Aisles

Each bay requires a clearance gap (aisle) adjacent to its front side. If all bays are oriented in the same direction, every row consumes `depth + gap` units of vertical space, and the aisle space between rows is effectively wasted.

Instead, we alternate row orientations (`0°` and `180°`). In this configuration, the front gaps of two adjacent rows overlap and form a single shared physical aisle. As a result, the row pitch alternates between `depth + gap` and `depth`, rather than remaining constant.

This increases the number of rows that can be accommodated within a warehouse and improves overall space utilization.

### Single Greedy Pass

Given a selected row depth `depth` and its corresponding aisle width `gap`, the algorithm proceeds as follows:

1. Compute the vertical positions of all alternating rows: `y₀ = 0` (rotation `0°`), `y₁ = depth + gap` (rotation `180°`), `y₂ = 2·depth + gap` (rotation `0°`), and so on.

2. For each row, intersect its horizontal strip with the free warehouse area (`warehouse − obstacles`) to obtain the available horizontal segments. Obstacles may split a row into multiple independent segments.

3. Within each segment, sweep from left to right:
   * Determine all bay types that can be placed at the current position while satisfying footprint, collision, and ceiling constraints.
   * Evaluate each feasible candidate using a greedy scoring function.
   * Place the highest-scoring candidate and advance the cursor by its width.
   * If no candidate fits, advance the cursor by the minimum catalog width and continue.

This procedure constitutes a single deterministic greedy pass. It is computationally inexpensive and already produces reasonably good layouts.

### Search Matrix

A single greedy pass is insufficient because several high-level design choices strongly influence the final solution:

* Which row depth should be used?
* Should the first row have its gap above or below?
* Which greedy scoring function should be employed?
* Should the layout be generated using horizontal or vertical rows?

Rather than attempting to predict the best configuration, we exhaustively evaluate all combinations. Since each greedy pass requires only a few tens of milliseconds, exploring the full configuration space remains affordable.

The search matrix is defined as:

| Parameter        | Options                         |
| ---------------- | ------------------------------- |
| Orientation      | {horizontal, vertical}          |
| Row depth        | {all catalog depths and widths} |
| Initial rotation | {0°, 180°}                      |
| Greedy criterion | {2 scoring functions}           |

This results in roughly 30 configurations per instance. The best solution according to the objective function $Q$ is selected.

### Greedy Selection Criteria

Six scoring functions were prototyped; benchmarking on 17 cases kept only two — the remaining four were either redundant or regressive.

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

The dropped criteria were:

- `cov-heavy` (`ew² · loads / price`) — strictly dominated by `eff×width`.
- `width` (`ew` alone) — ignores price entirely; hurts Q on price-sensitive cases.
- `big-first` (`ew · 1000 + tie`) — equivalent to `width` with a tiebreak.
- `area/$` (`area / price`) — ignores `n_loads`, which is a first-class term in Q.

See [`benchmarks/traces/criteria_benchmark.csv`](../benchmarks/traces/criteria_benchmark.csv). The two kept criteria are
complementary: one dominates nearly everywhere, the other recovers the
one case where "stuff the row full of efficient bays" is the wrong move.


### Vertical Orientation

The packing algorithm is implemented assuming horizontal rows. To evaluate layouts based on vertical rows, we apply a coordinate transformation:

1. Transpose the warehouse geometry, obstacles, and ceiling map by swapping the X and Y axes.
2. Execute the standard horizontal packing algorithm.
3. Transform the resulting bay placements back to the original coordinate system, adjusting rotations accordingly (`0° ↔ 270°`, `90° ↔ 180°`).

This allows the same implementation to efficiently explore both horizontal and vertical packing strategies.

### Dual Solver Strategy

So far, we have described a single optimization pipeline: a search matrix of greedy packing configurations from which the best solution is selected. In practice, the solver executes **two independent pipelines** and retains the solution with the highest objective value (Q).

#### Pipeline A — Whole-Warehouse Packing

The first pipeline is the one described in the previous sections. The entire warehouse is treated as a single packing region, and the full search matrix is evaluated over the complete layout.

This approach performs well when the warehouse is relatively convex or when obstacles integrate naturally into the row structure. In such cases, a global packing strategy can exploit the available space efficiently without requiring explicit decomposition.

**Best suited for:** warehouses with simple geometries and obstacle layouts that do not strongly partition the available space.

#### Pipeline B — Region Decomposition

For warehouses whose geometry naturally decomposes into several large rectangular areas (e.g., cross-shaped or ring-shaped layouts), it is often advantageous to optimize each region independently. Different regions may favor different packing orientations, making a global orientation suboptimal.

The decomposition-based pipeline proceeds as follows:

1. **Rectangle decomposition**: All X and Y coordinates corresponding to warehouse vertices and obstacle corners are collected. These coordinates define a rectilinear grid that partitions the warehouse into elementary cells.

   Since all geometries are axis-aligned, each cell lies either entirely inside or entirely outside the feasible area. The valid cells are then greedily merged into maximal non-overlapping rectangles.

2. **Rectangle ordering**: Several processing orders are evaluated, including: largest rectangle first, lexicographic ordering by Y and X coordinates, original generation order.

3. **Independent optimization of each rectangle**: for each rectangle in the selected order:

   * Evaluate both horizontal and vertical packing orientations.
   * For each orientation, execute a reduced search matrix consisting of:
     * 3 greedy scoring functions,
     * 2 row depths,
     * 2 initial row rotations.
   * Retain the best configuration for the current rectangle.
   * Bays placed in previously processed rectangles are treated as fixed obstacles, preventing collisions along region boundaries.

4. **Selection of the best ordering**: after evaluating all rectangle orderings, the highest-scoring overall solution is retained.

This strategy is particularly effective when the warehouse can be partitioned into relatively independent regions, each benefiting from a different packing orientation or packing pattern.

**Best suited for:** highly non-convex warehouse geometries whose natural subregions exhibit different optimal packing characteristics.



## LNS - Simulated Annealing

Once we have built an initial valid solution with the grreedy solver, we improve it using Local Search. To avoid quedarnos stuck in óptimos locales, we use a Simulated Annealing strategy and LNS as operator.


### Representation of the State

We take the solution of the greedy solver and we have the representation of the state using verious **boolean bitmaps**, indicating wether a specific point of the warehouse is occupied or not by a bay/gap/obstacle.

This representation of the state allows us to operate efficiently with the state via convolutions, boolean operations and standard CV techniques.

Two mutable bitmaps carry the world:
 - `MO` (map occupied): obstacles ∪ every placed bay's body
 - `MG` (map gaps): union of every placed bay's gap zone (bodies excluded)

Plus read-only scene descriptors:
 - `inside`: True for cells inside the warehouse polygon
 - `obstacles_occ`: occupied bitmap from obstacles only (never cleared)
 - `ceiling_profile`: per-column minimum ceiling height 1D vector


Kernels for each tuple $(bay type, rotation)$ are precomputed once: a `body_mask`,
a `bodygap_mask` (body + its gap), and a `gap_only_mask` (bodygap minus
body).

### General Procedure / Pseudocode

The local search phase starts from the best greedy solution and iteratively applies a **destroy-and-repair** strategy over sub-regions of the layout using **Simulated Annealing (SA)**. The process runs for a fixed wall-clock time budget (25 seconds by default).

The loop operates entirely on the previously described bitmap representations, allowing each iteration to be performed in $O(N \log N)$ time using FFT-based convolution instead of the $O((n_{bays})^2)$ complexity caused by repeated geometric collision checks with Shapely.

The overall procedure is:

```text
S = initial_greedy_solution()
best = S

for each iteration until time budget is exhausted {

    S' = destroy(S)      // Remove bays from a selected region
    S' = repair(S')      // Reinsert bays in the destroyed region

    q_new = evaluate(S')
    q_old = evaluate(S)

    Probabilistically accept S' with Simulated Annealing

    Update temperature

    if S is better than best:
        best = S
}
```

The temperature follows a linear cooling schedule based on the elapsed wall-clock time:
$T(t) = T_0 + (T_f - T_0)\cdot\frac{t}{budget}$ 
where $T_0 = 500$ and $T_f = 1$ are the initial and final temperatures by default.

The acceptance function $sa\_accept(\Delta, T)$ always accepts improving moves. For worsening solutions, the move is accepted with probability: $P = e^{-\Delta/T}$. This probabilistic acceptance mechanism allows the search to occasionally escape local optima, especially during the early stages when the temperature is higher.


### Destroy: selecting the zone to erase

This phase is a two-step process:

#### Step 1 — locate the sparsest window

Build a **density bitmap**: `dense = occupied | gap | ~inside`.

Cells outside the warehouse are treated as fully occupied so the minimum naturally avoids polygon boundaries without a separate boundary-distance term. A flat `K × K` box kernel is convolved (via `scipy.signal.fftconvolve`, mode `'valid'`) across `dense`:

- `K` is the **largest body dimension across all bay types and rotations, in cells**. That's the "natural neutral" window size: one bay's worth of neighbourhood. Smaller `K` resolves smaller pockets; larger smooths the field further.

The convolution cell `(r, c)` equals the count of occupied-or-outside cells inside the `K × K` window anchore at `(r, c)`. **The minimum over this grid is the sparsest window**, i.e. the one with the most free, in-warehouse cells. Ties are broken uniformly at random, which is the main source of iteration-to-iteration diversity in the SA. The window's centre becomes the destroy centre.

#### Step 2 — build the rectangle, then expand to contain intersecting bays

1. Place a **square initial rectangle** centred at the sparsest-window centre, with half-side `radius = destroy_radius_factor × max_bay_dim_cells`.
2. **One-shot expansion:** find every placed bay whose body+gap bounding
box intersects the initial rectangle, and expand the rectangle to envelop each one in full. Bays added by the expansion do **not** trigger further expansion. This bounded growth keeps destroyed zones predictable in size while guaranteeing every partially-covered bay is
removed (removing a bay by clearing only part of its footprint from the bitmaps would leave the state inconsistent with `state.placed`).
3. Clip the rectangle to the bitmap bounds, erase the chosen bays from both bitmaps (`state.remove`), and hand the rectangle to the repair phase.

If step 2 finds no bays at all (the sparse window happens to be in a purely-empty region), the iteration is skipped without spending FFT time on a no-op repair.


### Repair: generating candidates

For each $(bay type, rotation)$ the repair phase must find every legal anchor position whose body bottom-left cell lies inside the destroyed rectangle. Done via three FFT convolutions, each giving the count of conflicting cells a candidate placement would have:

```
conv_bg_occ = conv(bodygap_mask, occupied)   # must be 0: BG footprint clear of obstacles / other bay bodies
conv_bg_out = conv(bodygap_mask, ~inside)    # must be 0: BG stays inside the warehouse
conv_b_gap  = conv(body_mask,   gap)         # must be 0: body doesn't clobber an existing gap zone
```

An anchor is **legal** iff all three are zero at that cell. The three outputs are aligned to the body-gap-anchor grid (rotations 90° and 180° have the body offset inside the bodygap bbox; anchor offsets track this) and then restricted to:
- The body bottom-left cell is inside the destroyed rectangle.
- The ceiling filter passes: for the body's column span, the minimum
  ceiling height must be `≥ bt.height`. A vectorised rolling-min over
  the `ceiling_profile` gives a per-column boolean in one shot.

Every surviving anchor becomes a `Candidate(bay_type_id, rotation, row, col, score)` with `score = bt.efficiency = area · n_loads / price` — the greedy criterion of the refiner.

### Repair — selecting which candidates to place

Greedy, highest-score-first, with a small random jitter on ties: `candidates.sort(key=lambda c: (-c.score, rng.random()))`.

The random tiebreak is where diversity between SA iterations comes from —
same sparsest window, same candidate set, but a different accepted
subset depending on how ties fall.

Iterate through the sorted list. For each candidate:
1. Re-check feasibility against the **live** bitmaps (not the pre-convolution snapshot). Earlier accepted candidates in the same repair tighten the occupancy, so a formerly-legal anchor may be invalid now.
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


### Why this approach works

- **Destruction targets the part of the solution most in need of work.**
  The low-density window is exactly where coverage is lacking, which is
  the exponent of $Q$.
- **Candidate generation is exhaustive and cheap.** FFT convolution
  enumerates every legal anchor for every bay type × rotation in
  O(N log N). Nothing is missed.
- **Repair's greedy selection is fast and good enough** because the
  outer SA loop provides the exploration. Random tiebreaking on scores
  is all the diversity the loop needs.
- **Bitmap state keeps per-iteration work flat in the number of bays.**
  A Shapely-based LNS on the same design would be ~100× slower and
  would limit the budget to ~10 iterations per case instead of hundreds.

### Configuration knobs

| Parameter | Default | Effect |
|-----------|---------|--------|
| `time_limit` | 25 s | Wall-clock budget |
| `initial_temperature` | 500 | Higher → more worsening moves accepted early |
| `final_temperature` | 1 | Low final T makes the tail nearly-greedy |
| `destroy_radius_factor` | 5.0 | `rect_half_side = factor × max_bay_dim_cells` |
| `rng_seed` | 42 | Reproducibility |