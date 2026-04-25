#!/usr/bin/env python3
"""Run both solvers on all cases and compare Q / coverage / time.

Usage: python3 compare_solvers.py [cases_dir] [output_dir]
"""

import sys
import os
import time
import subprocess
from typing import Optional, Tuple


SOLVERS = [
    ('complex', 'solver.py'),
    ('greedy ', 'greedy_solver.py'),
]


def run_solver(script: str, case_dir: str, out_file: str) -> Tuple[Optional[float], Optional[float], Optional[int], float]:
    """Run a solver script, return (Q, coverage, n_bays, elapsed)."""
    start = time.time()
    result = subprocess.run(
        ['python3', script, case_dir, out_file],
        capture_output=True, text=True,
    )
    elapsed = time.time() - start

    # Parse output from the final summary line
    q_val: Optional[float] = None
    cov_val: Optional[float] = None
    n_bays: Optional[int] = None
    for line in result.stdout.splitlines():
        # e.g. "  solution.csv: 17 bays, cov=68.8%, Q=1951.24, 2.72s"
        if 'bays,' in line and 'Q=' in line and 'cov=' in line:
            try:
                n_bays_s = line.split('bays,')[0].split()[-1]
                cov_s = line.split('cov=')[1].split('%')[0]
                q_s = line.split('Q=')[1].split(',')[0]
                n_bays = int(n_bays_s)
                cov_val = float(cov_s) / 100.0
                q_val = float(q_s)
            except (ValueError, IndexError):
                pass
    return q_val, cov_val, n_bays, elapsed


def main():
    cases_dir = sys.argv[1] if len(sys.argv) > 1 else 'Cases'
    output_dir = sys.argv[2] if len(sys.argv) > 2 else 'solutions'
    os.makedirs(output_dir, exist_ok=True)

    case_dirs = sorted([
        os.path.join(cases_dir, d)
        for d in os.listdir(cases_dir)
        if os.path.isdir(os.path.join(cases_dir, d))
    ])

    # rows[case] = {solver_name: (q, cov, n, t)}
    results: dict = {}
    totals = {name: [0.0, 0.0] for name, _ in SOLVERS}  # [total_q, total_time]

    for case_dir in case_dirs:
        case_name = os.path.basename(case_dir)
        results[case_name] = {}
        for name, script in SOLVERS:
            out_file = os.path.join(output_dir, f'{case_name}_{name.strip()}.csv')
            q, cov, n, t = run_solver(script, case_dir, out_file)
            results[case_name][name] = (q, cov, n, t)
            if q is not None:
                totals[name][0] += q
            totals[name][1] += t

    # Print comparison table
    print()
    print('=' * 88)
    print(f"{'Case':<10} {'Solver':<10} {'N':>4} {'Cov':>7} {'Q':>12} {'Time':>8}   {'ΔQ vs best':>14}")
    print('-' * 88)
    for case_name in sorted(results.keys()):
        row = results[case_name]
        # Determine best Q in this row (smaller is better)
        valid_qs = [(name, v[0]) for name, v in row.items() if v[0] is not None]
        best_q = min(q for _, q in valid_qs) if valid_qs else None
        for name, _ in SOLVERS:
            q, cov, n, t = row[name]
            q_s = f"{q:>12.2f}" if q is not None else f"{'—':>12}"
            cov_s = f"{cov:>6.1%}" if cov is not None else f"{'—':>7}"
            n_s = f"{n:>4d}" if n is not None else f"{'—':>4}"
            t_s = f"{t:>7.2f}s"
            if q is not None and best_q is not None and best_q > 0:
                delta_pct = (q - best_q) / best_q * 100
                delta_s = f"{delta_pct:>+13.1f}%" if delta_pct > 0.01 else f"{'★ best':>14}"
            else:
                delta_s = f"{'—':>14}"
            print(f"{case_name:<10} {name:<10} {n_s} {cov_s} {q_s} {t_s}   {delta_s}")
        print()

    print('-' * 88)
    for name, _ in SOLVERS:
        tot_q, tot_t = totals[name]
        print(f"{'TOTAL':<10} {name:<10} {'':>4} {'':>7} Σ={tot_q:>10.2f} {tot_t:>7.2f}s")
    print('=' * 88)


if __name__ == '__main__':
    main()
