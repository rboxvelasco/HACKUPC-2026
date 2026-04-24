# Problem Modelization
This is the Mecalux's project for the HackUPC 2026 this project consist on a optimization problem of allocation of `Bays` in a warehouse

## Input files
1. WAREHOUSE.CSV: Definition of the warehouse (coord x, coord y) - corners that define the area
2. OBSTACLES.CSV: Definition of the obstacles (coord x, coord y, width_x, depth_y) 
3. CEILING.CSV: (coord x_inital, heigth) - interval x_t_1, x_t | x_n, finish
4. BAY.CSV: information about the bays (id, width, depth, gap, nLoads, Price) | **maybe** depth_ = depth + gap 

## Elements of the problem

## Restrictions
1. Alçada no pot ser més alta que el sostre
2. Coordenades han de ser dins del recinte
3. Dos bays no poden ocupar el mateix espai
4. Gaps poden overlapear
5. S'han de respectar els gaps a tot moment
6. Una bay no pot compartir cordenades de un obstacle (obstacles poden estar modelats com espais buits)

## Goal
Place bays in a warehouse the cheapest way using the largest ammount of area:



## Action Points
- En temps de rebre input escalar al digit de menor pes i maybe fallback into another algo
- Solucions paral·leles