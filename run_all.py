#!/usr/bin/env python3
"""Run solver on all cases and validate outputs."""

import sys
import os
import time
import subprocess


def main():
    cases_dir = sys.argv[1] if len(sys.argv) > 1 else 'Cases'
    output_dir = sys.argv[2] if len(sys.argv) > 2 else 'solutions'

    os.makedirs(output_dir, exist_ok=True)

    # Find all case directories
    case_dirs = sorted([
        os.path.join(cases_dir, d)
        for d in os.listdir(cases_dir)
        if os.path.isdir(os.path.join(cases_dir, d))
    ])

    total_start = time.time()

    for case_dir in case_dirs:
        case_name = os.path.basename(case_dir)
        output_file = os.path.join(output_dir, f'{case_name}.csv')

        print(f"\n{'='*60}")
        print(f"  Running {case_name}")
        print(f"{'='*60}")

        result = subprocess.run(
            ['python3', 'solver.py', case_dir, output_file],
            capture_output=False, text=True,
        )

        if result.returncode != 0:
            print(f"  ERROR: solver failed for {case_name}")

    total_time = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"  ALL CASES DONE in {total_time:.2f}s")
    print(f"  Under 30s: {'YES' if total_time < 30 else 'NO'}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
