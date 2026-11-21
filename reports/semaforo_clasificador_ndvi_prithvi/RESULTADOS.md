# Semáforo de humedad con Prithvi

Script: `scripts/semaforo_clasificador.py` con las variantes `ndvi_prithvi` y `prithvi_obligatorio`. Comparación: `scripts/comparar_semaforo_prithvi.py`.

**Mismo protocolo que el semáforo base** (`reports/semaforo_clasificador/`):
- mismas 4 particiones de prueba selladas;
- selección en validación por AUC-ROC macro;
- cada medición se prueba una sola vez.

**Entrada de Prithvi:**
- Prithvi-EO-2.0-300M-TL sobre HLS: un vector de 1024 números por fecha, de `reports/modelo_b/prithvi_embeddings.csv`. Hay 16 fechas: 7 en GS1, 6 en GS2 y 2 en GS3.
- Se lleva a diario por interpolación en días desde siembra, igual que el NDVI.
- Se resume en 3 componentes con PCA. El PCA se ajusta **solo con las fechas de entrenamiento** de cada partición.

**Las dos variantes:**
- `ndvi_prithvi`: cada candidato (logística, Random Forest, Gradient Boosting, XGBoost, ARIMAX) entra con y sin Prithvi, y la validación decide.
- `prithvi_obligatorio`: todos los candidatos llevan Prithvi. Es el esquema "cabeza sobre Prithvi".

## Prueba sellada: las mismas 198 mediciones

La diferencia es contra el semáforo base. El IC 95 % es pareado, por bootstrap de bloques de 10 días (1000 repeticiones).

| Métrica | Base (sin Prithvi) | Decide la validación | Diferencia [IC 95 %] | Prithvi obligatorio | Diferencia [IC 95 %] |
|---|---|---|---|---|---|
| AUC-ROC macro | **0,75** | 0,74 | −0,01 [−0,04 a +0,02] | 0,71 | −0,04 [−0,09 a +0,01] |
| Recall rojo | **0,65** (11/17) | 0,65 (11/17) | 0,00 | 0,47 (8/17) | −0,18 [−0,43 a 0,00] |
| Recall amarillo | 0,29 | 0,29 | 0,00 [−0,07 a +0,07] | 0,26 | −0,02 [−0,15 a +0,13] |
| Recall verde | 0,66 | 0,68 | +0,02 [0,00 a +0,04] | **0,75** | +0,09 [+0,03 a +0,15] |
| Exactitud balanceada | 0,53 | 0,54 | +0,01 [−0,02 a +0,04] | 0,49 | −0,04 [−0,14 a +0,05] |

**Validación** (mejor AUC macro por familia, promedio de las 4 particiones):
- ARIMAX 0,772; XGBoost + Prithvi 0,771; XGBoost 0,768; Gradient Boosting + Prithvi 0,767; ARIMAX + Prithvi 0,759.
- Con libertad de elegir, Prithvi gana en 1 de las 4 particiones.

**Tiempo en este PC (16 hilos):** base 10 min; con y sin Prithvi 22 min; Prithvi obligatorio 13 min.

**Control:** el semáforo base se volvió a correr con el código nuevo y reproduce sus archivos sin cambios.

## Lectura

- **Prithvi no rompe el semáforo, pero tampoco lo mejora.** Si la validación decide, el resultado es el mismo (AUC 0,74 frente a 0,75, con IC que incluye el 0).
- **Obligado, cambia el balance.** Acierta más verdes (+0,09, IC sin el 0) y detecta menos rojos (8 de 17 frente a 11 de 17). Para riego, perder rojos es lo grave.
- **Por qué no aporta:** el vector de Prithvi es uno solo por fecha para el bloque de 480 m que contiene el lote. Es el mismo para CD, MIRI y AWD+MIRI, así que no puede decir cuál franja se está secando. Lo que separa las franjas es el riego y el NDVI de 10 m por franja. Además hay pocas fechas: en GS3 solo 2, a 60 días de distancia.
- **Decisión** (regla de empate: si el IC de la diferencia incluye el 0, gana el más simple): **semáforo base, sin Prithvi**.
- **Dónde sí está Prithvi:** en el Modelo A (relleno de nubes).
## Comparación por familia y modelo oficial (21-nov-2026)

Cada familia se corrió con la variante `ndvi_prithvi` y la familia fija (`scripts/semaforo_clasificador.py ndvi_prithvi <familia>`): mismas particiones, selección de hiperparámetros y de Prithvi en validación dentro de la familia, una medición por punto de la prueba. Curvas ROC: `roc_familias.csv` y `roc_auc.csv` (`scripts/roc_semaforo_familias.py`).

| Familia | AUC-ROC prueba [IC 95 %] | Rojos detectados (de 17) | AUC validación (modelo de uso) |
|---|---|---|---|
| **ARIMAX** | **0,75 [0,68–0,81]** | **12** | 0,774 |
| Random Forest | 0,74 [0,67–0,80] | 7 | 0,773 |
| XGBoost | 0,73 [0,65–0,79] | 6 | 0,764 |
| Gradient Boosting (+ Prithvi) | 0,71 [0,62–0,79] | 4 | 0,777 |
| Regresión logística | 0,68 [0,60–0,75] | 7 | 0,717 |

**Modelo B oficial: ARIMAX(1), sin Prithvi.** En validación empata con Gradient Boosting + Prithvi (0,774 frente a 0,777); por la regla de empate gana el más simple. En la prueba sellada es el de mejor AUC y el que más casos de humedad baja detecta.

- **Qué haría falta para que Prithvi aporte en B:** salidas de Prithvi por pixel (30 m, con un decodificador), promediadas dentro de cada franja, no un vector por bloque de 480 m. Un parche de Prithvi (16×16 px HLS = 480 m) es más grande que una franja. Eso requiere volver a correr Prithvi.
