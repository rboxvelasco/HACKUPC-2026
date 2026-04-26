#!/usr/bin/env python3
"""
Master experiment runner — orchestrates every benchmark in the repo and
prints a top-level summary.

Stages (in order):
  1. run_all.py               — greedy solver over all cases
  2. compare_solvers.py       — greedy vs complex, same cases
  3. run_lns_all.py           — LNS+SA refinement of greedy outputs
  4. benchmark_criteria.py    — per-criterion breakdown (uses current
                                pruned CRITERIA list in greedy_solver.py)

Each stage streams its stdout/stderr live and also captures it to
traces/experiments/<stage>.log. A final summary reports wall time per
stage and any non-zero exit codes.

Usage:
    python3 run_all_experiments.py
    python3 run_all_experiments.py --skip lns,criteria     # skip by name
"""

import os
import sys
import time
import subprocess
from dataclasses import dataclass
from typing import List


@dataclass
class Stage:
    name: str           # short key, e.g. 'greedy'
    label: str          # human label
    cmd: List[str]


STAGES: List[Stage] = [
    Stage('greedy',   'Greedy over all cases (run_all.py)',
          [sys.executable, 'run_all.py', 'Cases', 'solutions']),
    Stage('compare',  'Greedy vs complex solver (compare_solvers.py)',
          [sys.executable, 'compare_solvers.py', 'Cases', 'solutions']),
    Stage('lns',      'LNS+SA refinement (run_lns_all.py)',
          [sys.executable, 'run_lns_all.py', 'Cases', 'solutions']),
    Stage('criteria', 'Per-criterion benchmark (benchmark_criteria.py)',
          [sys.executable, 'benchmark_criteria.py', 'Cases']),
]


def parse_skip_arg(argv: List[str]) -> set:
    skip = set()
    for i, a in enumerate(argv):
        if a == '--skip' and i + 1 < len(argv):
            skip.update(s.strip() for s in argv[i + 1].split(','))
    return skip


def run_stage(stage: Stage, log_dir: str) -> dict:
    log_path = os.path.join(log_dir, f'{stage.name}.log')

    header = (
        '\n' + '=' * 78 + '\n'
        f'▶  [{stage.name}] {stage.label}\n'
        f'   cmd: {" ".join(stage.cmd)}\n'
        f'   log: {log_path}\n'
        + '=' * 78 + '\n'
    )
    print(header, flush=True)

    start = time.time()
    exit_code = 0
    with open(log_path, 'w') as log_f:
        log_f.write(header)
        log_f.flush()
        # Stream both stdout and stderr live to terminal AND log file.
        proc = subprocess.Popen(
            stage.cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log_f.write(line)
        exit_code = proc.wait()
    elapsed = time.time() - start

    footer = (
        f'\n[{stage.name}] done in {elapsed:.2f}s  exit={exit_code}\n'
    )
    print(footer, flush=True)
    with open(log_path, 'a') as log_f:
        log_f.write(footer)

    return {
        'name': stage.name,
        'label': stage.label,
        'elapsed': elapsed,
        'exit_code': exit_code,
        'log': log_path,
    }


def main():
    skip = parse_skip_arg(sys.argv[1:])

    log_dir = os.path.join('traces', 'experiments')
    os.makedirs(log_dir, exist_ok=True)

    grand_start = time.time()
    results: List[dict] = []

    for stage in STAGES:
        if stage.name in skip:
            print(f'⏭  skipping stage "{stage.name}"')
            continue
        results.append(run_stage(stage, log_dir))

    grand_elapsed = time.time() - grand_start

    # ── Final summary ────────────────────────────────────────────────
    print('\n' + '═' * 78)
    print(' RUN-ALL EXPERIMENTS — SUMMARY')
    print('═' * 78)
    print(f' {"stage":<10} {"label":<50} {"time":>9}  exit')
    print(' ' + '─' * 76)
    any_failed = False
    for r in results:
        status = 'OK' if r['exit_code'] == 0 else f"FAIL({r['exit_code']})"
        if r['exit_code'] != 0:
            any_failed = True
        print(f" {r['name']:<10} {r['label']:<50} {r['elapsed']:>8.2f}s  {status}")
    print(' ' + '─' * 76)
    print(f' {"total":<10} {"":<50} {grand_elapsed:>8.2f}s')
    print('═' * 78)

    if any_failed:
        print('\nOne or more stages failed. See logs under traces/experiments/.')
        sys.exit(1)

    print('\nAll stages completed. Logs in traces/experiments/.')


if __name__ == '__main__':
    main()
