# Semáforo de humedad: un solo clasificador de 3 clases

Script: `scripts/semaforo_clasificador.py`. Datos: GS1 + GS2 + GS3 como un solo cultivo, 198 mediciones TDR.

**Clases** (umbrales del artículo: capacidad de campo ≈ 30 %; déficit crítico 4,34 mm en 150 mm de raíz):

| Clase | Humedad | Mediciones |
|---|---|---|
| verde | ≥ 30 % | 139 |
| amarillo | 27,1–30 % | 42 |
| rojo | < 27,1 % | 17 |

**Separación:**
- **Prueba:** 4 particiones por bloques de 10 días. Cada una queda **sellada** mientras se elige el modelo.
- **Validación:** dentro de las otras 3, validación cruzada interna por bloques (5 particiones). Elige modelo e hiperparámetros por el **mayor AUC-ROC macro**, criterio fijado antes de ver resultados.
- **Medición:** el ganador se reentrena con entrenamiento + validación y se mide **una vez** en su prueba. Cada medición se prueba exactamente una vez.

**Candidatos:** regresión logística, Random Forest, Gradient Boosting, XGBoost y ARIMAX. ARIMAX es la humedad diaria con errores AR y entradas exógenas: balance S y Dr, riego, ETo y NDVI. Como en la app no hay TDR, predice solo con las entradas.

## Selección en validación (mejor AUC macro por familia)

| Familia | Prueba 1 | Prueba 2 | Prueba 3 | Prueba 4 | Promedio |
|---|---|---|---|---|---|
| **ARIMAX** | 0,76 | 0,77 | 0,81 | 0,75 | **0,77** |
| XGBoost | 0,72 | 0,82 | 0,79 | 0,74 | 0,77 |
| Random Forest | 0,71 | 0,82 | 0,80 | 0,71 | 0,76 |
| Gradient Boosting | 0,71 | 0,82 | 0,78 | 0,73 | 0,76 |
| Regresión logística | 0,66 | 0,72 | 0,76 | 0,70 | 0,71 |

**Ganador:** ARIMAX en 3 de las 4 particiones (Gradient Boosting en la otra). También gana en la selección con todos los datos, que da el **modelo de uso: ARIMAX(1)**.

## Prueba sellada (198 mediciones, cada una una vez)

| Métrica | Verde | Amarillo | Rojo |
|---|---|---|---|
| AUC-ROC | 0,77 [0,69–0,84] | 0,73 [0,67–0,80] | 0,74 [0,62–0,85] |
| Recall | 0,66 [0,58–0,75] | **0,29 [0,17–0,41]** | **0,65 [0,37–0,88]** |
| Precisión | 0,86 [0,80–0,92] | 0,32 [0,22–0,41] | 0,21 [0,09–0,35] |

- **AUC-ROC macro: 0,75 [0,68–0,81].**
- **Exactitud balanceada: 0,53** (el azar da 0,33).

**Matriz de confusión** (filas = real, columnas = predicho):

| | verde | amarillo | rojo |
|---|---|---|---|
| verde | 92 | 23 | 24 |
| amarillo | 12 | 12 | 18 |
| rojo | 3 | 3 | 11 |

## Lectura

- **Rojo:** detecta 11 de 17 déficits (65 %). Hay 3 alertas falsas por cada acierto.
- **Amarillo es la clase débil:** solo acierta el 29 %. La franja amarilla mide 2,9 puntos de humedad, y el error típico del modelo es de unos 3,4 puntos, así que se confunde con verde y con rojo.
- **Verde:** cuando el modelo dice verde, acierta el 86 % de las veces.
- **ARIMAX gana en validación por poco** frente a XGBoost, Random Forest y Gradient Boosting (0,77 contra 0,76–0,77). La regresión logística queda atrás.
- **Nota:** en una de las particiones el ajuste de ARIMAX no terminó de converger (aviso de statsmodels).
