#!/usr/bin/env python3
"""
Benchmark repair-loop optimisations applied so far:
  1. gap_only_mask precomputed in BayKernel (no per-candidate .copy())
  2. rng.shuffle(candidates) + sort by attrgetter('score')
     (no 145M rng.random() calls, no per-call key lambda)

Runs LNS+SA on Cases 0-5 + Case15_mega with a 25s budget and compares
against the baseline profiling numbers captured in
`traces/lns_profile.log` (pre-optimisation).
"""

import cProfile
import io
import os
import pstats
import sys
import time

import lns_sa


LNS_TIME = 25.0
CASE_NAMES = ['Case0', 'Case1', 'Case2', 'Case3', 'Case4_T_shape',
              'Case5_U_shape', 'Case15_mega']

# Reference: pre-optimisation iteration counts and repair-loop time taken
# from traces/lns_profile.log. Used to estimate the speedup of each
# optimisation. These ARE valid to compare across greedy-seed changes
# because they measure pace-of-work (iterations / repair-time), not Q.
BASELINE_PACE = {
    'Case0':         {'iters': 80, 't_repair': 14.54},
    'Case1':         {'iters': 30, 't_repair': 15.43},
    'Case2':         {'iters': 64, 't_repair': 15.24},
    'Case3':         {'iters': 14, 't_repair': 15.72},
    'Case4_T_shape': {'iters': 38, 't_repair': 14.93},
    'Case5_U_shape': {'iters': 52, 't_repair': 14.60},
    'Case15_mega':   {'iters':  1, 't_repair': 40.59},
}


def ensure_greedy(case_dir, out_dir):
    """Regenerate the greedy seed CSV for this case.

    We always regenerate (rather than re-use whatever is on disk) because
    old `solutions/<case>.csv` files can lag behind recent changes to
    greedy_solver.py, producing misleading "ΔQ vs baseline" numbers.
    """
    name = os.path.basename(case_dir.rstrip('/'))
    path = os.path.join(out_dir, f'{name}.csv')
    os.makedirs(out_dir, exist_ok=True)
    import subprocess
    subprocess.run([sys.executable, 'greedy_solver.py', case_dir, path],
                   check=True, stdout=subprocess.DEVNULL)
    return path


def _instrumented_run(case_dir, solution, time_limit):
    """Run LNS+SA + measure time spent inside greedy_repair."""
    t_repair = [0.0]
    orig = lns_sa.greedy_repair

    def timed(*a, **kw):
        t0 = time.perf_counter()
        out = orig(*a, **kw)
        t_repair[0] += time.perf_counter() - t0
        return out

    lns_sa.greedy_repair = timed
    try:
        cfg = lns_sa.LNSConfig(time_limit=time_limit, verbose=False)
        t0 = time.perf_counter()
        r = lns_sa.run_lns_sa(case_dir, solution, cfg)
        elapsed = time.perf_counter() - t0
    finally:
        lns_sa.greedy_repair = orig
    return r, elapsed, t_repair[0]


def main():
    cases_dir = 'Cases'
    greedy_out = 'solutions'
    os.makedirs('traces', exist_ok=True)

    print(f"=== Repair optimisation benchmark ===")
    print(f"  Changes under test:")
    print(f"    1. gap_only_mask precomputed in BayKernel")
    print(f"    2. rng.shuffle(candidates) + sort(attrgetter('score'), reverse=True)")
    print(f"  Cases:  {', '.join(CASE_NAMES)}")
    print(f"  LNS:    {LNS_TIME}s per case")
    print(f"  Greedy seeds are regenerated at the start of the run.")
    print()
    print(f"{'case':<18}{'elapsed':>10}{'iters':>8}{'it_×':>7}"
          f"{'t_repair':>11}{'t_rep_×':>9}{'greedy_Q':>12}{'LNS_Q':>12}{'ΔQ':>10}")
    print('-' * 97)

    totals = {'elapsed': 0.0, 'iters': 0, 't_repair': 0.0,
              'base_iters': 0, 'base_t_repair': 0.0,
              'Q_sum': 0.0, 'greedy_Q_sum': 0.0}

    from solver import (parse_bay_types, parse_warehouse, parse_obstacles,
                        compute_score, usable_area)
    from lns_sa import _load_solution

    for name in CASE_NAMES:
        cdir = os.path.join(cases_dir, name)
        sol = ensure_greedy(cdir, greedy_out)

        # Greedy seed Q (recomputed from the CSV we just wrote)
        bts = parse_bay_types(os.path.join(cdir, 'types_of_bays.csv'))
        wh = parse_warehouse(os.path.join(cdir, 'warehouse.csv'))
        obs = parse_obstacles(os.path.join(cdir, 'obstacles.csv'))
        ua = usable_area(wh, obs)
        placed = _load_solution(sol, {b.id: b for b in bts})
        greedy_Q = compute_score(placed, ua)

        r, elapsed, t_repair = _instrumented_run(cdir, sol, LNS_TIME)

        base = BASELINE_PACE[name]
        it_ratio = (r.iterations / base['iters']) if base['iters'] else float('nan')
        rep_ratio = (base['t_repair'] / t_repair) if t_repair > 0 else float('nan')
        dQ = r.best_score - greedy_Q

        print(f"{name:<18}{elapsed:>9.2f}s{r.iterations:>8}{it_ratio:>6.1f}x"
              f"{t_repair:>10.2f}s{rep_ratio:>8.2f}x"
              f"{greedy_Q:>12.2f}{r.best_score:>12.2f}{dQ:>+10.2f}")

        totals['elapsed'] += elapsed
        totals['iters'] += r.iterations
        totals['t_repair'] += t_repair
        totals['base_iters'] += base['iters']
        totals['base_t_repair'] += base['t_repair']
        totals['Q_sum'] += r.best_score
        totals['greedy_Q_sum'] += greedy_Q

    print('-' * 97)
    it_ratio_total = totals['iters'] / max(1, totals['base_iters'])
    rep_ratio_total = totals['base_t_repair'] / max(0.001, totals['t_repair'])
    print(f"{'TOTAL':<18}{totals['elapsed']:>9.2f}s"
          f"{totals['iters']:>8}{it_ratio_total:>6.1f}x"
          f"{totals['t_repair']:>10.2f}s{rep_ratio_total:>8.2f}x"
          f"{totals['greedy_Q_sum']:>12.2f}{totals['Q_sum']:>12.2f}"
          f"{totals['Q_sum']-totals['greedy_Q_sum']:>+10.2f}")
    print()
    print(f"  Baseline total iters (7 cases):     {totals['base_iters']}")
    print(f"  Optimised total iters:              {totals['iters']}  "
          f"({it_ratio_total:.2f}x)")
    print(f"  Baseline total repair time:         {totals['base_t_repair']:.2f}s")
    print(f"  Optimised total repair time:        {totals['t_repair']:.2f}s  "
          f"({rep_ratio_total:.2f}x faster)")
    print()

    # Drill-down: confirm rng.random calls are gone from the sort path
    print("cProfile — focus on sort / shuffle / repair / copy paths")
    print('-' * 85)
    profiler = cProfile.Profile()
    profiler.enable()
    for name in CASE_NAMES:
        cdir = os.path.join(cases_dir, name)
        sol = os.path.join(greedy_out, f'{name}.csv')
        cfg = lns_sa.LNSConfig(time_limit=LNS_TIME, verbose=False)
        lns_sa.run_lns_sa(cdir, sol, cfg)
    profiler.disable()
    out_path = 'traces/repair_optims_profile.pstats'
    profiler.dump_stats(out_path)

    buf = io.StringIO()
    p = pstats.Stats(out_path, stream=buf)
    p.strip_dirs()
    p.sort_stats('tottime')
    p.print_stats(r'greedy_repair|generate_candidates|shuffle|'
                  r"'sort'|'random'|'copy'|_any", 25)
    print(buf.getvalue())
    print(f"Wrote {out_path}")


if __name__ == '__main__':
    main()
