## Definición del problema

Tengo que resovler un problema de optmizacion, tengo un warehouse y varias estanterias, tambien tengo la posicion de varias columnas o muros (llamemoslos obstaculos) donde no se pueden situar las estanterias, delante de cada estanteria (en el lado Y+) debe haber un gap para poder coger su contenido. Las medidas de las estanterias y los gaps se especifican en el input para cada estanteria.

Queremos minimizar Q.

Se pueden repetir ilimitadamente cada instancia de bay.

1. La altura no puede ser mayor que el techo **en todos los puntos de la estantería**.
2. Las coordenadas deben estar dentro del recinto.
3. Dos bays no pueden ocupar el mismo espacio.
4. Los gaps de varios bays pueden solaparse
5. Los gaps deben respetarse en todo momento y siempre debe estar en el lado Y+ en cordenadas del bay, por lo tanto al rotar este especto se debería tener en cuenta.
6. Una bay no puede compartir coordenadas con un obstáculo (los obstáculos pueden modelarse como espacios vacíos).
7. Las bays pueden comenzar en (0,0) del almacén; es decir, pueden compartir el límite con el warehouse.
8. El warehouse estará siempre alineado con los ejes.
9. Los obstáculos y las bays solo pueden ser rectangulares.
10. Cada ID representa un **tipo** de estantería; es decir, podemos repetir estanterías.
11. Los gaps no pueden estar en coordenadas que no pertenezcan al warehouse.


```
Q = ((sum of bay prices)/(sum of bay loads))^(2-PercentageAreaUsed)
```

## Formato de Input

1. WAREHOUSE.CSV: Definition of the warehouse (coord x, coord y) - corners that define the area
2. OBSTACLES.CSV: Definition of the obstacles (coord x, coord y, width_x, depth_y) 
3. TYPES_OF_BAYS.CSV: information about the bays (id, width, depth, height, gap, nLoads, Price)
4. CEILING.CSV: (coord x_inital, heigth)

i.e.
```
0,3000
3000,2000
6000,3000
```

means:
```
x ∈ [0,3000) → heigth = 3000
x ∈ [3000,6000) → heigth = 2000
x ∈ [6000,end] → heigth = 3000
```


Ejemplos 0 y 1:

```
===== Case0/ceiling.csv =====
0, 3000
3000, 2000
6000, 3000



===== Case0/obstacles.csv =====
750, 750, 750,750
8000, 2500, 1500, 300
1500, 4200, 200, 4600


===== Case0/types_of_bays.csv =====
0,  800, 1200, 2800, 200,  4, 2000
1, 1600, 1200, 2800, 200,  8, 2500
2, 2400, 1200, 2800, 200, 12, 2800
3,  800, 1000, 1800, 150,  3, 1800
4, 1600, 1000, 1800, 150,  6, 2300
5, 2400, 1000, 1800, 150,  9, 2600


===== Case0/warehouse.csv =====
0,0
10000,0
10000,3000
3000,3000
3000,10000
0,10000


===== Case1/ceiling.csv =====
0, 3000
3000, 6000


===== Case1/obstacles.csv =====


===== Case1/types_of_bays.csv =====
0, 1300, 1000, 1400, 500,  1, 1000
1, 1300, 1000, 2800, 500,  2, 1300
2, 1300, 1000, 4200, 500,  3, 1600
3, 1300, 1000, 5600, 500,  4, 1900
4, 2300, 1000, 1400, 500,  2, 1600
5, 2300, 1000, 2800, 500,  4, 2080
6, 2300, 1000, 4200, 500,  6, 2560
7, 2300, 1000, 5600, 500,  8, 3040
8, 3300, 1000, 1400, 500,  3, 2200
9, 3300, 1000, 2800, 500,  6, 2860
10, 3300, 1000, 4200, 500,  9, 3520
11, 3300, 1000, 5600, 500,  12, 4180
12, 4300, 1000, 1400, 500,  4, 2800
13, 4300, 1000, 2800, 500,  8, 3640
14, 4300, 1000, 4200, 500,  12, 4480
15, 4300, 1000, 5600, 500,  16, 5320


===== Case1/warehouse.csv =====
0,0
10000,0
10000,10000
0,10000

```


## Pseudocodigo del LNS/SA:


Approach de Simmulated Annealing con Large Neighborhood Search.

Es decir que en cada iteracion del SA destruimos y reconstruimos una seccion del bitmap.


```
S = initial_solution()
best = S

for iteration:
    S' = destroy(S) // pensar com seleccionar quina zona destruim de forma inteligent
    S' = repair(S') // convolutional greedy + filtres morfologics + operacions de bitmask

    q_new = evaluate(S')
    q_old = evaluate(S)

    if q_new < q_old:
        S = S'
    else:
        accept probabilistically via SA

    update temperature

    if S better than best:
        best = S
```

### Fase de repair

1. Buscar posiciones prometedoras
2. Filtrar posiciones inviables
3. Aplicar colocación eficiente
4. Actualizar estado
5. Repetir hasta llenar región


En la implementación se traduciría:
   **1. Convolutional greedy** ("Dónde merece la pena intentar colocar"): Es un generador de candidatos inteligentes.
    Estado general definido por:
    - Bit map posiciones ya ocupadas por bays (MO)
    - Bit map posiciones que ocupan los gaps de los bays (MG)
    Por cada instancia (referimos más adelante lo que es "instancia") genearar dos kernels:
    1. Kernel BG: todas las posiciones a 1 (tanto bay como gap) con el tamaño minimo para encabar el bay y el gap
    2. Kernel B: todas las posiciones del bay a 1.

    Convolucionar Kernel BG con el bit map MO para evitar colisiones
    Convolucionar Kernel B con el bit map MG para evtiar quitar los gaps de bays ya puestas

    El resultado de estas convoluciones no será un bitmap sinó del tamaño del kernel.
    Es decir: Conv(BG, MO) and Conv(B,MG) indicara con un 0 en que posiciones podemos añadir ese bay.

    //Estos calculos se deberían de bulletproofear (h es numero de filas, l es numero de columnas)
    Para gestionar la rotación de los bays vamos a restringir a 4 casos (0º, 90º, 180º, 270º), para cada bay generaremos instancias kernels:
    1. Kernel base: 0º (abajo izquireda kernel bay == i,j)
    2. Kernel base transpuesto: -90º = 270º (j, i)
    3. Kernel base mirrored: 180º (h+i-1, l) donde 
    4. Kernel transpuesta mirrored: 90º (l, h+i-1)

    Hay que tener en cuenta que la posicion de origen para cada caso será distinta como se ha mencionado.

    además, tener otro bitmap (MH) con las alturas y comprobar que `min(height_vector en footprint)≥hba`
    MH es el vector de x marcando para cada posicion en x la altura del techo, para cada posicion candidata filtrar:
    min(MH_[candidato_x0], ..., MH[candidato_x_l]) ≥ altura_bay

    El output de esta fase no será un bitmpa modificao, sino un grupo de tuplas (coordenadas, kernel):
        ({x, y, orientacion}, {Kernel B, Kernel BG}

   **2. Filtros morfológicos** (“Esto físicamente no cabe”): Es un pruning.
    
    AQUI escogemos la distribucion final de los bays y actualizamos la nueva distribución candidata.

    Tenemos que elegir un subconjunto de kernels (que representan los bays y gaps) que no se solapen. Los iremos eligiendo con criterio greedy.
    El primero en entrar se queda y lo reconstruimos en la zona destruida del mapa, generando T'.
    El segundo se queda siempre y cuando no se solape con T'.
    etc.

   **3. Bitmask Operations** ("Hazlo rápido"): reasignación de los bays. Comprueba, inserta, eliina y actualiza.
   Comprobar colisión: (mask & shifted_bay) != 0
   Insertar: mask |= shifted_bay
   Eliminar: mask ^= shifted_bay   o     mask &= ~shifted_bay


> **Resumen conceptual/ejemplo**
> 
> Destroy deja hueco
>
> Convolution greedy encuentra 20 posiciones interesantes
>
> Filtros reduce #candidatos: 20 → 7 válidos
> Se añaden esos 7 a los bitmaps MO y MG
>
> ```
> repair():
>     while region_not_filled:
>         candidates = convolution_search()
>         valid = filter(candidates)
>         best = evaluate_with_bitmask(valid)
>         place(best) // instead of best, select randomly between top-K scored
> ```
>
> donde best candidate = argmin(local_cost)
>
> el cálculo de local_cost puede incluir densidad, fill ratio, etc.
>
> Lo de añadir y eliminar bays al bitmap se puede hacer algo tipo:
>   Comprobar colisión: (mask & shifted_bay) != 0
>   Insertar: mask |= shifted_bay
>   Eliminar: mask ^= shifted_bay   o     mask &= ~shifted_bay