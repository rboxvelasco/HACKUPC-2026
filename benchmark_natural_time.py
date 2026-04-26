#!/usr/bin/env python3
"""
Measure the *natural* wall time of the greedy pipeline — how long it would
run if we gave it enough budget to never hit the time_limit cap.

We set time_limit = 120s per path (whole + regional). In practice the
deterministic matrix converges well before that for every case, so the
measured time is the real work time of the algorithm.

Runs twice for the pruned CRITERIA (current) and reports both totals.
"""

import os
import sys
import time

import greedy_solver as gs
from solver import (
    parse_warehouse, parse_obstacles, parse_ceiling, parse_bay_types,
    compute_score, usable_area,
)


HIGH_BUDGET = 120.0  # effectively uncapped for this problem size


def run_case(case_dir: str, budget: float) -> dict:
    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    ceiling = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))
    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
    ua = usable_area(warehouse, obstacles)

    t0 = time.time()
    placed_whole, _ = gs.solve_one_case(
        warehouse, obstacles, ceiling, bay_types,
        time_limit=budget, verbose=False,
    )
    t_whole = time.time() - t0

    t1 = time.time()
    placed_regional, _ = gs.solve_one_case_regional(
        warehouse, obstacles, ceiling, bay_types,
        time_limit=budget, verbose=False,
    )
    t_regional = time.time() - t1

    q_whole = compute_score(placed_whole, ua) if placed_whole else float('inf')
    q_regional = compute_score(placed_regional, ua) if placed_regional else float('inf')
    q = min(q_whole, q_regional)

    return {
        't_whole': t_whole,
        't_regional': t_regional,
        't_total': t_whole + t_regional,
        'q': q,
    }


def main():
    cases_dir = sys.argv[1] if len(sys.argv) > 1 else 'Cases'
    case_dirs = sorted(
        os.path.join(cases_dir, d)
        for d in os.listdir(cases_dir)
        if os.path.isdir(os.path.join(cases_dir, d))
    )

    print(f"=== Natural wall time — greedy (criteria={len(gs.CRITERIA)}) ===")
    print(f"Budget per path: {HIGH_BUDGET}s (effectively uncapped)")
    print()
    print(f"{'case':<26}{'t_whole':>10}{'t_regional':>12}{'t_total':>10}{'Q':>12}")
    print('-' * 70)

    tot_w = tot_r = tot_t = 0.0
    for case_dir in case_dirs:
        case_name = os.path.basename(case_dir)
        r = run_case(case_dir, HIGH_BUDGET)
        tot_w += r['t_whole']
        tot_r += r['t_regional']
        tot_t += r['t_total']
        print(f"{case_name:<26}{r['t_whole']:>9.2f}s{r['t_regional']:>11.2f}s"
              f"{r['t_total']:>9.2f}s{r['q']:>12.2f}")

    print('-' * 70)
    print(f"{'TOTAL':<26}{tot_w:>9.2f}s{tot_r:>11.2f}s{tot_t:>9.2f}s")


if __name__ == '__main__':
    main()
