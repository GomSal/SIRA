# Modelo único de humedad del suelo (GS1 + GS2 + GS3 como un solo cultivo)

Script: `scripts/modelo_humedad_unificado.py` (local, 18-nov-2026).

**Planteamiento:**
- **Un solo cultivo:** las tres temporadas se alinean por días desde siembra y por etapa; la etapa vegetativa de las tres es una sola, y lo mismo las demás.
- **Respuesta:** humedad del suelo medida con el TDR, las 198 mediciones reales.
- **Entradas, agua + clima entran → planta → agua sale:**
  - riego y lluvia CHIRPS (acumulados de 3, 5 y 10 días; días desde el último evento);
  - clima: ETo, temperatura, radiación y humedad del aire;
  - planta: NDVI de Sentinel-2 y Kc = 1,0·NDVI + 0,45;
  - balance diario: reservorio S con τ = 2, 5, 10 y 20 días, y déficit Dr FAO-56;
  - días desde siembra y etapa.

**Validación:**
- **Principal:** bloques de 10 días. Se esconde un tramo del cultivo, con las 3 franjas juntas, y se predice con el resto de las 3 temporadas.
- **Además:** una temporada entera afuera.
- **Hiperparámetros:** se ajustan solo dentro del entrenamiento.

## Resultado (bloques de 10 días)

| Entradas | Modelo | RMSE (% vol) | Línea base (media) | R² | r |
|---|---|---|---|---|---|
| Completo | Balance físico (recta) | 3,45 | 4,08 | 0,27 | 0,53 |
| Completo | Random Forest | **3,39** | 4,08 | 0,29 | 0,55 |
| Completo | XGBoost | 3,59 | 4,08 | 0,21 | 0,51 |
| Completo | Ridge | 3,58 | 4,08 | 0,21 | 0,52 |
| Sin riego | el mejor (Ridge) | 4,09 | 4,08 | −0,03 | — |
| Sin satélite (Kc de la tabla FAO) | Random Forest | 3,44 | 4,08 | 0,27 | 0,53 |
| Sin las columnas de clima (la ETo sigue en el balance) | XGBoost | 3,28 | 4,08 | 0,34 | 0,60 |

**Con una temporada entera afuera:**
- completo: 3,60 a 3,75, frente a 4,34 de la base;
- sin riego: peor que la media.

**Variables más importantes (XGBoost, permutación):**
1. el reservorio S con τ = 20 días (0,52);
2. el riego del día y de los últimos 5 y 10 días;
3. con menos peso: días desde siembra, ETo de 5 días y NDVI.

## Semáforo

De las 198 mediciones, 137 están en exceso (> 30 %, lote inundado), 44 en óptimo y 17 en déficit (< 27,1 %). Todas las de déficit son de AWD+MIRI, en sus fases de secado.

- **Como clase exacta no sirve:** ningún modelo detecta los 17 déficits (0 de 17). Decir siempre "exceso" acierta el 69 %, y los modelos aciertan el 66–67 %.
- **Como puntaje de riesgo sí sirve.** El modelo ordena bien los días que se están secando:
  - el área bajo la curva ROC es **0,75–0,79** para "por debajo de capacidad de campo" (< 30 %), y 0,70–0,76 para déficit;
  - con una temporada afuera, 0,73–0,79.

## Lectura

1. **Sí se puede estimar la humedad con un solo modelo, si se conoce el riego.** El error baja de 4,1 a 3,3–3,5 puntos de humedad. Explica alrededor del 30 % de la variación.
2. **El núcleo es el balance de agua** (riego + lluvia − Kc·ETo, acumulado en unos 20 días). Random Forest y XGBoost lo mejoran poco.
3. **Sin el registro de riego no hay estimación.** La app tiene que registrar los riegos del agricultor.
4. **El NDVI aporta poco frente a la tabla FAO** (≈ 0,05 puntos). Las columnas de clima, aparte de la ETo que ya entra en el balance, no ayudan.
5. **Para la app:** mostrar un **riesgo de secado** (bajo, medio, alto) a partir del puntaje, no un porcentaje exacto. Falta fijar el umbral de alerta dentro de la validación y medir aciertos y falsas alarmas.
