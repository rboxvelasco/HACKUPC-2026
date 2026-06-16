# HackUPC 2026 — Mecalux Warehouse Optimizer

This repository contains our solution to the [Mecalux](https://www.mecalux.com/) challenge at HackUPC 2026.

The challenge, provided by Mecalux, consists of optimally placing storage shelves (“bays”) inside a warehouse in order to minimize the following quality metric:


$$Q = \left( \frac{\sum_{\text{bay}} \text{price}}{\sum_{\text{bay}} \text{loads}} \right)^{2 - \text{PercentageAreaUsed}}$$

Lower values of $Q$ are better.

Because the coverage term appears in the exponent, it has a dominant impact on the final score. Increasing coverage from 60% to 80% can roughly halve $Q$, so the algorithm prioritizes spatial utilization over marginal cost improvements.


<p align="center">
  <img src="assets/solution.gif" width="750" alt="Home"/>
</p>


## Problem description

We must place rectangular storage bays inside an axis-aligned warehouse that can be represented as a rectilinear polygon. The warehouse may contain rectangular obstacles that block certain areas, and the ceiling height is not uniform; instead, it changes along the X-axis in discrete steps.

Each storage bay is selected from a predefined catalog of types, and each type can be used an unlimited number of times. Bays can also be rotated to better fit the available space. In addition, every bay must have an adjacent free space on one of its sides to allow access, which further constrains valid placements.

The objective is to arrange all bays in a way that respects these constraints while minimizing the given cost function. The execution time for each instance is limited to 30 seconds.

The full problem statement can be found in [`PROBLEM_BRIEF.md`](docs/PROBLEM_BRIEF.md).


## Our Approach

We used a greedy algorithm that evaluates several criteria and picks the most favorable one to generate an initial solution. From there, we apply a local search algorithm that combines **Large Neighborhood Search** (LNS) with **simulated annealing**, letting us explore the solution space efficiently and avoid getting stuck in local optima.

To operate on the full state nimbly, we went with a bitmap-based representation, which lets us carry out every operation via bitmasks and standard **computer-vision** techniques, simplifying how the overall state is handled. Additionally, within LNS, the region to destroy and rebuild is selected via **convolutions** that identify areas with higher or lower shelf density, thereby guiding the solution-improvement process.

Refer to [`Algorithm Rationale.md`](docs/Algorithm_Rationale.md) for a more detailed explanation of the algorithms and modeling decisions.

After the solution is generated, a viewer is created and saved in [`solutions/`](solutions/). You can currently see a few example solutions there.


## Project Structure

```bash
📂
├── assets/           # Images for this README
├── benchmarks/       # Benchmark code and traces (used during development, now outdated)
│
├── Cases/            # Input test cases
│   ├── Case0/
│   │   ├── ceiling.csv
│   │   ├── obstacles.csv
│   │   ├── types_of_bays.csv
│   │   └── warehouse.csv
│   …
│
├── solutions/       # Example solution outputs
│
├── docs/            # Documentation and design rationale
│   ├── PROBLEM_BRIEF.md
│   ├── Algorithm Rationale.md
│   └── Problem Statement Mecalux 2026.pdf
│
├── src/
│   ├── core/
│   │   ├── bitmap.py         # Rasterizes solutions into bitmap representations
│   │   ├── greedy_solver.py  # Constructive greedy solver
│   │   ├── lns_sa.py         # LNS + Simulated Annealing refinement phase
│   │   ├── pipeline.py       # Full pipeline orchestrator (greedy → LNS+SA → comparison)
│   │   ├── solver.py         # High-level solver interface
│   │   └── visualize.py      # HTML visualization and side-by-side comparison
│   │
│   ├── bitmap_preview.py     # Bitmap visualization utility
│   └── validate.py           # Solution validator (Shapely-based constraint checking)
│
├── requirements.txt
└── README.md
```


## Quickstart

The pipeline writes CSVs and HTMLs to `solutions/` by default and opens the compare HTML in a browser unless `--no-open` is passed.

```bash
pip install -r requirements.txt
python3 -m src.core.pipeline Cases/Case0
```

For more execution options, you can see [`pipeline.py`](src/core/pipeline.py) header.


## Authors

| Nombre | Github |
|--------|--------|
| Marc Peñalver | [![GitHub](https://img.shields.io/badge/GitHub-mpenalverguilera-181717?logo=github)](https://github.com/mpenalverguilera) |
| Àlex González | [![GitHub](https://img.shields.io/badge/GitHub-AlexGonzalezFernandez-181717?logo=github)](https://github.com/AlexGonzalezFernandez) |
| Raül Box | [![GitHub](https://img.shields.io/badge/GitHub-rboxvelasco-181717?logo=github)](https://github.com/rboxvelasco)