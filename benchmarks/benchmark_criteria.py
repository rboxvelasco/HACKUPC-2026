#!/usr/bin/env python3
"""
Benchmark greedy criteria — one criterion at a time, per case.

For each case:
  * Run the full `solve_one_case` (whole-warehouse) + `solve_one_case_regional`
    pipeline, but with the criteria matrix restricted to a single criterion.
  * Also run the baseline (all criteria enabled) for reference.
  * Record time, Q, coverage, n_bays, winning path (whole vs regional).

Outputs:
  * traces/criteria_benchmark.csv   — per-(case, criterion) row
  * traces/criteria_benchmark.log   — human-readable log
  * Stdout summary:
      - per-case winner criterion
      - criteria never used as winner  → candidates to drop
      - estimated speedup from dropping them

Usage:
    python3 benchmark_criteria.py [cases_dir]
"""

import os
import sys
import csv
import time
from collections import defaultdict
from typing import List, Tuple

# We import greedy_solver and monkey-patch its CRITERIA list for each run
import greedy_solver as gs
from solver import (
    parse_warehouse, parse_obstacles, parse_ceiling, parse_bay_types,
    compute_score, usable_area,
)


ALL_CRITERIA = list(gs.CRITERIA)  # preserve original order
CRITERION_NAMES = [name for name, _ in ALL_CRITERIA]

# Time budgets: keep them modest so the benchmark itself finishes fast.
# solve_one_case gets time_limit split between whole and regional paths in main,
# so we pass time_limit directly to each.
WHOLE_TIME_LIMIT = 4.0
REGIONAL_TIME_LIMIT = 4.0


def run_pipeline_with_criteria(
    case_dir: str,
    criteria: List[Tuple[str, callable]],
):
    """Run both whole-warehouse and regional paths with the given criteria list.

    Returns dict with: q, coverage, n_bays, t_whole, t_regional, t_total, winner.
    """
    warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
    obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
    ceiling = parse_ceiling(os.path.join(case_dir, 'ceiling.csv'))
    bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
    ua = usable_area(warehouse, obstacles)

    # Monkey-patch the module's CRITERIA list
    gs.CRITERIA = list(criteria)

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
        'q': q,
        'coverage': coverage,
        'n_bays': len(placed),
        't_whole': t_whole,
        't_regional': t_regional,
        't_total': t_whole + t_regional,
        'winner': winner,
    }


def main():
    cases_dir = sys.argv[1] if len(sys.argv) > 1 else 'Cases'
    traces_dir = 'traces'
    os.makedirs(traces_dir, exist_ok=True)

    csv_path = os.path.join(traces_dir, 'criteria_benchmark.csv')
    log_path = os.path.join(traces_dir, 'criteria_benchmark.log')

    case_dirs = sorted(
        os.path.join(cases_dir, d)
        for d in os.listdir(cases_dir)
        if os.path.isdir(os.path.join(cases_dir, d))
    )

    # Results: rows[(case, criterion_label)] = dict
    rows = []
    # Track best criterion per case (among single-criterion runs)
    best_per_case = {}
    # Track baseline per case
    baseline_per_case = {}

    log_lines: List[str] = []

    def log(msg: str):
        print(msg)
        log_lines.append(msg)

    log(f"=== Greedy criteria benchmark ===")
    log(f"Cases:     {len(case_dirs)}")
    log(f"Criteria:  {', '.join(CRITERION_NAMES)}")
    log(f"Budget:    whole={WHOLE_TIME_LIMIT}s regional={REGIONAL_TIME_LIMIT}s per run")
    log("")

    grand_start = time.time()

    for case_dir in case_dirs:
        case_name = os.path.basename(case_dir)
        log(f"--- {case_name} ---")

        # Baseline: all criteria enabled
        log(f"  baseline (all {len(ALL_CRITERIA)} criteria)...")
        base = run_pipeline_with_criteria(case_dir, ALL_CRITERIA)
        base['case'] = case_name
        base['criterion'] = '__baseline_all__'
        rows.append(base)
        baseline_per_case[case_name] = base
        log(f"    Q={base['q']:.2f}  cov={base['coverage']:.1%}  bays={base['n_bays']}  "
            f"t={base['t_total']:.2f}s  winner={base['winner']}")

        # One criterion at a time
        case_best = None
        for crit in ALL_CRITERIA:
            crit_name = crit[0]
            log(f"  solo '{crit_name}'...")
            r = run_pipeline_with_criteria(case_dir, [crit])
            r['case'] = case_name
            r['criterion'] = crit_name
            rows.append(r)
            delta_q = r['q'] - base['q']
            log(f"    Q={r['q']:.2f} (Δ={delta_q:+.2f})  cov={r['coverage']:.1%}  "
                f"bays={r['n_bays']}  t={r['t_total']:.2f}s  winner={r['winner']}")
            if case_best is None or r['q'] < case_best['q']:
                case_best = r

        best_per_case[case_name] = case_best
        log(f"  → best solo criterion: '{case_best['criterion']}' with Q={case_best['q']:.2f}")
        log("")

    grand_time = time.time() - grand_start

    # Write CSV
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'case', 'criterion', 'q', 'coverage', 'n_bays',
            't_whole_s', 't_regional_s', 't_total_s', 'winner',
        ])
        for r in rows:
            writer.writerow([
                r['case'], r['criterion'],
                f"{r['q']:.4f}", f"{r['coverage']:.4f}", r['n_bays'],
                f"{r['t_whole']:.3f}", f"{r['t_regional']:.3f}", f"{r['t_total']:.3f}",
                r['winner'],
            ])
    log(f"Wrote {csv_path}")

    # ── Summary ────────────────────────────────────────────────────────
    log("")
    log("=" * 72)
    log("SUMMARY")
    log("=" * 72)

    # 1. Per-case winner among solo runs + gap vs baseline
    log("")
    log("Best solo criterion per case (Q lower is better):")
    log(f"  {'case':<26} {'baseline_Q':>12} {'best_solo':>12} {'criterion':>14} "
        f"{'ΔQ':>10} {'ΔQ %':>8}")
    for case_name, r in best_per_case.items():
        base = baseline_per_case[case_name]
        dq = r['q'] - base['q']
        dq_pct = (dq / base['q'] * 100.0) if base['q'] > 0 else 0.0
        log(f"  {case_name:<26} {base['q']:>12.2f} {r['q']:>12.2f} "
            f"{r['criterion']:>14} {dq:>+10.2f} {dq_pct:>+7.2f}%")

    # 2. How often each criterion is the best
    winner_counts = defaultdict(int)
    for r in best_per_case.values():
        winner_counts[r['criterion']] += 1
    log("")
    log("Times each criterion is the sole-best for a case:")
    for name in CRITERION_NAMES:
        cnt = winner_counts.get(name, 0)
        bar = '#' * cnt
        log(f"  {name:>12}  {cnt:>2}  {bar}")

    # 3. Minimal criteria set: for each case, which criteria produce Q close
    #    enough to the best (within 1% and within 5%) to matter.
    log("")
    log("Criteria that produce Q within 1% of best-solo-Q for each case:")
    relevant_tight = defaultdict(set)   # crit_name -> set of cases it's 'near best' in
    relevant_loose = defaultdict(set)   # within 5%
    for case_name, cb in best_per_case.items():
        for r in rows:
            if r['case'] != case_name or r['criterion'] == '__baseline_all__':
                continue
            if cb['q'] <= 0:
                continue
            ratio = r['q'] / cb['q']
            if ratio <= 1.01:
                relevant_tight[r['criterion']].add(case_name)
            if ratio <= 1.05:
                relevant_loose[r['criterion']].add(case_name)

    log(f"  {'criterion':>12}  {'tight(≤1%)':>12}  {'loose(≤5%)':>12}")
    for name in CRITERION_NAMES:
        log(f"  {name:>12}  {len(relevant_tight[name]):>12}  "
            f"{len(relevant_loose[name]):>12}")

    # 4. Candidates to drop: criteria that are never 'near best' (tight)
    #    in ANY case. Dropping these leaves Q unchanged (within 1%) for every
    #    case in this benchmark.
    droppable_tight = [n for n in CRITERION_NAMES if not relevant_tight[n]]
    droppable_loose = [n for n in CRITERION_NAMES if not relevant_loose[n]]

    log("")
    log("Criteria candidates to drop:")
    log(f"  No case within 1% : {droppable_tight}")
    log(f"  No case within 5% : {droppable_loose}")

    # 5. Time savings estimate. Each criterion adds one pass over
    #    (depths × 2 start_rot × 2 orientations) in the whole-warehouse
    #    path. Regional path only uses CRITERIA[:3] internally, so dropping
    #    criteria with index ≥3 doesn't speed up regional. We estimate the
    #    fraction of total time the whole-warehouse passes consumed in
    #    baseline.
    total_whole_baseline = sum(b['t_whole'] for b in baseline_per_case.values())
    total_baseline = sum(b['t_total'] for b in baseline_per_case.values())
    per_crit_share = total_whole_baseline / len(ALL_CRITERIA) if ALL_CRITERIA else 0
    log("")
    log("Estimated time savings (whole-warehouse path scales linearly with N criteria):")
    log(f"  Baseline total time across all cases: {total_baseline:.2f}s")
    log(f"  Of which whole-warehouse:            {total_whole_baseline:.2f}s "
        f"(~{per_crit_share:.2f}s per criterion)")

    def estimate(drop_list):
        saved = per_crit_share * len(drop_list)
        return saved, (saved / total_baseline * 100.0) if total_baseline > 0 else 0.0

    s1, p1 = estimate(droppable_tight)
    s5, p5 = estimate(droppable_loose)
    log(f"  Drop tight-safe {droppable_tight}: saves ~{s1:.2f}s ({p1:.1f}%)")
    log(f"  Drop loose-safe {droppable_loose}: saves ~{s5:.2f}s ({p5:.1f}%)")

    log("")
    log(f"Total benchmark wall time: {grand_time:.1f}s")

    with open(log_path, 'w') as f:
        f.write('\n'.join(log_lines) + '\n')
    print(f"Wrote {log_path}")


if __name__ == '__main__':
    main()
