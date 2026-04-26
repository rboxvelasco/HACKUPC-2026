#!/usr/bin/env python3
"""
Compare wall time of the greedy pipeline with the current pruned CRITERIA
set vs the original 6-criteria baseline, case by case.

Baseline numbers come from traces/criteria_benchmark.csv (rows with
criterion='__baseline_all__'). We re-run the pipeline in-process using the
same time_limit so differences reflect the criteria change and nothing else.

Outputs:
    traces/pruned_vs_full.csv
    stdout summary table
"""

import csv
import os
import sys
import time
from typing import Dict

import greedy_solver as gs
from solver import (
    parse_warehouse, parse_obstacles, parse_ceiling, parse_bay_types,
    compute_score, usable_area,
)


WHOLE_TIME_LIMIT = 4.0
REGIONAL_TIME_LIMIT = 4.0


def load_baseline(csv_path: str) -> Dict[str, dict]:
    """Read baseline (all-criteria) rows from the criteria benchmark CSV."""
    out: Dict[str, dict] = {}
    if not os.path.exists(csv_path):
        return out
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('criterion') != '__baseline_all__':
                continue
            out[row['case']] = {
                't_whole': float(row['t_whole_s']),
                't_regional': float(row['t_regional_s']),
                't_total': float(row['t_total_s']),
                'q': float(row['q']),
                'coverage': float(row['coverage']),
                'n_bays': int(row['n_bays']),
            }
    return out


def run_case(case_dir: str) -> dict:
    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    ceiling = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))
    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
    ua = usable_area(warehouse, obstacles)

    t0 = time.time()
    placed_whole, _ = gs.solve_one_case(
        warehouse, obstacles, ceiling, bay_types,
        time_limit=WHOLE_TIME_LIMIT, verbose=False,
    )
    t_whole = time.time() - t0

    t1 = time.time()
    placed_regional, _rects = gs.solve_one_case_regional(
        warehouse, obstacles, ceiling, bay_types,
        time_limit=REGIONAL_TIME_LIMIT, verbose=False,
    )
    t_regional = time.time() - t1

    q_whole = compute_score(placed_whole, ua) if placed_whole else float('inf')
    q_regional = compute_score(placed_regional, ua) if placed_regional else float('inf')
    if q_regional < q_whole:
        placed = placed_regional
        q = q_regional
        winner = 'regional'
    else:
        placed = placed_whole
        q = q_whole
        winner = 'whole'

    coverage = (sum(p.bay_type.area for p in placed) / ua) if ua > 0 and placed else 0.0

    return {
        't_whole': t_whole,
        't_regional': t_regional,
        't_total': t_whole + t_regional,
        'q': q,
        'coverage': coverage,
        'n_bays': len(placed),
        'winner': winner,
    }


def main():
    cases_dir = sys.argv[1] if len(sys.argv) > 1 else 'Cases'
    os.makedirs('traces', exist_ok=True)
    out_csv = os.path.join('traces', 'pruned_vs_full.csv')

    baseline = load_baseline(os.path.join('traces', 'criteria_benchmark.csv'))

    case_dirs = sorted(
        os.path.join(cases_dir, d)
        for d in os.listdir(cases_dir)
        if os.path.isdir(os.path.join(cases_dir, d))
    )

    print(f"=== Pruned ({len(gs.CRITERIA)} criteria) vs Full (6 criteria) ===")
    print(f"Active criteria: {[n for n,_ in gs.CRITERIA]}")
    print(f"Budget per run:  whole={WHOLE_TIME_LIMIT}s regional={REGIONAL_TIME_LIMIT}s")
    print()

    rows = []
    tot_pruned = 0.0
    tot_full = 0.0
    q_pruned_sum = 0.0
    q_full_sum = 0.0

    for case_dir in case_dirs:
        case_name = os.path.basename(case_dir)
        r = run_case(case_dir)
        base = baseline.get(case_name)
        rows.append((case_name, r, base))

        tot_pruned += r['t_total']
        if base is not None:
            tot_full += base['t_total']
            q_full_sum += base['q']
        q_pruned_sum += r['q']

    # Write CSV
    with open(out_csv, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow([
            'case',
            'pruned_t_total_s', 'pruned_t_whole_s', 'pruned_t_regional_s',
            'pruned_q', 'pruned_coverage', 'pruned_n_bays', 'pruned_winner',
            'full_t_total_s', 'full_t_whole_s', 'full_t_regional_s',
            'full_q', 'full_coverage', 'full_n_bays',
            'speedup_x', 't_saved_s', 't_saved_pct',
            'delta_q', 'delta_q_pct',
        ])
        for case_name, r, base in rows:
            if base is not None:
                speedup = base['t_total'] / r['t_total'] if r['t_total'] > 0 else 0.0
                t_saved = base['t_total'] - r['t_total']
                t_saved_pct = (t_saved / base['t_total'] * 100.0) if base['t_total'] > 0 else 0.0
                dq = r['q'] - base['q']
                dq_pct = (dq / base['q'] * 100.0) if base['q'] > 0 else 0.0
                w.writerow([
                    case_name,
                    f"{r['t_total']:.3f}", f"{r['t_whole']:.3f}", f"{r['t_regional']:.3f}",
                    f"{r['q']:.2f}", f"{r['coverage']:.4f}", r['n_bays'], r['winner'],
                    f"{base['t_total']:.3f}", f"{base['t_whole']:.3f}", f"{base['t_regional']:.3f}",
                    f"{base['q']:.2f}", f"{base['coverage']:.4f}", base['n_bays'],
                    f"{speedup:.2f}", f"{t_saved:.3f}", f"{t_saved_pct:.2f}",
                    f"{dq:+.2f}", f"{dq_pct:+.2f}",
                ])
            else:
                w.writerow([
                    case_name,
                    f"{r['t_total']:.3f}", f"{r['t_whole']:.3f}", f"{r['t_regional']:.3f}",
                    f"{r['q']:.2f}", f"{r['coverage']:.4f}", r['n_bays'], r['winner'],
                    '', '', '', '', '', '',
                    '', '', '',
                    '', '',
                ])

    # Print table
    print(f"{'case':<26}{'full_t':>9}{'pruned_t':>10}{'Δt':>9}{'speedup':>10} "
          f"{'full_Q':>10}{'pruned_Q':>10}{'ΔQ%':>8}")
    print("-" * 94)
    for case_name, r, base in rows:
        if base is None:
            print(f"{case_name:<26}{'—':>9}{r['t_total']:>10.2f}{'—':>9}{'—':>10} "
                  f"{'—':>10}{r['q']:>10.2f}{'—':>8}")
            continue
        speedup = base['t_total'] / r['t_total'] if r['t_total'] > 0 else 0.0
        dt = r['t_total'] - base['t_total']
        dq_pct = (r['q'] - base['q']) / base['q'] * 100.0 if base['q'] > 0 else 0.0
        print(f"{case_name:<26}{base['t_total']:>8.2f}s{r['t_total']:>9.2f}s"
              f"{dt:>+8.2f}s{speedup:>9.2f}x "
              f"{base['q']:>10.2f}{r['q']:>10.2f}{dq_pct:>+7.2f}%")
    print("-" * 94)
    if tot_full > 0:
        overall_speedup = tot_full / tot_pruned if tot_pruned > 0 else 0.0
        print(f"{'TOTAL':<26}{tot_full:>8.2f}s{tot_pruned:>9.2f}s"
              f"{(tot_pruned-tot_full):>+8.2f}s{overall_speedup:>9.2f}x "
              f"{q_full_sum:>10.2f}{q_pruned_sum:>10.2f}")
    print()
    print(f"Wrote {out_csv}")


if __name__ == '__main__':
    main()
