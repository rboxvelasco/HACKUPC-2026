#!/usr/bin/env python3
"""Probe where lns_sa stalls on CaseAngledD."""
import sys, time, os

from lns_sa import (
    LNSState, LNSConfig, build_kernels, destroy_low_density,
    generate_candidates, greedy_repair, q_score,
)
from solver import (
    parse_bay_types, parse_obstacles, parse_warehouse, usable_area, compute_score,
)
import random

case_dir = 'Cases/CaseAngledD'
in_sol = '/tmp/angledD.csv'

t0 = time.time()
print(f"[{time.time()-t0:6.2f}s] parsing…", flush=True)
wh = parse_warehouse(os.path.join(case_dir, 'warehouse.csv'))
obs = parse_obstacles(os.path.join(case_dir, 'obstacles.csv'))
ua = usable_area(wh, obs)
bts = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))

print(f"[{time.time()-t0:6.2f}s] LNSState.from_case…", flush=True)
state = LNSState.from_case(case_dir, in_sol)
print(f"[{time.time()-t0:6.2f}s] state shape={state.shape} cell_size={state.cell_size} placed={len(state.placed)}", flush=True)

print(f"[{time.time()-t0:6.2f}s] build_kernels…", flush=True)
kernels = build_kernels(bts, state.cell_size)
print(f"[{time.time()-t0:6.2f}s] kernels for {len(kernels)} bay types", flush=True)

rng = random.Random(42)
print(f"[{time.time()-t0:6.2f}s] destroy_low_density…", flush=True)
rect, removed = destroy_low_density(state, kernels, 5.0, rng)
print(f"[{time.time()-t0:6.2f}s] rect={rect} removed={len(removed)}", flush=True)

print(f"[{time.time()-t0:6.2f}s] generate_candidates…", flush=True)
cands = generate_candidates(state, kernels, rect, bts)
print(f"[{time.time()-t0:6.2f}s] candidates={len(cands)}", flush=True)

print(f"[{time.time()-t0:6.2f}s] greedy_repair…", flush=True)
new = greedy_repair(state, cands, kernels, {b.id: b for b in bts}, rng)
print(f"[{time.time()-t0:6.2f}s] newly placed={len(new)}", flush=True)

print(f"[{time.time()-t0:6.2f}s] done iter 1", flush=True)
