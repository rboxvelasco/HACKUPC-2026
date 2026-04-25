#!/usr/bin/env python3
"""
Validate a solution against a case.
Usage: python3 validate.py <case_dir> <solution_file>
"""

import sys
import os
from shapely.geometry import Polygon, box

from solver import (
    parse_warehouse, parse_obstacles, parse_ceiling, parse_bay_types,
    PlacedBay, compute_score, usable_area,
)


def validate(case_dir: str, solution_file: str) -> bool:
    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    ceiling = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))
    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))

    bay_type_map = {bt.id: bt for bt in bay_types}
    usable_area_val = usable_area(warehouse, obstacles)

    # Parse solution
    placed = []
    with open(solution_file) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split(',')
            if len(parts) < 4:
                print(f"  ERROR line {line_num}: expected 4 fields, got {len(parts)}")
                return False
            bay_id = int(parts[0].strip())
            x = int(parts[1].strip())
            y = int(parts[2].strip())
            rot = int(parts[3].strip())

            if bay_id not in bay_type_map:
                print(f"  ERROR line {line_num}: unknown bay type {bay_id}")
                return False
            if rot not in (0, 90, 180, 270):
                print(f"  WARNING line {line_num}: rotation {rot} not in [0,90,180,270]")

            placed.append(PlacedBay(bay_type=bay_type_map[bay_id], x=x, y=y, rotation=rot))

    print(f"  Loaded {len(placed)} bays from {solution_file}")

    errors = 0

    # Check each bay
    for i, pb in enumerate(placed):
        body = pb.get_body_polygon()
        body_gap = pb.get_body_with_gap_polygon()

        # Body inside warehouse
        if not warehouse.contains(body):
            print(f"  ERROR bay {i} (type={pb.bay_type.id} at {pb.x},{pb.y} rot={pb.rotation}): "
                  f"body outside warehouse")
            errors += 1

        # Body+gap inside warehouse
        if not warehouse.contains(body_gap):
            print(f"  ERROR bay {i} (type={pb.bay_type.id} at {pb.x},{pb.y} rot={pb.rotation}): "
                  f"body+gap outside warehouse")
            errors += 1

        # Obstacle check
        for j, obs in enumerate(obstacles):
            if body.intersects(obs) and not body.touches(obs):
                print(f"  ERROR bay {i}: body overlaps obstacle {j}")
                errors += 1
            if body_gap.intersects(obs) and not body_gap.touches(obs):
                print(f"  ERROR bay {i}: body+gap overlaps obstacle {j}")
                errors += 1

        # Ceiling check
        x_min, x_max = pb.get_x_range()
        min_ceil = ceiling.min_height_in_range(x_min, x_max)
        if pb.bay_type.height > min_ceil:
            print(f"  ERROR bay {i}: height {pb.bay_type.height} > ceiling {min_ceil} "
                  f"in x=[{x_min},{x_max}]")
            errors += 1

    # Check pairwise overlaps
    for i in range(len(placed)):
        body_i = placed[i].get_body_polygon()
        body_gap_i = placed[i].get_body_with_gap_polygon()
        for j in range(i + 1, len(placed)):
            body_j = placed[j].get_body_polygon()
            body_gap_j = placed[j].get_body_with_gap_polygon()

            # Body i vs body+gap j
            if body_i.intersects(body_gap_j) and not body_i.touches(body_gap_j):
                print(f"  ERROR: bay {i} body overlaps bay {j} body+gap")
                errors += 1

            # Body j vs body+gap i
            if body_j.intersects(body_gap_i) and not body_j.touches(body_gap_i):
                print(f"  ERROR: bay {j} body overlaps bay {i} body+gap")
                errors += 1

    if errors == 0:
        score = compute_score(placed, usable_area_val)
        total_area = sum(pb.bay_type.area for pb in placed)
        coverage = total_area / usable_area_val if usable_area_val > 0 else 0
        print(f"  VALID! {len(placed)} bays, coverage={coverage:.1%}, Q={score:.2f}")
        return True
    else:
        print(f"  INVALID: {errors} errors found")
        return False


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 validate.py <case_dir> <solution_file>")
        sys.exit(1)

    ok = validate(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)
