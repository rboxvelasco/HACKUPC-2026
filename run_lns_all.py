#!/usr/bin/env python3
"""Run LNS+SA on every case + solution pair and emit compare HTMLs.

Assumes `solutions/Case<N>.csv` already exists (greedy output from solver.py).
Writes:
    solutions/Case<N>_lns.csv       — LNS-refined placements
    solutions/Case<N>_compare.html  — side-by-side Greedy vs SA viewer
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from lns_sa import LNSConfig, run_lns_sa, validate_with_shapely, _write_solution
from solver import compute_score, parse_bay_types, parse_obstacles, parse_warehouse, usable_area
from lns_sa import _load_solution


CASES_DIR = 'Cases'
SOLUTIONS_DIR = 'solutions'
TIME_LIMIT = 3.0  # seconds per case


def main():
    cases_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(CASES_DIR)
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(SOLUTIONS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    case_dirs = sorted(p for p in cases_dir.iterdir() if p.is_dir())

    t0 = time.time()
    for cdir in case_dirs:
        name = cdir.name
        greedy_csv = out_dir / f'{name}.csv'
        lns_csv = out_dir / f'{name}_lns.csv'
        compare_html = out_dir / f'{name}_compare.html'

        if not greedy_csv.exists():
            print(f"[{name}] skip: {greedy_csv} not found (run run_all.py first)")
            continue

        print(f"\n{'='*60}\n[{name}] LNS+SA ({TIME_LIMIT:.1f}s)\n{'='*60}")

        # Starting score
        wh = parse_warehouse(str(cdir / 'warehouse.csv'))
        obs = parse_obstacles(str(cdir / 'obstacles.csv'))
        usable = usable_area(wh, obs)
        bts = parse_bay_types(str(cdir / 'types_of_bays.csv'))
        bt_map = {b.id: b for b in bts}
        start = _load_solution(str(greedy_csv), bt_map)
        start_q = compute_score(start, usable)
        print(f"    Greedy: {len(start)} bays, Q={start_q:.2f}")

        # Run LNS
        cfg = LNSConfig(time_limit=TIME_LIMIT, verbose=False)
        result = run_lns_sa(str(cdir), str(greedy_csv), cfg)
        print(f"    SA:     {len(result.best_placed)} bays, "
              f"Q={result.best_score:.2f}  (iter={result.iterations}, "
              f"improved={result.improved}, {result.elapsed:.2f}s)")

        # Validate
        ok, errs = validate_with_shapely(str(cdir), result.best_placed)
        if ok:
            print(f"    validate: OK")
        else:
            print(f"    validate: FAILED ({len(errs)} errors):")
            for e in errs[:5]:
                print(f"       - {e}")

        # Write LNS CSV
        _write_solution(result.best_placed, str(lns_csv))

        # Generate compare HTML via visualize.py CLI
        subprocess.run(
            [sys.executable, 'visualize.py',
             str(cdir), str(greedy_csv),
             '--compare', str(lns_csv),
             '--labels', 'Greedy,SA',
             '-o', str(compare_html)],
            check=True,
        )

    total = time.time() - t0
    print(f"\n{'='*60}\n  Done in {total:.2f}s\n{'='*60}")


if __name__ == '__main__':
    main()
