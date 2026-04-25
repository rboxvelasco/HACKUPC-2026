# HackUPC 2026 — Mecalux Warehouse Optimizer

Optimizador de colocación de estanterías (bays) en un almacén. Dado un almacén con forma poligonal, obstáculos, techo variable y un catálogo de tipos de bay, el objetivo es minimizar:

```
Q = (total_price / total_loads) ^ (2 - coverage)
```

donde `coverage = suma_area_bays / area_warehouse`.

## Quickstart

```bash
pip install -r requirements.txt
python3 solver.py Cases/Case0 solution.csv
```

Para ejecutar todos los casos (< 30s total):

```bash
python3 run_all.py Cases solutions
```

Para validar y visualizar:

```bash
python3 validate.py Cases/Case0 solutions/Case0.csv
python3 visualize.py Cases/Case0 solutions/Case0.csv solutions/Case0.html
```

## Arquitectura del solver (v2 — Strip Packing)

Pipeline de tres fases con presupuesto de tiempo adaptativo (~7s por caso). Sin dependencias de ILP — solo geometría computacional.

### Fase A — Strip Packing con Alternating Gaps

1. **Free space**: resta obstáculos del warehouse.
2. **Row positions**: para cada profundidad de bay, calcula posiciones Y alternando rot 0° (gap arriba) y rot 180° (gap abajo). Esto permite empaquetar filas más juntas: pitch = depth+gap, depth, depth+gap, depth... en vez de depth+gap constante.
3. **Ceiling-aware row packing**: para cada fila, coloca bays de izquierda a derecha seleccionando el tipo más eficiente que quepa bajo el techo local en cada posición X.
4. **Multi-start**: prueba todas las profundidades de bay y ambas direcciones de alternancia, se queda con la mejor combinación.

### Fase B — Gap Filling

Genera posiciones candidatas desde bordes de bays colocadas, obstáculos y vértices del warehouse. Coloca bays adicionales que mejoren Q.

### Fase C — Simulated Annealing

Swap de tipos, eliminación y adición de bays con cooling lineal.

## Resultados

| Case | Bays | Coverage | Q |
|------|------|----------|---|
| 0 | 16 | 64% | 2,363 |
| 1 | 24 | 79% | 1,340 |
| 2 | 20 | 58% | 4,945 |
| 3 | 44 | 59% | 4,135 |

Tiempo total: ~28s para 4 casos.

## Ficheros

| Fichero | Descripción |
|---------|-------------|
| `solver.py` | Solver principal |
| `validate.py` | Validador de soluciones |
| `visualize.py` | Generador de visualización HTML interactiva |
| `run_all.py` | Runner para todos los casos |
