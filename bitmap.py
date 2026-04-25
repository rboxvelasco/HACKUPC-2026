#!/usr/bin/env python3
"""
Bitmap rasterization for warehouse solutions.

Produces two boolean numpy arrays at a resolution determined by the GCD of
every coordinate in the scene (bay widths/depths/gaps, obstacle positions and
sizes, warehouse vertices). Using the GCD guarantees every feature edge falls
on a cell boundary — no rounding, no lost slivers.

Arrays use shape (rows, cols) with row 0 at y=0 (bottom), matching the
warehouse coordinate system. Each cell spans `cell_size` millimetres.

  bitmap_occupied:  True where a cell is inside an obstacle OR a placed bay body
  bitmap_gap:       True where a cell is inside a placed bay's gap zone (body excluded)

Cells outside the warehouse polygon are False in both bitmaps.

Usage (CLI):
    python3 bitmap.py <case_dir> <solution_csv> [output_npz]

Usage (library):
    from bitmap import rasterize_solution
    result = rasterize_solution("Cases/Case0", "solutions/Case0.csv")
    occ = result.bitmap_occupied   # shape (rows, cols), dtype=bool
    gap = result.bitmap_gap        # shape (rows, cols), dtype=bool
    cell = result.cell_size        # mm per cell
"""

import os
import sys
from dataclasses import dataclass
from functools import reduce
from math import gcd
from typing import List, Tuple

import numpy as np

from solver import (
    PlacedBay,
    parse_bay_types,
    parse_obstacles,
    parse_warehouse,
)


@dataclass
class RasterResult:
    bitmap_occupied: np.ndarray  # bool, shape (rows, cols)
    bitmap_gap: np.ndarray       # bool, shape (rows, cols)
    cell_size: int               # mm per cell (the GCD)
    origin: Tuple[int, int]      # (x, y) world coords of cell (0, 0), in mm
    shape: Tuple[int, int]       # (rows, cols)


def _collect_grid_dimensions(
    warehouse_coords: List[Tuple[int, int]],
    obstacles_raw: List[Tuple[int, int, int, int]],
    bay_dims: List[Tuple[int, int, int]],  # (width, depth, gap)
    placements: List[Tuple[int, int, int, int, int]],  # (x, y, w_eff, d_eff, gap_dir_axis)
) -> List[int]:
    """Collect every coordinate/dimension that must snap to the grid."""
    nums: List[int] = []
    for x, y in warehouse_coords:
        nums.extend([x, y])
    for ox, oy, ow, od in obstacles_raw:
        nums.extend([ox, oy, ow, od])
    for w, d, g in bay_dims:
        nums.extend([w, d, g])
    for px, py, *_ in placements:
        nums.extend([px, py])
    # Drop zeros — gcd(0, n) = n so they don't affect result, but keep it tidy
    return [abs(n) for n in nums if n != 0]


def _compute_cell_size(nums: List[int]) -> int:
    if not nums:
        return 1
    return reduce(gcd, nums)


def _parse_obstacles_raw(filepath: str) -> List[Tuple[int, int, int, int]]:
    """Read obstacles as raw (x, y, w, d) tuples (separate from the Shapely version)."""
    out: List[Tuple[int, int, int, int]] = []
    if not os.path.exists(filepath):
        return out
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) < 4:
                continue
            out.append((int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])))
    return out


def _parse_warehouse_coords(filepath: str) -> List[Tuple[int, int]]:
    coords: List[Tuple[int, int]] = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(',')]
            coords.append((int(parts[0]), int(parts[1])))
    return coords


def _load_placements(
    solution_csv: str, bay_type_map: dict
) -> List[PlacedBay]:
    placed: List[PlacedBay] = []
    with open(solution_csv) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(',')]
            bay_id = int(parts[0])
            x = int(parts[1])
            y = int(parts[2])
            rot = int(parts[3])
            placed.append(PlacedBay(
                bay_type=bay_type_map[bay_id], x=x, y=y, rotation=rot,
            ))
    return placed


def _bay_body_rect(pb: PlacedBay) -> Tuple[int, int, int, int]:
    """Return (x0, y0, x1, y1) of the bay body in world mm."""
    w, d = pb.get_body_dims()
    return int(pb.x), int(pb.y), int(pb.x + w), int(pb.y + d)


def _bay_gap_rect(pb: PlacedBay) -> Tuple[int, int, int, int]:
    """Return (x0, y0, x1, y1) of the bay's gap-only region (body excluded).

    Rotation convention (matches solver.py):
      0   → gap on +Y side
      90  → gap on −X side
      180 → gap on −Y side
      270 → gap on +X side
    """
    w, d = pb.get_body_dims()
    g = pb.bay_type.gap
    x, y = int(pb.x), int(pb.y)
    if pb.rotation == 0:
        return x, y + d, x + w, y + d + g
    if pb.rotation == 90:
        return x - g, y, x, y + d
    if pb.rotation == 180:
        return x, y - g, x + w, y
    if pb.rotation == 270:
        return x + w, y, x + w + g, y + d
    # Safety: unknown rotation → empty rect
    return x, y, x, y


def _fill_rect(
    bitmap: np.ndarray,
    x0: int, y0: int, x1: int, y1: int,
    origin_x: int, origin_y: int,
    cell: int,
) -> None:
    """Fill a world-coordinate rect into the bitmap. Clips to array bounds."""
    rows, cols = bitmap.shape
    col_start = (x0 - origin_x) // cell
    col_end = (x1 - origin_x) // cell
    row_start = (y0 - origin_y) // cell
    row_end = (y1 - origin_y) // cell
    # Clip
    col_start = max(0, col_start)
    col_end = min(cols, col_end)
    row_start = max(0, row_start)
    row_end = min(rows, row_end)
    if col_start < col_end and row_start < row_end:
        bitmap[row_start:row_end, col_start:col_end] = True


def _fill_polygon_mask(
    coords: List[Tuple[int, int]],
    origin_x: int, origin_y: int,
    cell: int,
    rows: int, cols: int,
) -> np.ndarray:
    """Rasterize a polygon (given by integer mm vertices) into a bool mask.

    Supports arbitrary axis-aligned or convex polygons by using a scanline
    even-odd fill. Since every vertex coordinate is a multiple of `cell`,
    the scanline boundaries land exactly on cell edges.
    """
    mask = np.zeros((rows, cols), dtype=bool)
    n = len(coords)
    if n < 3:
        return mask

    # Convert to cell coordinates (exact because cell divides every coord)
    cell_coords = [
        ((x - origin_x) // cell, (y - origin_y) // cell) for x, y in coords
    ]

    # For each row, find x-crossings of polygon edges at the row's centerline
    for row in range(rows):
        # Use row center (row + 0.5) so we don't hit vertices exactly
        y = row + 0.5
        xs: List[float] = []
        for i in range(n):
            x1, y1 = cell_coords[i]
            x2, y2 = cell_coords[(i + 1) % n]
            if (y1 <= y < y2) or (y2 <= y < y1):
                # Edge crosses this scanline
                t = (y - y1) / (y2 - y1)
                xs.append(x1 + t * (x2 - x1))
        xs.sort()
        # Even-odd fill between pairs
        for i in range(0, len(xs) - 1, 2):
            col_start = max(0, int(np.ceil(xs[i] - 1e-9)))
            col_end = min(cols, int(np.floor(xs[i + 1] + 1e-9)))
            if col_start < col_end:
                mask[row, col_start:col_end] = True
    return mask


def rasterize_solution(
    case_dir: str, solution_csv: str, cell_size: int = None
) -> RasterResult:
    """Rasterize a case + solution into two boolean bitmaps.

    Args:
        case_dir: path to a case directory (contains warehouse.csv,
                  obstacles.csv, types_of_bays.csv).
        solution_csv: path to a solution CSV in the format produced by
                      solver.py: `id, x, y, rotation` per line.
        cell_size: override for the cell resolution in mm. If None, uses the
                   GCD of every coordinate in the scene (exact, lossless).

    Returns:
        RasterResult with bitmap_occupied, bitmap_gap, cell_size, origin, shape.
    """
    wh_coords = _parse_warehouse_coords(os.path.join(case_dir, 'warehouse.csv'))
    obs_raw = _parse_obstacles_raw(os.path.join(case_dir, 'obstacles.csv'))
    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
    bay_type_map = {bt.id: bt for bt in bay_types}

    placements = _load_placements(solution_csv, bay_type_map)

    # Decide cell size
    if cell_size is None:
        bay_dims = [(bt.width, bt.depth, bt.gap) for bt in bay_types]
        placement_tuples = [
            (int(pb.x), int(pb.y), 0, 0, 0) for pb in placements
        ]
        nums = _collect_grid_dimensions(wh_coords, obs_raw, bay_dims, placement_tuples)
        cell_size = _compute_cell_size(nums)

    # Grid extent is taken from warehouse bounding box
    xs = [x for x, _ in wh_coords]
    ys = [y for _, y in wh_coords]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    origin_x, origin_y = min_x, min_y
    cols = (max_x - min_x) // cell_size
    rows = (max_y - min_y) // cell_size

    # Warehouse interior mask — restricts both bitmaps to inside the polygon
    inside = _fill_polygon_mask(wh_coords, origin_x, origin_y, cell_size, rows, cols)

    # Occupied bitmap: obstacles ∪ bay bodies, clipped to interior
    occupied = np.zeros((rows, cols), dtype=bool)
    for ox, oy, ow, od in obs_raw:
        _fill_rect(occupied, ox, oy, ox + ow, oy + od, origin_x, origin_y, cell_size)
    for pb in placements:
        x0, y0, x1, y1 = _bay_body_rect(pb)
        _fill_rect(occupied, x0, y0, x1, y1, origin_x, origin_y, cell_size)
    occupied &= inside

    # Gap bitmap: union of bay gap rects (body excluded), clipped to interior
    gap = np.zeros((rows, cols), dtype=bool)
    for pb in placements:
        x0, y0, x1, y1 = _bay_gap_rect(pb)
        if x1 > x0 and y1 > y0:
            _fill_rect(gap, x0, y0, x1, y1, origin_x, origin_y, cell_size)
    gap &= inside
    # Defensive: if a gap somehow overlaps a body (shouldn't per the rules),
    # keep it flagged as occupied, not as gap.
    gap &= ~occupied

    return RasterResult(
        bitmap_occupied=occupied,
        bitmap_gap=gap,
        cell_size=cell_size,
        origin=(origin_x, origin_y),
        shape=(rows, cols),
    )


def _print_summary(result: RasterResult) -> None:
    rows, cols = result.shape
    occ = result.bitmap_occupied
    gap = result.bitmap_gap
    total = rows * cols
    print(f"  cell_size = {result.cell_size} mm")
    print(f"  shape     = {rows} rows × {cols} cols ({total:,} cells)")
    print(f"  origin    = {result.origin} mm")
    print(f"  occupied  = {int(occ.sum()):>8,} cells ({100*occ.mean():.1f}%)")
    print(f"  gap       = {int(gap.sum()):>8,} cells ({100*gap.mean():.1f}%)")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 bitmap.py <case_dir> <solution_csv> [output.npz]")
        sys.exit(1)

    case_dir = sys.argv[1]
    solution_csv = sys.argv[2]
    out_path = sys.argv[3] if len(sys.argv) > 3 else None

    result = rasterize_solution(case_dir, solution_csv)
    _print_summary(result)

    if out_path:
        np.savez_compressed(
            out_path,
            occupied=result.bitmap_occupied,
            gap=result.bitmap_gap,
            cell_size=np.array(result.cell_size),
            origin=np.array(result.origin),
        )
        print(f"  saved     → {out_path}")
