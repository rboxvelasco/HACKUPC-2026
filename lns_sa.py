#!/usr/bin/env python3
"""
Large Neighborhood Search + Simulated Annealing over a bitmap representation.

Pipeline (see mvp.md for the design discussion):

    S  = initial_solution  (loaded from a solver.py run)
    for each iteration:
        S' = destroy(S)     → pick the sparsest region via box-convolution of
                              (occupied | gap | ~inside), anchor a square rect
                              of half-side `radius_factor × max_bay_dim` there,
                              expand once to envelop every bay whose body+gap
                              intersects the initial rect, remove those bays.
        S' = repair(S')     → convolutional greedy + morphological filter
        accept(S') via SA
        update temperature
        track best

The state is carried on two boolean bitmaps:

    MO (occupied): obstacles ∪ placed-bay bodies
    MG (gap):      union of placed-bay gap zones (bodies excluded)

Candidate generation uses 2D FFT-based convolution (scipy.signal.fftconvolve)
to find, for every bay type × rotation, every legal anchor position in O(N log N):

    conv(Kernel_BG, MO) == 0  ⇒  body+gap does not collide with any occupied cell
    conv(Kernel_B,  MG) == 0  ⇒  body does not collide with any existing gap zone

Plus a cheap ceiling check using the per-column ceiling profile, and a
`inside` check so anchors that would push the body outside the warehouse
polygon are filtered out.

The rotation convention matches solver.py:

    rot   body footprint (cells)                 gap footprint (cells)
    ────  ─────────────────────────────          ─────────────────────────
    0     [x, x+w) × [y, y+d)                    [x, x+w) × [y+d, y+d+g)
    90    [x, x+d) × [y, y+w)                    [x−g, x)  × [y, y+w)
    180   [x, x+w) × [y, y+d)                    [x, x+w)  × [y−g, y)
    270   [x, x+d) × [y, y+w)                    [x+w, x+w+g) × [y, y+d)

where (x, y) is the body's bottom-left corner in world mm. When we convolve,
the kernel is built in cell units with its "anchor" at the cell that receives
the bay's world (x, y) position. For rotations 90 and 180 (gap on the −X or
−Y side) the anchor is not at the kernel's bottom-left, so we track a
per-rotation anchor offset and the minimum world (x, y) the anchor may take
(to keep the gap inside the warehouse bbox).

Usage (CLI):
    python3 lns_sa.py <case_dir> <input_solution_csv> [output_solution_csv]
"""

from __future__ import annotations

import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.signal import fftconvolve

from bitmap import RasterResult, rasterize_solution
from solver import (
    BayType,
    CollisionEngine,
    Ceiling,
    PlacedBay,
    compute_score,
    parse_bay_types,
    parse_ceiling,
    parse_obstacles,
    parse_warehouse,
    usable_area,
)


# ─────────────────────────────────────────────
# Kernel generation
# ─────────────────────────────────────────────

@dataclass(frozen=True)
class BayKernel:
    """A kernel describing one (bay type, rotation) combo in cell-space.

    Kernels are 2D boolean masks with the cell at (0, 0) interpreted as the
    footprint cell receiving the bay's world (x, y) position.

    Attributes:
        bay_type_id:   ID of the bay type.
        rotation:      One of 0, 90, 180, 270.
        body_mask:     (body_rows, body_cols) bool, body footprint relative to
                       the body's bottom-left. Pure shape.
        bodygap_mask:  (bg_rows, bg_cols) bool, body+gap footprint, positioned
                       within its bounding box so that (anchor_row_off,
                       anchor_col_off) is the cell that coincides with the
                       body's bottom-left.
        anchor_row_off: Row offset of the body's bottom-left inside
                        bodygap_mask. 0 except for rotation 180 (g cells up).
        anchor_col_off: Column offset of the body's bottom-left inside
                        bodygap_mask. 0 except for rotation 90 (g cells right).
        gap_mm:         Gap size in mm (for diagnostics only).
        body_w_cells:   Body width in cells (cols).
        body_h_cells:   Body height in cells (rows).
    """
    bay_type_id: int
    rotation: int
    body_mask: np.ndarray
    bodygap_mask: np.ndarray
    anchor_row_off: int
    anchor_col_off: int
    gap_mm: int
    body_w_cells: int
    body_h_cells: int
    min_height_mm: int  # bay type height — must fit under ceiling


def build_kernels(
    bay_types: List[BayType], cell_size: int
) -> Dict[int, Dict[int, BayKernel]]:
    """Build body/body+gap kernels for every (bay_type_id, rotation) combo.

    Returns: {bay_type_id: {rotation: BayKernel}}
    """
    kernels: Dict[int, Dict[int, BayKernel]] = {}
    for bt in bay_types:
        # Body dims in cells, by rotation (cols, rows).
        # rot 0/180: width along X (cols), depth along Y (rows)
        # rot 90/270: depth along X (cols), width along Y (rows)
        base_w_cells = bt.width // cell_size
        base_d_cells = bt.depth // cell_size
        gap_cells = bt.gap // cell_size

        per_rotation: Dict[int, BayKernel] = {}

        for rot in (0, 90, 180, 270):
            if rot in (0, 180):
                body_cols = base_w_cells
                body_rows = base_d_cells
            else:
                body_cols = base_d_cells
                body_rows = base_w_cells

            body_mask = np.ones((body_rows, body_cols), dtype=bool)

            # Build body+gap mask, placing the body inside it according to
            # where the gap sits. anchor_* tells us where the body's BL corner
            # lies within the bodygap mask.
            if rot == 0:
                # gap above (rows beyond the body)
                bg_rows = body_rows + gap_cells
                bg_cols = body_cols
                anchor_row = 0
                anchor_col = 0
            elif rot == 90:
                # gap to the left (columns before the body)
                bg_rows = body_rows
                bg_cols = body_cols + gap_cells
                anchor_row = 0
                anchor_col = gap_cells
            elif rot == 180:
                # gap below (rows before the body)
                bg_rows = body_rows + gap_cells
                bg_cols = body_cols
                anchor_row = gap_cells
                anchor_col = 0
            else:  # 270
                # gap to the right (columns beyond the body)
                bg_rows = body_rows
                bg_cols = body_cols + gap_cells
                anchor_row = 0
                anchor_col = 0

            bodygap_mask = np.ones((bg_rows, bg_cols), dtype=bool)

            per_rotation[rot] = BayKernel(
                bay_type_id=bt.id,
                rotation=rot,
                body_mask=body_mask,
                bodygap_mask=bodygap_mask,
                anchor_row_off=anchor_row,
                anchor_col_off=anchor_col,
                gap_mm=bt.gap,
                body_w_cells=body_cols,
                body_h_cells=body_rows,
                min_height_mm=bt.height,
            )

        kernels[bt.id] = per_rotation

    return kernels


# ─────────────────────────────────────────────
# State (mutable bitmap world)
# ─────────────────────────────────────────────

@dataclass
class LNSState:
    """Mutable LNS/SA state carried across iterations.

    The two boolean bitmaps (occupied, gap) are the source of truth for
    candidate generation. `placed` holds the canonical list of PlacedBay
    objects for scoring and for writing the final solution.

    `inside` and `ceiling_profile` are read-only scene descriptors.
    """
    occupied: np.ndarray       # (rows, cols) bool
    gap: np.ndarray            # (rows, cols) bool
    inside: np.ndarray         # (rows, cols) bool
    obstacles_occ: np.ndarray  # (rows, cols) bool — occupied cells from obstacles only (never cleared)
    ceiling_profile: np.ndarray  # (cols,) int32
    cell_size: int
    origin: Tuple[int, int]
    shape: Tuple[int, int]

    placed: List[PlacedBay] = field(default_factory=list)

    # ─── bitmask operations (mvp.md §3) ────────────────────────────
    def insert(self, pb: PlacedBay, kernels: Dict[int, Dict[int, BayKernel]]) -> None:
        """Stamp a bay onto the bitmaps. Caller guarantees no collision."""
        k = kernels[pb.bay_type.id][pb.rotation]
        r0, c0 = self._body_origin_cell(pb, k)
        bh, bw = k.body_mask.shape
        self.occupied[r0:r0 + bh, c0:c0 + bw] |= k.body_mask

        # Gap region: bodygap_mask minus the body portion
        bg_r0 = r0 - k.anchor_row_off
        bg_c0 = c0 - k.anchor_col_off
        bgh, bgw = k.bodygap_mask.shape
        gap_patch = k.bodygap_mask.copy()
        # Zero out the body area in the patch
        gap_patch[k.anchor_row_off:k.anchor_row_off + bh,
                  k.anchor_col_off:k.anchor_col_off + bw] = False
        self.gap[bg_r0:bg_r0 + bgh, bg_c0:bg_c0 + bgw] |= gap_patch
        # gap must not overlap occupied (defensive; body + gap are disjoint by construction)
        self.gap &= ~self.occupied

        self.placed.append(pb)

    def remove(self, idx: int, kernels: Dict[int, Dict[int, BayKernel]]) -> None:
        """Erase a bay from the bitmaps by stamping-off its kernel."""
        pb = self.placed[idx]
        k = kernels[pb.bay_type.id][pb.rotation]
        r0, c0 = self._body_origin_cell(pb, k)
        bh, bw = k.body_mask.shape
        self.occupied[r0:r0 + bh, c0:c0 + bw] &= ~k.body_mask

        bg_r0 = r0 - k.anchor_row_off
        bg_c0 = c0 - k.anchor_col_off
        bgh, bgw = k.bodygap_mask.shape
        gap_patch = k.bodygap_mask.copy()
        gap_patch[k.anchor_row_off:k.anchor_row_off + bh,
                  k.anchor_col_off:k.anchor_col_off + bw] = False
        self.gap[bg_r0:bg_r0 + bgh, bg_c0:bg_c0 + bgw] &= ~gap_patch

        self.placed.pop(idx)

    def _body_origin_cell(self, pb: PlacedBay, k: BayKernel) -> Tuple[int, int]:
        """World (pb.x, pb.y) → (row, col) of body's bottom-left cell."""
        ox, oy = self.origin
        col = (int(pb.x) - ox) // self.cell_size
        row = (int(pb.y) - oy) // self.cell_size
        return row, col

    # ─── factory ─────────────────────────────────────────────────
    @classmethod
    def from_case(
        cls,
        case_dir: str,
        solution_csv: str,
    ) -> "LNSState":
        """Build initial state from a case + existing solution CSV.

        The obstacle-only occupancy layer is recomputed separately from the
        full raster because during destroy() we must distinguish "cell occupied
        by obstacle (permanent)" from "cell occupied by bay body (removable)".
        """
        raster = rasterize_solution(case_dir, solution_csv)

        # Rebuild obstacle-only bitmap (rasterize with empty solution)
        empty_sol = _empty_solution_path()
        try:
            empty_raster = rasterize_solution(case_dir, empty_sol, cell_size=raster.cell_size)
        finally:
            if os.path.exists(empty_sol):
                os.unlink(empty_sol)

        # Load placed bays
        bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
        bay_type_map = {bt.id: bt for bt in bay_types}
        placed = _load_solution(solution_csv, bay_type_map)

        return cls(
            occupied=raster.bitmap_occupied.copy(),
            gap=raster.bitmap_gap.copy(),
            inside=raster.bitmap_inside,
            obstacles_occ=empty_raster.bitmap_occupied.copy(),
            ceiling_profile=raster.ceiling_profile,
            cell_size=raster.cell_size,
            origin=raster.origin,
            shape=raster.shape,
            placed=placed,
        )


def _empty_solution_path() -> str:
    """Write an empty solution CSV and return its path. Caller must remove it."""
    import tempfile
    fd, path = tempfile.mkstemp(suffix='_empty_solution.csv')
    os.close(fd)
    with open(path, 'w'):
        pass
    return path


def _load_solution(path: str, bay_type_map: Dict[int, BayType]) -> List[PlacedBay]:
    placed: List[PlacedBay] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) < 4:
                continue
            bid = int(parts[0])
            x, y, rot = int(parts[1]), int(parts[2]), int(parts[3])
            placed.append(PlacedBay(bay_type=bay_type_map[bid], x=x, y=y, rotation=rot))
    return placed


# ─────────────────────────────────────────────
# Destroy
# ─────────────────────────────────────────────

def _max_bay_dim_cells(
    kernels: Dict[int, Dict[int, BayKernel]]
) -> int:
    """Largest body dimension across all bay types and rotations, in cells."""
    m = 0
    for per_rot in kernels.values():
        for k in per_rot.values():
            m = max(m, k.body_h_cells, k.body_w_cells)
    return max(1, m)


def _low_density_center(
    state: LNSState,
    kernel_size: int,
    rng: random.Random,
) -> Tuple[int, int]:
    """Pick the (row, col) with the minimum density in a `kernel_size × kernel_size`
    window. Ties broken uniformly at random.

    Density bitmap = `occupied | gap | outside_warehouse`. Outside-the-warehouse
    cells are treated as fully occupied (value 1), which naturally repels the
    minimum away from polygon boundaries without needing a separate inside mask.
    """
    # Flat box kernel. fftconvolve with mode='valid' gives an output whose
    # cell (r, c) is the sum over window [r:r+K, c:c+K]. Lower = sparser.
    dense_bitmap = (state.occupied | state.gap | ~state.inside).astype(np.float32)
    kernel = np.ones((kernel_size, kernel_size), dtype=np.float32)
    conv = fftconvolve(dense_bitmap, kernel, mode='valid')
    conv = np.rint(conv).astype(np.int32)

    # Find minimum with random tie-breaking.
    min_val = int(conv.min())
    rows_t, cols_t = np.where(conv == min_val)
    idx = rng.randrange(len(rows_t))
    # The conv cell (r, c) corresponds to window anchored at (r, c), spanning
    # [r : r+K, c : c+K]. Convert to that window's center.
    r_anchor = int(rows_t[idx])
    c_anchor = int(cols_t[idx])
    r_center = r_anchor + kernel_size // 2
    c_center = c_anchor + kernel_size // 2
    return r_center, c_center


def _expand_rect_to_contain_bays(
    rect: Tuple[int, int, int, int],
    state: LNSState,
    kernels: Dict[int, Dict[int, BayKernel]],
) -> Tuple[Tuple[int, int, int, int], List[int]]:
    """Expand `rect` so every bay whose body+gap bbox currently intersects it
    is fully contained. Expansion is done once — bays added by the expansion
    do NOT trigger further expansion.

    Returns (expanded_rect, indices_of_bays_to_remove).
    """
    r0, c0, r1, c1 = rect
    rows, cols = state.shape
    to_remove: List[int] = []

    # First pass: find every bay that intersects the initial rect, and grow
    # the rect to envelop each one. We do NOT re-scan after growing.
    for idx, pb in enumerate(state.placed):
        k = kernels[pb.bay_type.id][pb.rotation]
        br, bc = state._body_origin_cell(pb, k)
        bg_r0 = br - k.anchor_row_off
        bg_c0 = bc - k.anchor_col_off
        bgh, bgw = k.bodygap_mask.shape
        bg_r1 = bg_r0 + bgh
        bg_c1 = bg_c0 + bgw

        # Intersect?
        if bg_r0 < r1 and bg_r1 > r0 and bg_c0 < c1 and bg_c1 > c0:
            to_remove.append(idx)

    # Expand rect from the intersecting bays (pass 1 only).
    for idx in to_remove:
        pb = state.placed[idx]
        k = kernels[pb.bay_type.id][pb.rotation]
        br, bc = state._body_origin_cell(pb, k)
        bg_r0 = br - k.anchor_row_off
        bg_c0 = bc - k.anchor_col_off
        bgh, bgw = k.bodygap_mask.shape
        bg_r1 = bg_r0 + bgh
        bg_c1 = bg_c0 + bgw

        r0 = min(r0, bg_r0)
        c0 = min(c0, bg_c0)
        r1 = max(r1, bg_r1)
        c1 = max(c1, bg_c1)

    # Clip to bitmap bounds
    r0 = max(0, r0)
    c0 = max(0, c0)
    r1 = min(rows, r1)
    c1 = min(cols, c1)

    return (r0, c0, r1, c1), to_remove


def destroy_low_density(
    state: LNSState,
    kernels: Dict[int, Dict[int, BayKernel]],
    radius_factor: float,
    rng: random.Random,
) -> Tuple[Tuple[int, int, int, int], List[PlacedBay]]:
    """Pick the sparsest region of the bitmap, build a square rect around it
    of radius `radius_factor × max_bay_dim_cells`, expand it once to envelop
    every bay whose body+gap intersects the initial rect, and remove those bays.

    Density = occupied | gap | outside_warehouse. Outside cells treated as
    occupied so the minimum naturally avoids warehouse boundaries.

    Ties on the minimum are broken uniformly at random.

    Returns:
        (final_rect_cells, removed_bays)  — see destroy semantics.
        final_rect is the expanded (and clipped) rectangle in cell coords;
        removed_bays is the list of PlacedBay objects that were ejected
        (caller uses this for rollback).
    """
    rows, cols = state.shape
    max_bay = _max_bay_dim_cells(kernels)

    # Density kernel is max_bay × max_bay — one bay's worth of "window".
    # A smaller kernel would resolve smaller pockets; a larger one smooths
    # the density field further. `max_bay` is a natural neutral default.
    kernel_size = min(max(1, max_bay), rows, cols)
    r_center, c_center = _low_density_center(state, kernel_size, rng)

    # Initial rect: square of half-side `radius`.
    radius = max(1, int(radius_factor * max_bay))
    r0 = max(0, r_center - radius)
    c0 = max(0, c_center - radius)
    r1 = min(rows, r_center + radius)
    c1 = min(cols, c_center + radius)

    # Expand once to contain every intersecting bay, then remove them.
    final_rect, indices = _expand_rect_to_contain_bays(
        (r0, c0, r1, c1), state, kernels
    )

    removed: List[PlacedBay] = []
    # Sort descending so index removal is stable.
    for idx in sorted(indices, reverse=True):
        removed.append(state.placed[idx])
        state.remove(idx, kernels)

    return final_rect, removed


# ─────────────────────────────────────────────
# Repair: convolutional candidate generation
# ─────────────────────────────────────────────

def _convolve_forbidden(
    bitmap: np.ndarray, kernel: np.ndarray
) -> np.ndarray:
    """Compute, for every anchor (top-left) placement, the count of conflicting
    cells if the kernel were stamped there.

    Output shape = (rows - kh + 1, cols - kw + 1). An output cell == 0 means
    no conflict; > 0 means at least one collision cell.

    Uses FFT-based convolution. To turn a correlation-shaped output into the
    "valid" anchor placement grid, we flip the kernel.
    """
    # Convert to float32 for FFT math.
    b = bitmap.astype(np.float32)
    k = kernel.astype(np.float32)
    # fftconvolve(..., mode='valid') flips the kernel → that's correlation, not
    # convolution. For an all-ones kernel the result is the same either way,
    # but we use 'valid' for the exact cell-count semantics we want.
    out = fftconvolve(b, k[::-1, ::-1], mode='valid')
    # Round to int-space — FFT introduces tiny floating errors.
    return np.rint(out).astype(np.int32)


@dataclass
class Candidate:
    """One legal placement found by the convolutional search."""
    bay_type_id: int
    rotation: int
    row: int  # body bottom-left row
    col: int  # body bottom-left col
    score: float  # placement score (higher = better)


def generate_candidates(
    state: LNSState,
    kernels: Dict[int, Dict[int, BayKernel]],
    rect: Tuple[int, int, int, int],
    bay_types: List[BayType],
) -> List[Candidate]:
    """For every bay type × rotation, find every legal anchor whose body's
    bottom-left cell lies inside `rect`.

    Filters applied per candidate:
      1. body+gap kernel vs occupied bitmap (no collision)
      2. body kernel vs gap bitmap (don't clobber existing gaps)
      3. body+gap kernel fully inside the warehouse polygon
      4. bay height ≤ ceiling_profile min across body's column span
    """
    rows, cols = state.shape
    r0, c0, r1, c1 = rect

    candidates: List[Candidate] = []

    # Pre-build an "outside warehouse" bitmap; any kernel that touches an
    # outside cell is invalid.
    outside = ~state.inside

    for bt in bay_types:
        for rot in (0, 90, 180, 270):
            k = kernels[bt.id][rot]
            bg_h, bg_w = k.bodygap_mask.shape

            if bg_h > rows or bg_w > cols:
                continue  # kernel larger than the bitmap

            # Conflict counts for the body+gap kernel.
            #   conv(BG, occupied)        → must be 0 (no obstacle or bay body in BG footprint)
            #   conv(B,  gap)             → must be 0 (body doesn't clobber existing gaps)
            #   conv(BG, outside)         → must be 0 (BG stays inside warehouse)
            conv_bg_occ = _convolve_forbidden(state.occupied, k.bodygap_mask)
            conv_bg_out = _convolve_forbidden(outside, k.bodygap_mask)
            conv_b_gap  = _convolve_forbidden(state.gap, k.body_mask)

            # The three convolutions have different output shapes. Align them
            # to the body+gap top-left anchor grid.
            #   conv_bg_*  shape = (rows-bg_h+1, cols-bg_w+1)
            #   conv_b_gap shape = (rows-bh+1,   cols-bw+1)
            # Body is positioned at (anchor_row_off, anchor_col_off) inside BG,
            # so for BG anchor (R, C) the body anchor is (R + anchor_row_off,
            # C + anchor_col_off).
            bg_rows_out, bg_cols_out = conv_bg_occ.shape
            bh, bw = k.body_mask.shape
            # Extract aligned body-gap-anchor slices:
            ar = k.anchor_row_off
            ac = k.anchor_col_off
            b_slice = conv_b_gap[ar:ar + bg_rows_out, ac:ac + bg_cols_out]

            legal = (conv_bg_occ == 0) & (conv_bg_out == 0) & (b_slice == 0)

            if not legal.any():
                continue

            # Iterate over legal BG-anchor cells. Body anchor in state grid
            # is (R + ar, C + ac). We want the *body's* bottom-left cell to
            # lie inside the destroyed rect [r0, r1) × [c0, c1).
            #
            # Ceiling filter: for body anchor col B_col and body width bw,
            # need min(ceiling_profile[B_col : B_col + bw]) >= min_height.
            # We vectorize by computing a running-min of ceiling_profile over
            # windows of size bw.
            if bw <= len(state.ceiling_profile):
                # sliding-window min via reduceat trick — use a simple loop
                # since bw is small and cols are modest.
                ceil_ok_cols = _ceiling_window_ok(
                    state.ceiling_profile, bw, k.min_height_mm
                )
            else:
                continue

            # Restrict to BG anchors that produce body anchors inside rect
            # AND whose body column span passes the ceiling filter.
            R_idx, C_idx = np.where(legal)
            if R_idx.size == 0:
                continue
            body_rows_idx = R_idx + ar
            body_cols_idx = C_idx + ac

            in_rect = (
                (body_rows_idx >= r0) & (body_rows_idx < r1) &
                (body_cols_idx >= c0) & (body_cols_idx < c1)
            )
            # ceiling_ok: body_cols_idx index into ceil_ok_cols, which has
            # length cols - bw + 1. Every valid body_cols_idx must be in that
            # range (the convolution bounds already guarantee that).
            ceil_ok = ceil_ok_cols[body_cols_idx]
            keep = in_rect & ceil_ok
            if not keep.any():
                continue

            body_rows_idx = body_rows_idx[keep]
            body_cols_idx = body_cols_idx[keep]

            # Score: efficiency is area * loads / price. Higher is better.
            eff = bt.efficiency
            for br, bc in zip(body_rows_idx, body_cols_idx):
                candidates.append(Candidate(
                    bay_type_id=bt.id,
                    rotation=rot,
                    row=int(br),
                    col=int(bc),
                    score=float(eff),
                ))

    return candidates


def _ceiling_window_ok(
    profile: np.ndarray, window: int, min_height: int
) -> np.ndarray:
    """True at column c iff min(profile[c : c+window]) >= min_height.

    profile has shape (cols,). Output has shape (cols - window + 1,).
    """
    # Simple vectorized rolling min using np.minimum.accumulate on window slices.
    # For small windows (bay bodies are rarely more than a few dozen cells) a
    # direct loop is fine and avoids stride-tricks surprises.
    n = len(profile) - window + 1
    if n <= 0:
        return np.zeros(0, dtype=bool)
    out = np.full(n, np.iinfo(profile.dtype).max, dtype=profile.dtype)
    for i in range(window):
        out = np.minimum(out, profile[i:i + n])
    return out >= min_height


# ─────────────────────────────────────────────
# Repair: morphological greedy selection
# ─────────────────────────────────────────────

def greedy_repair(
    state: LNSState,
    candidates: List[Candidate],
    kernels: Dict[int, Dict[int, BayKernel]],
    bay_types_map: Dict[int, BayType],
    rng: random.Random,
) -> List[PlacedBay]:
    """Greedily accept candidates in score order. First accepted always goes
    in; each subsequent candidate is only accepted if its body+gap footprint
    doesn't collide with the partial reconstruction.

    Uses bitmask ops directly on state.occupied / state.gap, i.e. every
    accepted candidate is immediately stamped.

    Returns the list of newly placed bays.
    """
    if not candidates:
        return []

    # Sort by score descending. Tiny jitter for tie-breaking gives SA diversity.
    candidates.sort(
        key=lambda c: (-c.score, rng.random())
    )

    newly_placed: List[PlacedBay] = []

    for cand in candidates:
        k = kernels[cand.bay_type_id][cand.rotation]
        br, bc = cand.row, cand.col
        bh, bw = k.body_mask.shape

        # Check against the current occupancy state (may have tightened since
        # candidate generation due to previously-accepted candidates).
        if state.occupied[br:br + bh, bc:bc + bw].any():
            continue
        bg_r0 = br - k.anchor_row_off
        bg_c0 = bc - k.anchor_col_off
        bgh, bgw = k.bodygap_mask.shape
        # BG cells that are gap-only (bodygap - body)
        gap_patch = k.bodygap_mask.copy()
        gap_patch[k.anchor_row_off:k.anchor_row_off + bh,
                  k.anchor_col_off:k.anchor_col_off + bw] = False
        # Body must not hit existing gap, and BG must not hit existing occupied
        if state.gap[br:br + bh, bc:bc + bw].any():
            continue
        if (state.occupied[bg_r0:bg_r0 + bgh, bg_c0:bg_c0 + bgw] & gap_patch).any():
            continue

        # All clear — place it.
        world_x = state.origin[0] + bc * state.cell_size
        world_y = state.origin[1] + br * state.cell_size
        pb = PlacedBay(
            bay_type=bay_types_map[cand.bay_type_id],
            x=world_x,
            y=world_y,
            rotation=cand.rotation,
        )
        state.insert(pb, kernels)
        newly_placed.append(pb)

    return newly_placed


# ─────────────────────────────────────────────
# Scoring & SA accept
# ─────────────────────────────────────────────

def q_score(placed: List[PlacedBay], usable_area_val: float) -> float:
    return compute_score(placed, usable_area_val)


def sa_accept(delta_q: float, temperature: float, rng: random.Random) -> bool:
    """Accept a worse solution with Boltzmann probability. Better ⇒ always accept."""
    if delta_q <= 0:
        return True
    if temperature <= 0:
        return False
    return rng.random() < math.exp(-delta_q / temperature)


# ─────────────────────────────────────────────
# Outer loop
# ─────────────────────────────────────────────

@dataclass
class LNSConfig:
    time_limit: float = 3.0
    initial_temperature: float = 500.0
    final_temperature: float = 1.0
    destroy_radius_factor: float = 5.0  # radius (in cells) = factor × max_bay_dim_cells
    rng_seed: int = 42
    verbose: bool = False


@dataclass
class LNSResult:
    best_placed: List[PlacedBay]
    best_score: float
    iterations: int
    accepted: int
    improved: int
    elapsed: float


def run_lns_sa(
    case_dir: str,
    solution_csv: str,
    config: LNSConfig = LNSConfig(),
) -> LNSResult:
    """Run LNS+SA starting from the solution at `solution_csv`.

    Does not write any files; return the best placements in `LNSResult`.
    The caller is responsible for persisting the result.
    """
    rng = random.Random(config.rng_seed)

    # Load scene
    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    usable_area_val = usable_area(warehouse, obstacles)

    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
    bay_types_map = {bt.id: bt for bt in bay_types}

    state = LNSState.from_case(case_dir, solution_csv)
    kernels = build_kernels(bay_types, state.cell_size)

    current_score = q_score(state.placed, usable_area_val)
    best_placed = list(state.placed)
    best_score = current_score

    start = time.time()
    it = 0
    accepted = 0
    improved = 0

    # Linear cooling based on elapsed time fraction.
    t_init = config.initial_temperature
    t_final = config.final_temperature

    while time.time() - start < config.time_limit:
        it += 1
        progress = (time.time() - start) / config.time_limit
        temperature = t_init + (t_final - t_init) * progress

        # Snapshot state for rollback.
        snap_occ = state.occupied.copy()
        snap_gap = state.gap.copy()
        snap_placed = list(state.placed)

        # Destroy: pick the sparsest region, expand to contain intersecting bays.
        rect, removed = destroy_low_density(
            state, kernels, config.destroy_radius_factor, rng,
        )

        # If nothing was removed, the destroy did nothing productive. Don't
        # burn FFT time on the repair — just roll forward.
        if not removed:
            continue

        # Repair (convolutional greedy + morphological filter)
        candidates = generate_candidates(state, kernels, rect, bay_types)
        greedy_repair(state, candidates, kernels, bay_types_map, rng)

        # Evaluate
        new_score = q_score(state.placed, usable_area_val)
        delta = new_score - current_score

        if sa_accept(delta, temperature, rng):
            current_score = new_score
            accepted += 1
            if new_score < best_score:
                best_score = new_score
                best_placed = list(state.placed)
                improved += 1
                if config.verbose:
                    print(f"    [lns] it={it:>4} T={temperature:>7.1f} "
                          f"Q={new_score:>10.2f} ★ (removed={len(removed)} "
                          f"added={len(state.placed) - (len(snap_placed) - len(removed))})")
            elif config.verbose and it % 10 == 0:
                print(f"    [lns] it={it:>4} T={temperature:>7.1f} Q={new_score:>10.2f}")
        else:
            # Reject → rollback.
            state.occupied = snap_occ
            state.gap = snap_gap
            state.placed = snap_placed
            if config.verbose and it % 20 == 0:
                print(f"    [lns] it={it:>4} T={temperature:>7.1f} "
                      f"Q={new_score:>10.2f} rejected")

    return LNSResult(
        best_placed=best_placed,
        best_score=best_score,
        iterations=it,
        accepted=accepted,
        improved=improved,
        elapsed=time.time() - start,
    )


# ─────────────────────────────────────────────
# Validation (optional safety net)
# ─────────────────────────────────────────────

def validate_with_shapely(
    case_dir: str, placed: List[PlacedBay]
) -> Tuple[bool, List[str]]:
    """Full ground-truth validation using Shapely (same checks as validate.py).

    Separate from the LNS inner loop so the loop stays bitmap-fast. Call this
    after the LNS run, not on every iteration. Returns (ok, error_messages).
    """
    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    ceiling = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))

    engine = CollisionEngine(warehouse, obstacles, ceiling)
    errors: List[str] = []
    for i, pb in enumerate(placed):
        if not engine.can_place(pb):
            errors.append(f"bay {i} (type={pb.bay_type.id} x={pb.x} y={pb.y} "
                          f"rot={pb.rotation}): fails engine.can_place")
        else:
            engine.place(pb)
    return len(errors) == 0, errors


# ─────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────

def _write_solution(placed: List[PlacedBay], path: str) -> None:
    with open(path, 'w') as f:
        for pb in placed:
            f.write(f"{pb.bay_type.id}, {int(pb.x)}, {int(pb.y)}, {pb.rotation}\n")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 lns_sa.py <case_dir> <input_solution.csv> [output_solution.csv]")
        sys.exit(1)

    case_dir = sys.argv[1]
    in_sol = sys.argv[2]
    out_sol = sys.argv[3] if len(sys.argv) > 3 else in_sol.replace('.csv', '_lns.csv')

    cfg = LNSConfig(time_limit=3.0, verbose=True)
    print(f"[lns] {case_dir}  ←  {in_sol}  (budget={cfg.time_limit}s)")

    # Starting score (for comparison)
    wh = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obs = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    usable = usable_area(wh, obs)
    bts = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
    bt_map = {b.id: b for b in bts}
    starting = _load_solution(in_sol, bt_map)
    print(f"[lns] starting: {len(starting)} bays Q={compute_score(starting, usable):.2f}")

    result = run_lns_sa(case_dir, in_sol, cfg)
    print(f"[lns] finished: iterations={result.iterations} accepted={result.accepted} "
          f"improved={result.improved} elapsed={result.elapsed:.2f}s")
    print(f"[lns] best:     {len(result.best_placed)} bays Q={result.best_score:.2f}")

    ok, errs = validate_with_shapely(case_dir, result.best_placed)
    if ok:
        print("[lns] shapely validation: OK")
    else:
        print(f"[lns] shapely validation: {len(errs)} errors")
        for e in errs[:10]:
            print(f"        {e}")

    _write_solution(result.best_placed, out_sol)
    print(f"[lns] wrote → {out_sol}")
