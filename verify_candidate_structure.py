"""Micro-experiment: show how candidates per (bay_type) distribute across
rotations and geographically. Validates whether the proposed dedup-by-bay
rule is safe.

Run:  python3 verify_candidate_structure.py Cases/Case1 solutions/Case1.csv
"""
import os, sys, random
from collections import Counter, defaultdict
import lns_sa
from solver import parse_bay_types

case_dir = sys.argv[1] if len(sys.argv) > 1 else 'Cases/Case1'
sol_path = sys.argv[2] if len(sys.argv) > 2 else 'solutions/Case1.csv'

state = lns_sa.LNSState.from_case(case_dir, sol_path)
bay_types = parse_bay_types(os.path.join(case_dir, 'types_of_bays.csv'))
kernels = lns_sa.build_kernels(bay_types, state.cell_size)

# Simulate destroy to get a realistic rect
rng = random.Random(42)
rect, removed = lns_sa.destroy_low_density(state, kernels, 5.0, rng)
print(f"rect={rect}  removed={len(removed)} bays")

cands = lns_sa.generate_candidates(state, kernels, rect, bay_types)
print(f"total candidates: {len(cands)}")

# For each bay type: how many candidates per rotation and what is the
# geographical spread?
by_bay = defaultdict(list)
for c in cands:
    by_bay[c.bay_type_id].append(c)

print(f"\n{'bay':<5}{'total':>8}{'rot0':>8}{'rot90':>8}{'rot180':>8}{'rot270':>8}"
      f"{'uniq_pos':>12}{'max_dist':>10}")
for bid in sorted(by_bay):
    cs = by_bay[bid]
    rot_counts = Counter(c.rotation for c in cs)
    positions = {(c.row, c.col) for c in cs}
    rows = [c.row for c in cs]
    cols = [c.col for c in cs]
    max_dist = max((max(rows) - min(rows), max(cols) - min(cols))) if cs else 0
    print(f"{bid:<5}{len(cs):>8}{rot_counts.get(0,0):>8}{rot_counts.get(90,0):>8}"
          f"{rot_counts.get(180,0):>8}{rot_counts.get(270,0):>8}"
          f"{len(positions):>12}{max_dist:>10}")

# Key question: for a given (bay, position) tuple, how many rotations coexist?
pos_key_rots = defaultdict(set)
for c in cands:
    pos_key_rots[(c.bay_type_id, c.row, c.col)].add(c.rotation)
rot_counts_distribution = Counter(len(s) for s in pos_key_rots.values())
print(f"\nRotations per (bay, row, col) tuple:")
for k in sorted(rot_counts_distribution):
    print(f"  {k} rotations: {rot_counts_distribution[k]} tuples")
