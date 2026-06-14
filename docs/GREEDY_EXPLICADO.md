# Cómo funciona nuestro greedy — explicación por capas

Explicación progresiva del `greedy_solver.py`, de lo más general a lo más
concreto. Vamos por capas: primero el "por qué", luego el "qué", y al
final el "cómo" con el detalle de cada pieza.

---

## Capa 0 — ¿Qué estamos optimizando?

La función objetivo es:

```
Q = (precio_total / cargas_totales) ^ (2 - coverage)
```

donde `coverage = área_bays / área_útil`.

Lo más importante: **el coverage está en el exponente**. Pasar de 60% a
80% de coverage no es "20% mejor", es muchísimo más. Por ejemplo, si
`precio/cargas = 500`:

- Coverage 60% → `Q = 500^1.4 ≈ 6620`
- Coverage 80% → `Q = 500^1.2 ≈ 1737`

**Cuatro veces mejor solo subiendo el coverage 20 puntos.** Por eso toda
la estrategia del greedy gira alrededor de meter tantas bays como sea
posible, con bays "baratas por carga" cuando se puede elegir.

---

## Capa 1 — La idea de una sola pasada greedy

Un greedy ingenuo sería: "encuentra cualquier sitio libre, mete la
primera bay que quepa, repite". Eso da soluciones malas porque deja
huecos raros.

Nuestra estrategia es más estructurada: **pack por filas horizontales**,
de izquierda a derecha, con un truco para compartir pasillos.

### El truco clave: pasillos alternados

Cada bay necesita un gap (pasillo) pegado a su lado "frontal". Si todas
las bays miran en la misma dirección, cada fila consume `depth + gap` de
altura vertical y el pasillo se desperdicia entre fila y fila.

Pero si alternamos la orientación de las filas:

```
Fila 0 (rotación 0°):   bay mira hacia arriba, gap arriba
Fila 1 (rotación 180°): bay mira hacia abajo, gap abajo
Fila 2 (rotación 0°):   ...
```

Entonces el gap de la fila 0 (arriba) y el gap de la fila 1 (abajo)
**se solapan en el mismo pasillo físico**. Un solo pasillo sirve a dos
filas. El pitch ya no es `depth+gap, depth+gap, ...`, sino
`depth+gap, depth, depth+gap, depth, ...`.

Para un almacén de altura H, en vez de caber `H / (depth + gap)` filas,
caben casi el doble. Esto por sí solo ya explica por qué llegamos al 80%
de coverage en Case 1 en lugar de ~55%.

### Una pasada greedy concreta

Dado un `depth` (profundidad de fila elegida del catálogo) y su `gap`:

1. Calcula las posiciones Y de todas las filas alternadas:
   `y0=0 (rot 0°), y1=depth+gap (rot 180°), y2=2·depth+gap (rot 0°), ...`
2. Para cada fila, intersecta su banda horizontal con
   `warehouse − obstáculos` para encontrar los segmentos X libres (los
   obstáculos parten filas en trozos).
3. En cada segmento, avanza el cursor de izquierda a derecha:
   - Mira qué bays caben en ese cursor (respetando techo, colisiones,
     polígono del almacén).
   - Puntúa cada candidato con un **criterio greedy** (ver abajo).
   - Pone la que tenga mejor puntuación. Avanza el cursor su ancho.
   - Si ninguna cabe, avanza el cursor el ancho mínimo del catálogo y
     sigue.

Eso es **una sola pasada**. Determinista. Rápida. Ya da una solución
razonable.

---

## Capa 2 — La matriz de búsqueda

Una sola pasada no es suficiente porque hay decisiones que afectan mucho
al resultado:

- ¿Qué `depth` elegimos para las filas?
- ¿Empezamos con gap arriba o gap abajo?
- ¿Qué criterio greedy usamos?
- ¿Filas horizontales o verticales?

En vez de adivinar la mejor combinación, **las probamos todas**. Como
cada pasada es barata (decenas de ms), hacer ~100 pasadas es asequible.

La matriz:

```
orientación     ∈ { horizontal, vertical }            ← 2 opciones
depth           ∈ { cada depth y width del catálogo } ← típicamente 3-4
rotación_inicio ∈ { 0°, 180° }                        ← gap-arriba o gap-abajo primero
criterio        ∈ { 6 funciones de puntuación }       ← ver abajo
```

Total: ~100 combinaciones por caso. Probamos las 100 y nos quedamos con
la de mejor Q.

### Los 6 criterios greedy

Cuando el packer mira candidatos en el cursor, necesita una manera de
ordenarlos. Seis criterios distintos:

| Criterio | Fórmula | Qué prioriza |
|----------|---------|--------------|
| `eff×width` | `area × loads / price × ew` | Equilibrado — el default |
| `cov-heavy` | `ew² × loads / price` | Bays anchas (coverage) |
| `loads/$` | `loads / price` | Cargas más baratas |
| `width` | `ew` | Coverage puro |
| `big-first` | `ew × 1000 + loads/price` | La más grande, desempate por precio |
| `area/$` | `area / price` | Área por euro |

`ew` es el ancho efectivo de la bay en la fila. Cada criterio enfoca el
problema desde un ángulo distinto y gana en situaciones distintas.

### La orientación vertical

Es más sutil. El algoritmo está escrito para empaquetar **filas
horizontales**. Para probar "filas verticales" hacemos:

1. Transponer todo: `warehouse`, `obstáculos`, `ceiling` (intercambiar
   X e Y).
2. Empaquetar con el algoritmo horizontal normal.
3. Destransponer las posiciones de las bays resultantes (y rotarlas
   adecuadamente: 0°↔270°, 90°↔180°).

Parece un truco pero es lo que gana Case 0. La forma L del almacén
empaqueta mucho mejor con tiras verticales que horizontales.

---

## Capa 3 — El rellenador de huecos (gap filler) — **ELIMINADO**

Históricamente el greedy cerraba cada pasada con un gap filler basado
en anchors (esquinas de bays colocadas, obstáculos y vértices del
almacén, extendidas por las dimensiones de cada tipo de bay). Intentaba
meter bays en huecos residuales aceptándolas sólo si mejoraban Q
estrictamente.

**Se eliminó** tras medirlo (`traces/filler_vs_sa.csv`):

- Coste: ~75 s totales sobre los 17 casos.
- Ganancia de Q: ~+1664 pts totales, concentrada casi por completo en
  Case0 (+1632) y Case13 (+32).
- Cuando LNS+SA corre después, **ya recupera esa ganancia y más**
  (−4211 pts de Q totales respecto al greedy-con-filler, −4067 sólo en
  Case15_mega). Y mantener el filler antes de LNS no cambia Q respecto
  a quitarlo: ΔQ entre "greedy+filler+LNS" y "greedy+LNS" es −20 pts,
  indistinguible.

Conclusión: el filler era redundante con el LNS+SA downstream y
bloqueaba tiempo que LNS puede usar mejor. El greedy queda ahora como
constructor puro (matriz de pasadas → mejor resultado).

---

## Capa 4 — Los dos caminos del solver

Hasta aquí hemos descrito **un** pipeline: matriz de pasadas → mejor
resultado. El solver real corre **dos pipelines** y se queda con el
mejor.

### Camino A — Whole-warehouse

El pipeline que acabamos de describir, aplicado al almacén entero como
una sola zona.

**Gana cuando**: el almacén es razonablemente convexo o tiene obstáculos
"integrados" en la rejilla de filas. Cases 1, 2, 4, 5, 6, 7, 8, 9, y 0
(con orientación vertical).

### Camino B — Region decomposition

Cuando el almacén tiene forma que se presta a descomponerse en
rectángulos grandes (una cruz, un donut), conviene empaquetar cada
rectángulo por separado, porque cada uno puede preferir una orientación
distinta.

El proceso:

1. **Descomponer en rectángulos**: coge todas las coordenadas X e Y de
   vértices del almacén y esquinas de obstáculos. Estas líneas crean una
   rejilla. Cada celda está totalmente dentro o totalmente fuera del
   área útil (gracias a que todo es rectilíneo). Fusiona greedy las
   celdas útiles en rectángulos maximales.
2. **Ordenar los rectángulos**: probamos varias ordenaciones (más grande
   primero, por Y-X, as-given).
3. **Para cada rectángulo en la orden elegida**:
   - Probar orientación H y V.
   - En cada orientación, mini-matriz de búsqueda
     (3 criterios × 2 depths × 2 start_rot).
   - Quedarse con la mejor para ese rectángulo.
   - Las bays ya colocadas en rectángulos anteriores se tratan como
     **obstáculos** para los siguientes, evitando colisiones en las
     fronteras.
4. Quedarse con la mejor de las ordenaciones probadas.

**Gana cuando**: la forma descompone "limpiamente" y cada región
prefiere distinta orientación. Case 3 (cruz) y Case 10 (donut) son los
casos canónicos.

### El dispatcher

El entrypoint corre los dos caminos, compara Q, y escribe el mejor:

```python
whole_Q, whole_placements = solve_whole_warehouse()
regional_Q, regional_placements = solve_with_region_decomposition()

if regional_Q < whole_Q:
    escribir regional_placements
else:
    escribir whole_placements
```

---

## Capa 5 — Por qué esto funciona (y por qué es simple)

Cuatro observaciones que justifican el diseño:

1. **El alternating-gap strip packing está cerca del óptimo en
   rectángulos.** No necesitas búsqueda sofisticada: los pasillos se
   comparten automáticamente por construcción.
2. **La matriz de búsqueda es pequeña y exhaustiva.** Tras la poda a
   2 criterios quedan ~16 pasadas × ~10 ms cada una = <1 s. Nada que
   optimizar con SA o LNS a nivel constructivo.
3. **La descomposición regional da "gratis" orientaciones distintas en
   partes distintas del almacén.** Buscar eso directamente con un
   solver continuo sería carísimo.
4. **El refinamiento lo hace LNS+SA después.** Los huecos residuales
   los cierra la fase bitmap+FFT, que es exactamente para lo que sirve
   y para lo que el gap filler determinista era un mal sustituto.

---

## Resumen ejecutivo

Por cada caso:

1. **`solve_whole_warehouse`**:
   - Genera ~16 pasadas de strip packing con alternating gaps,
     variando `(orientación, depth, start_rot, criterio)`.
   - Se queda con la de menor Q.
2. **`solve_regional`**:
   - Descompone el almacén en rectángulos axis-aligned.
   - Para cada ordenación candidata, empaqueta cada rectángulo
     independientemente (con mini-matriz), tratando placements
     anteriores como obstáculos.
3. **Dispatcher**: compara los dos resultados, escribe el mejor.
4. **LNS+SA** (fuera del greedy, en `lns_sa.py`) refina la solución
   durante ~25 s por caso.

Todo el constructivo es determinista. El refinamiento es estocástico
por construcción (SA). Juntos ganan al solver complejo anterior en los
17 casos.
