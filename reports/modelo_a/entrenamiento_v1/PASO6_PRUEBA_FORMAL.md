# Paso 6: prueba formal de cada grupo de variables (Diebold–Mariano)

Recálculo en local del paso 6 del notebook 2. Mismos datos (salida del notebook 1 en Kaggle), los mismos 750 pixeles de entrenamiento y la misma partición. Sin Prithvi, que va en el complemento. Tabla completa: `paso6_seleccion_variables_diebold_mariano.csv`.

**Pruebas.** Para cada ronda, se compara el pronóstico de validación sin el grupo y con el grupo:

1. **Diebold–Mariano (1995)**, pérdida cuadrática:
   - la diferencia de errores se promedia por bloque de 10 días (100 bloques), porque los pixeles de un mismo día no son independientes;
   - varianza de largo plazo de Newey–West, con 3 rezagos (30 días);
   - corrección de Harvey–Leybourne–Newbold (1997);
   - t de Student, dos colas.

   **H₀: el grupo no cambia el error.**
2. **Regla de entrada** (fijada antes de ver resultados): IC 95 % por bootstrap de bloques de la mejora en skill medio (1 − RMSE / RMSE de la persistencia, promedio de los 5 horizontes).

**Lectura del estadístico DM:** mayor que 0 significa que con el grupo hay menos error.

| Índice | Ronda | Grupo | RMSE sin → con | DM | Valor p | ¿Entra? |
|---|---|---|---|---|---|---|
| NDVI | 1 | fenología | 0,162 → 0,139 | +5,18 | < 0,001 | **sí** |
| NDVI | 1 | valores recientes | 0,162 → 0,153 | +3,72 | < 0,001 | (ronda 2) |
| NDVI | 1 | lluvia | 0,162 → 0,154 | +0,94 | 0,35 | no |
| NDVI | 1 | ETo | 0,162 → 0,156 | −0,65 | 0,52 | no |
| NDVI | 1 | ONI | 0,162 → 0,181 | −5,19 | < 0,001 | no (empeora) |
| NDVI | 2 | valores recientes | 0,139 → 0,134 | +4,02 | < 0,001 | **sí** |
| NDVI | 2 | lluvia | 0,139 → 0,138 | +1,54 | 0,13 | no |
| NDVI | 2 | ETo | 0,139 → 0,138 | +0,42 | 0,68 | no |
| NDVI | 3 | lluvia | 0,134 → 0,134 | +0,66 | 0,51 | no |
| NDVI | 3 | ETo | 0,134 → 0,134 | −1,06 | 0,29 | no |
| NDMI | 1 | fenología | 0,123 → 0,109 | +5,22 | < 0,001 | **sí** |
| NDMI | 1 | lluvia | 0,123 → 0,119 | +1,39 | 0,17 | no |
| NDMI | 1 | ONI | 0,123 → 0,141 | −3,92 | < 0,001 | no (empeora) |
| NDMI | 2 | valores recientes | 0,109 → 0,105 | +2,94 | 0,004 | **sí** |
| NDMI | 2 | lluvia | 0,109 → 0,108 | +0,75 | 0,45 | no |
| NDMI | 2 | ETo | 0,109 → 0,109 | −0,01 | 0,99 | no |
| NDMI | 3 | lluvia | 0,105 → 0,104 | +0,09 | 0,93 | no |
| NDMI | 3 | ETo | 0,105 → 0,105 | +0,65 | 0,52 | no |

**Resultado:**
- Diebold–Mariano confirma la selección: entran **fenología** y **valores recientes del índice**, con p < 0,005.
- **Lluvia y ETo no cambian el error de forma significativa en ninguna ronda** (p entre 0,13 y 0,99).
- El **ONI empeora** el pronóstico de forma significativa cuando entra solo (p < 0,001): con pocos años, el modelo se aprende el año en vez del cultivo.

**Una discrepancia que se reporta:** en el NDMI de la ronda 2, el bootstrap del skill medio da a la ETo una mejora con IC que excluye el 0 (+0,006 a +0,020), pero DM sobre el error total da p = 0,99. La mejora se concentra en algunos horizontes y no cambia el error total. Igual no entra, porque esa ronda la gana "valores recientes", que mejora más.
