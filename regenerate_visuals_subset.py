#!/usr/bin/env python3
"""Regenerate greedy + LNS solutions and HTMLs for a subset of cases.

Scope: Cases 0, 1, 2, 3, 4_T_shape, 5_U_shape, 15_mega.

Per case:
  1. python3 greedy_solver.py Cases/<case> solutions/<case>.csv
  2. python3 lns_sa.py        Cases/<case> solutions/<case>.csv solutions/<case>_lns.csv
  3. python3 visualize.py     Cases/<case> solutions/<case>_lns.csv      -o solutions/<case>_lns.html
  4. python3 visualize.py     Cases/<case> solutions/<case>.csv \
                              --compare solutions/<case>_lns.csv \
                              --labels Greedy,SA \
                              -o solutions/<case>_compare.html
"""

import os
import subprocess
import sys
import time

CASES = [
    'Case0',
    'Case1',
    'Case2',
    'Case3',
    'Case4_T_shape',
    'Case5_U_shape',
    'Case15_mega',
]

CASES_ROOT = 'Cases'
OUT_ROOT = 'solutions'


def run(cmd):
    print('  $ ' + ' '.join(cmd), flush=True)
    t0 = time.time()
    subprocess.run(cmd, check=True)
    print(f'    ({time.time() - t0:.2f}s)')


def main():
    os.makedirs(OUT_ROOT, exist_ok=True)
    total_t = time.time()
    for name in CASES:
        case_dir = os.path.join(CASES_ROOT, name)
        greedy_csv = os.path.join(OUT_ROOT, f'{name}.csv')
        lns_csv = os.path.join(OUT_ROOT, f'{name}_lns.csv')
        lns_html = os.path.join(OUT_ROOT, f'{name}_lns.html')
        cmp_html = os.path.join(OUT_ROOT, f'{name}_compare.html')

        print(f'\n=== {name} ===')
        run([sys.executable, 'greedy_solver.py', case_dir, greedy_csv])
        run([sys.executable, 'lns_sa.py', case_dir, greedy_csv, lns_csv])
        run([sys.executable, 'visualize.py', case_dir, lns_csv, '-o', lns_html])
        run([sys.executable, 'visualize.py', case_dir, greedy_csv,
             '--compare', lns_csv, '--labels', 'Greedy,SA',
             '-o', cmp_html])

    print(f'\nAll done in {time.time() - total_t:.1f}s.')
    print('Outputs in', OUT_ROOT + '/')


if __name__ == '__main__':
    main()
