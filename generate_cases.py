#!/usr/bin/env python3
"""Generate synthetic test cases to stress-test the solver.

Creates Cases/Case4..Case9 with distinct geometries and catalogs to
exercise edge cases that Cases 0-3 don't cover.

Usage: python3 generate_cases.py
"""

import os
from typing import List, Tuple


CATALOGS = {
    'small': [
        # id, width, depth, height, gap, nLoads, price
        (0,  800, 1200, 2800, 200,  4, 2000),
        (1, 1600, 1200, 2800, 200,  8, 2500),
        (2, 2400, 1200, 2800, 200, 12, 2800),
        (3,  800, 1000, 1800, 150,  3, 1800),
        (4, 1600, 1000, 1800, 150,  6, 2300),
        (5, 2400, 1000, 1800, 150,  9, 2600),
    ],
    'large': [
        (0, 1300, 1000, 1400, 500,  1, 1000),
        (1, 1300, 1000, 2800, 500,  2, 1300),
        (2, 1300, 1000, 4200, 500,  3, 1600),
        (3, 1300, 1000, 5600, 500,  4, 1900),
        (4, 2300, 1000, 1400, 500,  2, 1600),
        (5, 2300, 1000, 2800, 500,  4, 2080),
        (6, 2300, 1000, 4200, 500,  6, 2560),
        (7, 2300, 1000, 5600, 500,  8, 3040),
        (8, 3300, 1000, 1400, 500,  3, 2200),
        (9, 3300, 1000, 2800, 500,  6, 2860),
        (10, 3300, 1000, 4200, 500,  9, 3520),
        (11, 3300, 1000, 5600, 500, 12, 4180),
        (12, 4300, 1000, 1400, 500,  4, 2800),
        (13, 4300, 1000, 2800, 500,  8, 3640),
        (14, 4300, 1000, 4200, 500, 12, 4480),
        (15, 4300, 1000, 5600, 500, 16, 5320),
    ],
    'mixed': [
        # Multiple depths: forces interesting row-pitch decisions
        (0,  1000, 1200, 2800, 200,  3, 1600),
        (1,  2000, 1200, 2800, 200,  6, 2100),
        (2,  3000, 1200, 2800, 200,  9, 2500),
        (3,  1000,  800, 2500, 150,  2, 1200),
        (4,  2000,  800, 2500, 150,  4, 1700),
        (5,  3000,  800, 2500, 150,  6, 2100),
        (6,  1500, 1500, 3200, 300,  5, 2400),
        (7,  3000, 1500, 3200, 300, 10, 3200),
    ],
}


def write_case(case_name: str,
               warehouse: List[Tuple[int, int]],
               obstacles: List[Tuple[int, int, int, int]],
               ceiling: List[Tuple[int, int]],
               catalog_name: str):
    """Write a case to Cases/<case_name>/."""
    dir_path = os.path.join('Cases', case_name)
    os.makedirs(dir_path, exist_ok=True)

    with open(os.path.join(dir_path, 'warehouse.csv'), 'w') as f:
        for (x, y) in warehouse:
            f.write(f"{x},{y}\n")

    with open(os.path.join(dir_path, 'obstacles.csv'), 'w') as f:
        for (x, y, w, d) in obstacles:
            f.write(f"{x}, {y}, {w}, {d}\n")

    with open(os.path.join(dir_path, 'ceiling.csv'), 'w') as f:
        for (x, h) in ceiling:
            f.write(f"{x}, {h}\n")

    catalog = CATALOGS[catalog_name]
    with open(os.path.join(dir_path, 'types_of_bays.csv'), 'w') as f:
        for row in catalog:
            f.write(f"{row[0]}, {row[1]}, {row[2]}, {row[3]}, "
                    f"{row[4]}, {row[5]}, {row[6]}\n")

    print(f"  Wrote {case_name}: {len(warehouse)} vertices, "
          f"{len(obstacles)} obstacles, catalog={catalog_name}")


# ─────────────────────────────────────────────
# Case 4: T-shape (tests region decomposition with 3 arms)
# ─────────────────────────────────────────────
#     ┌──────────────────┐
#     │                  │
#     │       stem       │
#     │                  │
#     └──┐            ┌──┘
#        │            │
#        │    top     │
#        │            │
#        └────────────┘
#
#  Top bar: 0..16000 × 0..5000
#  Stem (centered): 5000..11000 × 5000..14000
#
# Actually let's do an inverted T: bar at bottom, stem going up.

def case_4_T_shape():
    warehouse = [
        (0, 0), (16000, 0), (16000, 5000),
        (11000, 5000), (11000, 14000),
        (5000, 14000), (5000, 5000),
        (0, 5000),
    ]
    obstacles = []
    ceiling = [(0, 3000), (16000, 3000)]
    write_case('Case4_T_shape', warehouse, obstacles, ceiling, 'large')


# ─────────────────────────────────────────────
# Case 5: U-shape warehouse (tests dead-zone handling)
# ─────────────────────────────────────────────
#  ┌────┐      ┌────┐
#  │    │      │    │
#  │    │      │    │
#  │    └──────┘    │
#  │                │
#  └────────────────┘
#
# Two 4000-wide vertical arms + 2000-tall horizontal base

def case_5_U_shape():
    warehouse = [
        (0, 0), (14000, 0), (14000, 12000),
        (10000, 12000), (10000, 4000),
        (4000, 4000), (4000, 12000),
        (0, 12000),
    ]
    obstacles = []
    ceiling = [(0, 3500), (14000, 3500)]
    write_case('Case5_U_shape', warehouse, obstacles, ceiling, 'large')


# ─────────────────────────────────────────────
# Case 6: Many small obstacles (stress the obstacle handling)
# ─────────────────────────────────────────────

def case_6_obstacle_field():
    warehouse = [(0, 0), (12000, 0), (12000, 12000), (0, 12000)]
    # Grid of 6 obstacles — 2×3 arrangement of 500×500 columns
    obstacles = []
    for row in range(3):
        for col in range(2):
            x = 3000 + col * 5500
            y = 2000 + row * 3500
            obstacles.append((x, y, 500, 500))
    ceiling = [(0, 3500), (12000, 3500)]
    write_case('Case6_obstacle_field', warehouse, obstacles, ceiling, 'large')


# ─────────────────────────────────────────────
# Case 7: Variable ceiling with multiple drops (tests type mixing)
# ─────────────────────────────────────────────

def case_7_variable_ceiling():
    warehouse = [(0, 0), (16000, 0), (16000, 8000), (0, 8000)]
    obstacles = []
    # Multiple ceiling drops along X
    ceiling = [
        (0, 3500),
        (4000, 2000),   # dropped: only short bays fit
        (8000, 3500),
        (12000, 1800),  # even shorter
        (14000, 3500),
    ]
    write_case('Case7_variable_ceiling', warehouse, obstacles, ceiling, 'small')


# ─────────────────────────────────────────────
# Case 8: Long narrow corridor
# ─────────────────────────────────────────────

def case_8_corridor():
    warehouse = [(0, 0), (30000, 0), (30000, 4000), (0, 4000)]
    obstacles = [
        # A few structural pillars along the corridor
        (8000, 1500, 500, 500),
        (16000, 1500, 500, 500),
        (24000, 1500, 500, 500),
    ]
    ceiling = [(0, 3500), (30000, 3500)]
    write_case('Case8_corridor', warehouse, obstacles, ceiling, 'large')


# ─────────────────────────────────────────────
# Case 9: Large complex — irregular polygon + scattered obstacles + ceiling drops
# ─────────────────────────────────────────────

def case_9_complex():
    # L-shape extended with a notch
    warehouse = [
        (0, 0), (18000, 0), (18000, 6000),
        (14000, 6000), (14000, 10000),
        (10000, 10000), (10000, 14000),
        (0, 14000),
    ]
    obstacles = [
        (2000, 2000, 600, 600),    # pillar in main floor
        (6000, 3500, 800, 300),    # wall segment
        (12000, 1500, 500, 500),   # pillar near right wall
        (4000, 8000, 300, 2000),   # vertical wall in left area
        (6500, 11000, 2000, 500),  # long wall in upper area
    ]
    ceiling = [
        (0, 3500),
        (6000, 2500),
        (10000, 3500),
        (14000, 2000),
    ]
    write_case('Case9_complex', warehouse, obstacles, ceiling, 'mixed')


# ─────────────────────────────────────────────
# Case 10: Donut (square with large central obstacle — like Case 2 but ring-shape)
# ─────────────────────────────────────────────

def case_10_donut():
    warehouse = [(0, 0), (15000, 0), (15000, 15000), (0, 15000)]
    obstacles = [(4000, 4000, 7000, 7000)]  # large central obstacle, 7000×7000
    ceiling = [(0, 3500), (15000, 3500)]
    write_case('Case10_donut', warehouse, obstacles, ceiling, 'large')


if __name__ == '__main__':
    print("Generating synthetic test cases...")
    case_4_T_shape()
    case_5_U_shape()
    case_6_obstacle_field()
    case_7_variable_ceiling()
    case_8_corridor()
    case_9_complex()
    case_10_donut()
    print("Done. Test with: python3 greedy_solver.py Cases/Case4_T_shape /tmp/c4.csv")
