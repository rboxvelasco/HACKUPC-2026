# Problem Modelization
This is the Mecalux's project for the HackUPC 2026 this project consist on a optimization problem of allocation of `Bays` in a warehouse

## Input files
1. WAREHOUSE.CSV: Definition of the warehouse (coord x, coord y) - corners that define the area
2. OBSTACLES.CSV: Definition of the obstacles (coord x, coord y, width_x, depth_y) 
3. CEILING.CSV: (coord x_inital, heigth) - interval x_t_1, x_t | x_n, finish
4. TYPES_OF_BAYS.CSV: information about the bays (id, width, depth, height, gap, nLoads, Price) | **maybe** depth_ = depth + gap 

## Elements of the problem

## Restrictions
01. Alçada no pot ser més alta que el sostre **en tots els punts de la estanteria**
02. Coordenades han de ser dins del recinte
03. Dos bays no poden ocupar el mateix espai
04. Gaps poden overlapear
05. S'han de respectar els gaps a tot moment
06. Una bay no pot compartir cordenades de un obstacle (obstacles poden estar modelats com espais buits)
07. Les bays poden començara (0,0) de warehouse es a dir poden compartir boundary amb el warehouse
08. El warehouse estara sempre aliniean amb els axis.
09. Els obstacles i bays només poden ser rectangulars
10. Cada id representa un **tipus** de estanteria, és a dir podem repetir estanteries

## Goal
Place bays in a warehouse the cheapest way using the largest ammount of area:
$$Q = \left( \frac{\sum_{\text{bay}} \text{price}}{\sum_{\text{bay}} \text{loads}} \right)^{2 - \frac{\sum_{\text{bay}} \text{area}}{\text{area}_{\text{warehouse}}}}$$

Each experiment needs to be finished in 30s this means that the case should finish in 10s

## Expected output
Per cada bay colocat: Id, X, Y, Rotation

## Action Points
- En temps de rebre input escalar al digit de menor pes i maybe fallback into another algo
- Solucions paral·leles
- 2 punts marcan la rotació