#!/usr/bin/env python3
"""
Profile LNS+SA to find optimization targets.

Two-pass approach:
  1. Coarse: for each case, time the three phases (destroy / repair /
     accept+score) by instrumenting run_lns_sa via a monkey-patched
     wrapper. Also count iterations and FFT calls.
  2. Fine:   run cProfile over the whole LNS execution on every case,
     accumulate stats into a single pstats dump, and print the top
     functions by cumulative and internal time.

Outputs:
    traces/lns_profile.pstats       — binary pstats dump (load with
                                      `python -m pstats traces/lns_profile.pstats`
                                      or snakeviz)
    traces/lns_profile.log          — human-readable summary
    stdout                          — same summary

Usage:
    python3 benchmark_profile_lns.py [cases_dir]
"""

from __future__ import annotations

import cProfile
import io
import os
import pstats
import sys
import time
from dataclasses import dataclass
from typing import Dict, List

import numpy as np
from scipy.signal import fftconvolve

# Import lazily so monkey-patching takes effect before we call into the module.
import lns_sa
from solver import (
    compute_score, parse_bay_types, parse_obstacles, parse_warehouse, usable_area,
)


LNS_TIME = 25.0


# ─────────────────────────────────────────────
# Coarse instrumentation via monkey-patch
# ─────────────────────────────────────────────

@dataclass
class PhaseStats:
    t_destroy: float = 0.0
    t_repair_candidates: float = 0.0
    t_repair_greedy: float = 0.0
    t_score: float = 0.0
    n_iterations: int = 0
    n_fft_calls: int = 0
    n_accepted: int = 0
    n_improved: int = 0
    n_rejected_empty_destroy: int = 0
    elapsed: float = 0.0


def _instrument_case(case_dir: str, solution_csv: str, time_limit: float) -> PhaseStats:
    """Run LNS+SA with simple timing wrappers around destroy / repair / score."""
    stats = PhaseStats()

    orig_destroy = lns_sa.destroy_low_density
    orig_gen_cands = lns_sa.generate_candidates
    orig_greedy_repair = lns_sa.greedy_repair
    orig_compute_score = lns_sa.compute_score
    orig_fftconvolve = lns_sa.fftconvolve

    def timed_destroy(*args, **kwargs):
        t0 = time.perf_counter()
        out = orig_destroy(*args, **kwargs)
        stats.t_destroy += time.perf_counter() - t0
        stats.n_iterations += 1
        if not out[1]:
            stats.n_rejected_empty_destroy += 1
        return out

    def timed_gen_cands(*args, **kwargs):
        t0 = time.perf_counter()
        out = orig_gen_cands(*args, **kwargs)
        stats.t_repair_candidates += time.perf_counter() - t0
        return out

    def timed_greedy_repair(*args, **kwargs):
        t0 = time.perf_counter()
        out = orig_greedy_repair(*args, **kwargs)
        stats.t_repair_greedy += time.perf_counter() - t0
        return out

    def timed_compute_score(*args, **kwargs):
        t0 = time.perf_counter()
        out = orig_compute_score(*args, **kwargs)
        stats.t_score += time.perf_counter() - t0
        return out

    def counted_fft(*args, **kwargs):
        stats.n_fft_calls += 1
        return orig_fftconvolve(*args, **kwargs)

    lns_sa.destroy_low_density = timed_destroy
    lns_sa.generate_candidates = timed_gen_cands
    lns_sa.greedy_repair = timed_greedy_repair
    lns_sa.compute_score = timed_compute_score
    lns_sa.fftconvolve = counted_fft

    try:
        cfg = lns_sa.LNSConfig(time_limit=time_limit, verbose=False)
        t0 = time.perf_counter()
        result = lns_sa.run_lns_sa(case_dir, solution_csv, cfg)
        stats.elapsed = time.perf_counter() - t0
        stats.n_accepted = result.accepted
        stats.n_improved = result.improved
    finally:
        lns_sa.destroy_low_density = orig_destroy
        lns_sa.generate_candidates = orig_gen_cands
        lns_sa.greedy_repair = orig_greedy_repair
        lns_sa.compute_score = orig_compute_score
        lns_sa.fftconvolve = orig_fftconvolve

    return stats


# ─────────────────────────────────────────────
# Greedy output fabrication
# ─────────────────────────────────────────────

def _ensure_greedy_solution(case_dir: str, out_dir: str) -> str:
    """Return path to the greedy solution CSV for this case, generating it
    if missing. Uses greedy_solver's full pipeline."""
    case_name = os.path.basename(case_dir.rstrip('/'))
    target = os.path.join(out_dir, f'{case_name}.csv')
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return target
    os.makedirs(out_dir, exist_ok=True)
    import subprocess
    subprocess.run(
        [sys.executable, 'greedy_solver.py', case_dir, target],
        check=True, stdout=subprocess.DEVNULL,
    )
    return target


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def _fmt_s(s: float) -> str:
    return f"{s:>7.2f}s"


def _summarize_phases(per_case: Dict[str, PhaseStats], log: list) -> None:
    def add(msg: str = ''):
        print(msg)
        log.append(msg)

    add('')
    add('─' * 100)
    add('PHASE TIMING (seconds, instrumented)')
    add('─' * 100)
    add(f"{'case':<26}{'elapsed':>9}{'destroy':>9}"
        f"{'cand_gen':>10}{'greedy_rep':>11}{'score':>9}"
        f"{'iters':>8}{'fft':>7}{'acc':>6}{'imp':>6}")
    add('─' * 100)

    totals = PhaseStats()
    for case_name, s in per_case.items():
        add(f"{case_name:<26}{_fmt_s(s.elapsed)}{_fmt_s(s.t_destroy)}"
            f"{_fmt_s(s.t_repair_candidates)}{_fmt_s(s.t_repair_greedy)}"
            f"{_fmt_s(s.t_score)}"
            f"{s.n_iterations:>8}{s.n_fft_calls:>7}{s.n_accepted:>6}{s.n_improved:>6}")
        totals.elapsed += s.elapsed
        totals.t_destroy += s.t_destroy
        totals.t_repair_candidates += s.t_repair_candidates
        totals.t_repair_greedy += s.t_repair_greedy
        totals.t_score += s.t_score
        totals.n_iterations += s.n_iterations
        totals.n_fft_calls += s.n_fft_calls
        totals.n_accepted += s.n_accepted
        totals.n_improved += s.n_improved

    add('─' * 100)
    add(f"{'TOTAL':<26}{_fmt_s(totals.elapsed)}{_fmt_s(totals.t_destroy)}"
        f"{_fmt_s(totals.t_repair_candidates)}{_fmt_s(totals.t_repair_greedy)}"
        f"{_fmt_s(totals.t_score)}"
        f"{totals.n_iterations:>8}{totals.n_fft_calls:>7}"
        f"{totals.n_accepted:>6}{totals.n_improved:>6}")

    # Percent breakdown
    if totals.elapsed > 0:
        pct = lambda x: 100.0 * x / totals.elapsed
        add('')
        add(f"  destroy       : {pct(totals.t_destroy):>5.1f}%")
        add(f"  cand_gen (FFT): {pct(totals.t_repair_candidates):>5.1f}%")
        add(f"  greedy_repair : {pct(totals.t_repair_greedy):>5.1f}%")
        add(f"  Q scoring     : {pct(totals.t_score):>5.1f}%")
        other = totals.elapsed - (totals.t_destroy + totals.t_repair_candidates
                                  + totals.t_repair_greedy + totals.t_score)
        add(f"  other/overhead: {pct(other):>5.1f}%")

    if totals.n_iterations > 0:
        add('')
        add(f"  mean FFTs per iteration: "
            f"{totals.n_fft_calls / totals.n_iterations:.1f}")
        add(f"  mean time per iteration: "
            f"{totals.elapsed / totals.n_iterations * 1000:.1f} ms")
        add(f"  improvement rate       : "
            f"{100.0 * totals.n_improved / totals.n_iterations:.1f}% "
            f"of iterations produced a new best")


def _summarize_cprofile(profile_stats_path: str, log: list) -> None:
    def add(msg: str = ''):
        print(msg)
        log.append(msg)

    add('')
    add('─' * 100)
    add('cProfile — cumulative time in lns_sa.py functions (top 20)')
    add('─' * 100)

    buf = io.StringIO()
    p = pstats.Stats(profile_stats_path, stream=buf)
    p.strip_dirs()
    p.sort_stats('cumulative')
    p.print_stats('lns_sa', 20)
    text = buf.getvalue()
    for line in text.splitlines():
        add(line)

    add('')
    add('─' * 100)
    add('cProfile — internal (tottime) — top 25 overall (any module)')
    add('─' * 100)
    buf2 = io.StringIO()
    p2 = pstats.Stats(profile_stats_path, stream=buf2)
    p2.strip_dirs()
    p2.sort_stats('tottime')
    p2.print_stats(25)
    for line in buf2.getvalue().splitlines():
        add(line)

    add('')
    add('─' * 100)
    add('cProfile — cumulative time in FFT path')
    add('─' * 100)
    buf3 = io.StringIO()
    p3 = pstats.Stats(profile_stats_path, stream=buf3)
    p3.strip_dirs()
    p3.sort_stats('cumulative')
    # scipy fft lives in scipy.signal._signaltools; filter by name fragments
    p3.print_stats('fftconvolve|pocketfft|signaltools|_convolve_forbidden', 20)
    for line in buf3.getvalue().splitlines():
        add(line)


def main():
    cases_dir = sys.argv[1] if len(sys.argv) > 1 else 'Cases'
    greedy_out = 'solutions'
    os.makedirs('traces', exist_ok=True)

    pstats_path = os.path.join('traces', 'lns_profile.pstats')
    log_path = os.path.join('traces', 'lns_profile.log')

    case_dirs = sorted(
        os.path.join(cases_dir, d)
        for d in os.listdir(cases_dir)
        if os.path.isdir(os.path.join(cases_dir, d))
    )

    log_lines: List[str] = []

    def log(msg: str = ''):
        print(msg)
        log_lines.append(msg)

    log('=== LNS+SA profiling ===')
    log(f'LNS budget per case: {LNS_TIME}s')
    log(f'Cases:               {len(case_dirs)}')
    log('')

    # Ensure greedy solutions exist
    for cdir in case_dirs:
        _ensure_greedy_solution(cdir, greedy_out)

    # ── Pass 1: instrumented phase timing ─────────────────────────
    per_case: Dict[str, PhaseStats] = {}
    log('Pass 1 — instrumented phase timing')
    for cdir in case_dirs:
        name = os.path.basename(cdir)
        sol = os.path.join(greedy_out, f'{name}.csv')
        log(f'  [{name}] running ...')
        s = _instrument_case(cdir, sol, LNS_TIME)
        per_case[name] = s
        log(f'    elapsed={s.elapsed:.2f}s  iters={s.n_iterations}  '
            f'fft={s.n_fft_calls}  acc={s.n_accepted}  imp={s.n_improved}')

    _summarize_phases(per_case, log_lines)

    # ── Pass 2: cProfile across all cases ─────────────────────────
    log('')
    log('Pass 2 — cProfile accumulation over all cases')
    profiler = cProfile.Profile()
    profiler.enable()
    for cdir in case_dirs:
        name = os.path.basename(cdir)
        sol = os.path.join(greedy_out, f'{name}.csv')
        cfg = lns_sa.LNSConfig(time_limit=LNS_TIME, verbose=False)
        lns_sa.run_lns_sa(cdir, sol, cfg)
    profiler.disable()
    profiler.dump_stats(pstats_path)
    log(f'  wrote {pstats_path}')

    _summarize_cprofile(pstats_path, log_lines)

    log('')
    log('Tip: interactive exploration → snakeviz traces/lns_profile.pstats')
    log(f'     or: python -m pstats {pstats_path}')

    with open(log_path, 'w') as f:
        f.write('\n'.join(log_lines) + '\n')
    print(f"Wrote {log_path}")


if __name__ == '__main__':
    main()
