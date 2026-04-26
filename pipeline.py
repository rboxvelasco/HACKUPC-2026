#!/usr/bin/env python3
"""
End-to-end pipeline: Greedy → LNS+SA → compare HTML.

For each input case this runs, in order:

    1. greedy_solver.py <case> <out>/<case>.csv
    2. lns_sa.py        <case> <out>/<case>.csv  <out>/<case>_lns.csv
    3. visualize.py     <case> <out>/<case>.csv \
                                --compare <out>/<case>_lns.csv \
                                --labels Greedy,SA \
                                -o <out>/<case>_compare.html

Each case is independent: one failure does not abort the rest. A summary
line per case is printed with greedy Q, LNS Q, Δ, and wall times.

Usage
-----
    python3 pipeline.py Cases/Case1                         # one case
    python3 pipeline.py Cases/Case1 Cases/Case2             # several
    python3 pipeline.py --all                               # every dir under Cases/
    python3 pipeline.py --all --lns-time 60                 # longer SA budget
    python3 pipeline.py --all --out-dir solutions_run2      # custom output dir
    python3 pipeline.py Cases/Case1 --skip-greedy           # reuse existing greedy CSV
    python3 pipeline.py Cases/Case1 --no-open               # skip browser launch
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from typing import List, Optional

from solver import (
    compute_score, parse_bay_types, parse_obstacles, parse_warehouse,
    usable_area,
)
from lns_sa import _load_solution  # small helper to read a solution CSV


# ──────────────────────────────────────────────────────────────────────
# Per-case dataclass & helpers
# ──────────────────────────────────────────────────────────────────────

@dataclass
class Stage:
    """Outcome of a single pipeline stage for one case."""
    ok: bool = True
    elapsed: float = 0.0
    message: str = ''


@dataclass
class CaseResult:
    case_name: str
    case_dir: str
    greedy_csv: str
    lns_csv: str
    compare_html: str
    greedy: Stage
    lns: Stage
    visual: Stage
    greedy_q: Optional[float] = None
    lns_q: Optional[float] = None

    @property
    def total_time(self) -> float:
        return self.greedy.elapsed + self.lns.elapsed + self.visual.elapsed

    @property
    def success(self) -> bool:
        return self.greedy.ok and self.lns.ok and self.visual.ok

    @property
    def delta_q(self) -> Optional[float]:
        if self.greedy_q is None or self.lns_q is None:
            return None
        return self.lns_q - self.greedy_q


def _run(cmd: List[str]) -> Stage:
    t0 = time.time()
    try:
        result = subprocess.run(cmd, check=False)
        elapsed = time.time() - t0
        if result.returncode == 0:
            return Stage(ok=True, elapsed=elapsed)
        return Stage(ok=False, elapsed=elapsed,
                     message=f'exit code {result.returncode}')
    except Exception as e:
        return Stage(ok=False, elapsed=time.time() - t0,
                     message=f'{type(e).__name__}: {e}')


def _score_csv(case_dir: str, csv_path: str) -> Optional[float]:
    """Load a solution CSV and return its Q score (None if file missing/empty)."""
    if not (os.path.exists(csv_path) and os.path.getsize(csv_path) > 0):
        return None
    try:
        bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
        warehouse = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
        obstacles = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
        ua = usable_area(warehouse, obstacles)
        placed = _load_solution(csv_path, {b.id: b for b in bay_types})
        return compute_score(placed, ua)
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────
# Pipeline for one case
# ──────────────────────────────────────────────────────────────────────

def run_case(case_dir: str, out_dir: str, lns_time: float,
             skip_greedy: bool) -> CaseResult:
    case_name = os.path.basename(case_dir.rstrip('/'))
    greedy_csv = os.path.join(out_dir, f'{case_name}.csv')
    lns_csv = os.path.join(out_dir, f'{case_name}_lns.csv')
    compare_html = os.path.join(out_dir, f'{case_name}_compare.html')

    # Stage 1: greedy
    if skip_greedy and os.path.exists(greedy_csv) and os.path.getsize(greedy_csv) > 0:
        greedy_stage = Stage(ok=True, elapsed=0.0, message='reused existing CSV')
    else:
        greedy_stage = _run(
            [sys.executable, 'greedy_solver.py', case_dir, greedy_csv]
        )
    greedy_q = _score_csv(case_dir, greedy_csv) if greedy_stage.ok else None

    # Stage 2: LNS+SA (in-process so we can pass a per-case time_limit)
    if not greedy_stage.ok:
        lns_stage = Stage(ok=False, message='skipped: greedy failed')
        lns_q = None
    else:
        lns_stage = _run_lns_inproc(case_dir, greedy_csv, lns_csv, lns_time)
        lns_q = _score_csv(case_dir, lns_csv) if lns_stage.ok else None

    # Stage 3: compare HTML (needs both CSVs)
    if lns_stage.ok and os.path.exists(greedy_csv) and os.path.exists(lns_csv):
        visual_stage = _run([
            sys.executable, 'visualize.py', case_dir, greedy_csv,
            '--compare', lns_csv,
            '--labels', 'Greedy,SA',
            '-o', compare_html,
        ])
    else:
        visual_stage = Stage(ok=False, message='skipped: prior stage failed')

    return CaseResult(
        case_name=case_name,
        case_dir=case_dir,
        greedy_csv=greedy_csv,
        lns_csv=lns_csv,
        compare_html=compare_html,
        greedy=greedy_stage,
        lns=lns_stage,
        visual=visual_stage,
        greedy_q=greedy_q,
        lns_q=lns_q,
    )


def _run_lns_inproc(case_dir: str, in_csv: str, out_csv: str,
                    time_limit: float) -> Stage:
    """Run LNS+SA in-process so we can pass time_limit directly.

    Falls back to subprocess if the module import fails for any reason.
    """
    t0 = time.time()
    try:
        import lns_sa
        cfg = lns_sa.LNSConfig(time_limit=time_limit, verbose=False)
        result = lns_sa.run_lns_sa(case_dir, in_csv, cfg)
        lns_sa._write_solution(result.best_placed, out_csv)
        return Stage(ok=True, elapsed=time.time() - t0)
    except Exception as e:
        return Stage(ok=False, elapsed=time.time() - t0,
                     message=f'{type(e).__name__}: {e}')


# ──────────────────────────────────────────────────────────────────────
# CLI & summary
# ──────────────────────────────────────────────────────────────────────

def _discover_all_cases(cases_root: str) -> List[str]:
    if not os.path.isdir(cases_root):
        return []
    return sorted(
        os.path.join(cases_root, d)
        for d in os.listdir(cases_root)
        if os.path.isdir(os.path.join(cases_root, d))
    )


def _print_summary(results: List[CaseResult]) -> None:
    print()
    print('=' * 96)
    print(' PIPELINE SUMMARY')
    print('=' * 96)
    print(f" {'case':<26}{'greedy_Q':>12}{'lns_Q':>12}{'ΔQ':>10}"
          f"{'greedy_t':>10}{'lns_t':>8}{'viz_t':>8}{'status':>10}")
    print(' ' + '─' * 94)

    for r in results:
        q_g = f"{r.greedy_q:.2f}" if r.greedy_q is not None else '—'
        q_l = f"{r.lns_q:.2f}" if r.lns_q is not None else '—'
        dq = f"{r.delta_q:+.2f}" if r.delta_q is not None else '—'
        status = 'OK' if r.success else 'FAIL'
        bad_stage = ''
        if not r.success:
            for name, st in (('greedy', r.greedy), ('lns', r.lns), ('viz', r.visual)):
                if not st.ok:
                    bad_stage = f' ({name}: {st.message})'
                    break
        print(f" {r.case_name:<26}{q_g:>12}{q_l:>12}{dq:>10}"
              f"{r.greedy.elapsed:>9.2f}s{r.lns.elapsed:>7.2f}s"
              f"{r.visual.elapsed:>7.2f}s{status:>10}{bad_stage}")

    total = sum(r.total_time for r in results)
    ok = sum(1 for r in results if r.success)
    print(' ' + '─' * 94)
    print(f" {ok}/{len(results)} cases OK · total wall time {total:.1f}s")
    print('=' * 96)


def main():
    parser = argparse.ArgumentParser(
        description='Run Greedy → LNS+SA → compare HTML for one or more cases.',
    )
    parser.add_argument('cases', nargs='*',
                        help='One or more case directories (e.g. Cases/Case1).')
    parser.add_argument('--all', action='store_true',
                        help='Run every case directory under --cases-root.')
    parser.add_argument('--cases-root', default='Cases',
                        help='Root directory scanned when --all is used.')
    parser.add_argument('--out-dir', default='solutions',
                        help='Output directory for CSVs and HTMLs (default: solutions).')
    parser.add_argument('--lns-time', type=float, default=30.0,
                        help='Time budget for LNS+SA per case, in seconds '
                             '(default: 30.0).')
    parser.add_argument('--skip-greedy', action='store_true',
                        help='Reuse an existing greedy CSV in --out-dir if '
                             'one is present for the case.')
    parser.add_argument('--no-open', action='store_true',
                        help='Do not open the compare HTMLs in the default '
                             'browser after the pipeline finishes.')

    args = parser.parse_args()

    if args.all:
        case_dirs = _discover_all_cases(args.cases_root)
        if not case_dirs:
            print(f'ERROR: no case directories under {args.cases_root}/',
                  file=sys.stderr)
            sys.exit(2)
    else:
        if not args.cases:
            parser.print_help(sys.stderr)
            sys.exit(2)
        case_dirs = args.cases

    # Validate each path exists
    missing = [c for c in case_dirs if not os.path.isdir(c)]
    if missing:
        for c in missing:
            print(f'ERROR: not a directory: {c}', file=sys.stderr)
        sys.exit(2)

    os.makedirs(args.out_dir, exist_ok=True)

    results: List[CaseResult] = []
    for cdir in case_dirs:
        name = os.path.basename(cdir.rstrip('/'))
        print(f'\n▶ {name}')
        r = run_case(cdir, args.out_dir, args.lns_time, args.skip_greedy)
        # Per-stage outcome summary (subprocess stdout already streamed live).
        for label, st in (('greedy', r.greedy), ('lns   ', r.lns),
                          ('viz   ', r.visual)):
            mark = '✓' if st.ok else '✗'
            extra = f'  [{st.message}]' if st.message else ''
            print(f'  {label} {mark}  {st.elapsed:>6.2f}s{extra}')
        if r.greedy_q is not None and r.lns_q is not None:
            print(f'  Q: greedy={r.greedy_q:.2f}  lns={r.lns_q:.2f}  '
                  f'Δ={r.lns_q - r.greedy_q:+.2f}')
        results.append(r)

    _print_summary(results)

    # Open compare HTMLs in the browser (one per successful case)
    if not args.no_open:
        to_open = [r.compare_html for r in results
                   if r.visual.ok and os.path.exists(r.compare_html)]
        if to_open:
            print(f'\nOpening {len(to_open)} compare HTML(s) in your browser…')
            for path in to_open:
                # file:// URI required on most platforms for webbrowser.open
                uri = 'file://' + os.path.abspath(path)
                try:
                    webbrowser.open(uri, new=2)
                except Exception as e:
                    print(f'  ! could not open {path}: {e}', file=sys.stderr)

    # Exit non-zero if any case failed
    sys.exit(0 if all(r.success for r in results) else 1)


if __name__ == '__main__':
    main()
