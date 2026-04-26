#!/usr/bin/env python3
"""Run greedy + LNS on a single case and open the comparison HTML.

The usual end-to-end loop when iterating on a specific warehouse:

    greedy_solver.py  Cases/<case>  solutions/<case>.csv
    lns_sa.py         Cases/<case>  solutions/<case>.csv  solutions/<case>_lns.csv
    visualize.py      Cases/<case>  solutions/<case>.csv  \\
                      --compare solutions/<case>_lns.csv  \\
                      --labels Greedy,SA                  \\
                      -o solutions/<case>_compare.html
    open solutions/<case>_compare.html

Usage:
    python3 run_case.py <case>
    python3 run_case.py Case1
    python3 run_case.py Cases/Case5_U_shape --lns-time 40
    python3 run_case.py Case12 --no-lns --no-open
    python3 run_case.py Case3 --skip-greedy     # reuse existing greedy csv

Flags:
    --lns-time N      seconds for LNS refinement (default 25)
    --no-lns          skip LNS, just greedy + single-solution HTML
    --skip-greedy     reuse solutions/<case>.csv if it already exists
    --no-open         do not launch a browser at the end
    -o, --out DIR     output directory (default: solutions)
"""

import argparse
import os
import subprocess
import sys
import time
import webbrowser


CASES_ROOT = 'Cases'
DEFAULT_OUT = 'solutions'


def resolve_case(arg: str) -> tuple[str, str]:
    """Return (case_dir, case_name) from a flexible user input.

    Accepts "Case1", "Cases/Case1", "./Cases/Case1", trailing slashes, etc.
    Raises SystemExit with a clear message if the path cannot be resolved.
    """
    # Drop any trailing slash
    arg = arg.rstrip('/\\')
    if os.path.isdir(arg):
        case_dir = arg
    else:
        # Treat as a bare case name under CASES_ROOT
        candidate = os.path.join(CASES_ROOT, arg)
        if not os.path.isdir(candidate):
            print(f"error: case not found: {arg}", file=sys.stderr)
            print(f"       tried '{arg}' and '{candidate}'", file=sys.stderr)
            available = [d for d in os.listdir(CASES_ROOT)
                         if os.path.isdir(os.path.join(CASES_ROOT, d))]
            print(f"       available under {CASES_ROOT}/: "
                  f"{', '.join(sorted(available))}", file=sys.stderr)
            raise SystemExit(2)
        case_dir = candidate
    return case_dir, os.path.basename(case_dir)


def run(cmd: list[str], label: str) -> None:
    print(f"\n▶ {label}")
    print(f"  $ {' '.join(cmd)}")
    t0 = time.time()
    subprocess.run(cmd, check=True)
    print(f"  done in {time.time() - t0:.2f}s")


def main() -> None:
    ap = argparse.ArgumentParser(
        description='End-to-end run of greedy + LNS on one case + visualiser.',
    )
    ap.add_argument('case', help='case name (Case1) or path (Cases/Case1)')
    ap.add_argument('-o', '--out', default=DEFAULT_OUT,
                    help=f'output directory (default: {DEFAULT_OUT})')
    ap.add_argument('--lns-time', type=float, default=25.0,
                    help='seconds for LNS refinement (default: 25)')
    ap.add_argument('--no-lns', action='store_true',
                    help='skip LNS refinement, visualise greedy only')
    ap.add_argument('--skip-greedy', action='store_true',
                    help='reuse solutions/<case>.csv if it already exists')
    ap.add_argument('--no-open', action='store_true',
                    help='do not open the browser when done')
    args = ap.parse_args()

    case_dir, name = resolve_case(args.case)
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    greedy_csv = os.path.join(out_dir, f'{name}.csv')
    lns_csv    = os.path.join(out_dir, f'{name}_lns.csv')
    html_out   = os.path.join(out_dir, f'{name}_compare.html')
    html_solo  = os.path.join(out_dir, f'{name}.html')

    py = sys.executable
    t_total = time.time()

    # 1. Greedy
    if args.skip_greedy and os.path.exists(greedy_csv) and os.path.getsize(greedy_csv) > 0:
        print(f"▶ skipping greedy (reusing {greedy_csv})")
    else:
        run([py, 'greedy_solver.py', case_dir, greedy_csv],
            label=f'greedy → {greedy_csv}')

    # 2. LNS (optional)
    if args.no_lns:
        html_target = html_solo
        run([py, 'visualize.py', case_dir, greedy_csv, '-o', html_target],
            label=f'visualize (greedy only) → {html_target}')
    else:
        if args.lns_time != 25.0:
            print(f"\nnote: --lns-time {args.lns_time} requested, but lns_sa.py's"
                  " CLI currently uses a hard-coded 25s budget. Ignoring the"
                  " flag and running with 25s. Edit LNSConfig(time_limit=...)"
                  " in lns_sa.py's __main__ block if you need a different budget.")
        run([py, 'lns_sa.py', case_dir, greedy_csv, lns_csv],
            label=f'LNS+SA → {lns_csv}')

        html_target = html_out
        run([py, 'visualize.py', case_dir, greedy_csv,
             '--compare', lns_csv,
             '--labels', 'Greedy,SA',
             '-o', html_target],
            label=f'visualize (compare) → {html_target}')

    print(f"\n✓ All done in {time.time() - t_total:.2f}s")
    print(f"  {html_target}")

    if not args.no_open:
        abs_path = os.path.abspath(html_target)
        print(f"  opening {abs_path}")
        webbrowser.open('file://' + abs_path)


if __name__ == '__main__':
    main()
