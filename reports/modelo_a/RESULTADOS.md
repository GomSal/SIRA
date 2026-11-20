# Modelo predictor (A): resultados

**Qué hace:** pronostica el **NDVI y el NDMI de cada pixel de 10 m de 1 a 30 días adelante**, en la escala de Sentinel-2 que usa el semáforo (B).

**Cómo se reproduce:**
1. `cloud/modelo_a/sira_a_1_datos.ipynb` (Kaggle, CPU, ~13 min).
2. `cloud/modelo_a/sira_a_2_entrenar.ipynb` (Kaggle, GPU T4, 103 min), con la salida del notebook 1 como entrada.
3. GEOS-S2S aparte: `scripts/modelo_a/bajar_geos.py` → `data/processed/modelo_a/geos/`.

**Resultados:** `reports/modelo_a/entrenamiento_final/`. La versión v1 queda como historial.

## Datos y los 8 pasos

1. **Exploración y datos:**
   - 1 412 imágenes de Sentinel-2 y Landsat 7/8/9 (nov-2012 → nov-2026), 0 fallos;
   - ventana de 5 × 5 km alrededor de Nataima: 250 000 pixeles de 10 m, de los cuales 143 335 son agrícolas;
   - clima CHIRPS y NASA POWER (FAO-56), y ONI.
2. **Calidad:**
   - nubes y sombras por pixel;
   - regla de bruma (se descarta el 9,4 %);
   - rayas de Landsat 7;
   - calibración Landsat → Sentinel-2 (R² 0,66–0,84);
   - siembras detectadas por la curva del NDVI. Se comprobaron con las 3 de Nataima: error mediano de −7, +10 y −2 días.
3. **Partición cronológica, nunca al azar:**
   - **historia:** 10 años, elegidos con una prueba de significancia (`reports/modelo_a/anios/`);
   - **validación:** 3 años con ventana deslizante (nov-2022 → nov-2025);
   - **prueba sellada:** nov-2025 → sep-2026, medida una vez.
4. **Métricas por horizonte** (1, 3, 7, 15 y 30 días): RMSE, MAE y sesgo; skill = 1 − RMSE / RMSE de la línea base (equivale a 1 − U de Theil) frente a **persistencia** y **climatología**; IC 95 % por bootstrap de bloques de 10 días.
5. **Estandarización:** ajustada solo con el entrenamiento de cada partición.
6. **Selección de variables:** hacia adelante por grupos. Un grupo entra si el IC 95 % de su mejora excluye el 0, y se confirma con **Diebold–Mariano** (Newey–West + HLN).
   - **Entran:** fenología (días desde siembra, etapa, época del año) y valores recientes del índice.
   - **No entran:** lluvia, ETo, ONI ni Prithvi. Su mejora tiene IC que incluye el 0, y el ONI empeora. En la ronda 1, DM da p = 0,017 a la lluvia en el NDVI, pero su IC incluye el 0 y la fenología mejora más. En las rondas siguientes, p = 0,47 y 0,77.
   - **Curva de aprendizaje:** bastan 750 pixeles.
7. **Modelos elegidos en validación:** ARIMAX, Random Forest, Gradient Boosting (sklearn por histogramas), XGBoost y una GRU pequeña.
   - Random Forest, Gradient Boosting, XGBoost y la GRU empatan; ARIMAX pierde (skill 0,07 contra 0,19–0,20).
   - Por la regla de empate gana el más simple de los que empatan: **Random Forest** (200 árboles, muestra del 10 % por árbol), en NDVI y en NDMI.
8. **Evaluación:** ver abajo.

## Prueba sellada (nov-2025 → sep-2026, una sola vez)

| Horizonte | NDVI: RMSE | NDVI: skill frente a persistencia | NDMI: RMSE | NDMI: skill frente a persistencia |
|---|---|---|---|---|
| 1 día | 0,083 | +0,10 [0,05–0,14] | 0,063 | +0,02 [−0,09–0,13] |
| 3 días | 0,083 | +0,19 [0,14–0,24] | 0,064 | +0,21 [0,10–0,27] |
| 7 días | 0,097 | +0,21 [0,17–0,25] | 0,077 | +0,20 [0,15–0,24] |
| 15 días | 0,119 | +0,27 [0,23–0,31] | 0,094 | +0,28 [0,22–0,32] |
| 30 días | 0,146 | +0,32 [0,27–0,36] | 0,111 | +0,33 [0,27–0,38] |
| **Todos** | **0,120** | **+0,28 [0,24–0,31]** | **0,093** | **+0,29 [0,24–0,33]** |

- **Frente a la climatología:** skill +0,41 en NDVI y +0,41 en NDMI.
- **Sesgo:** cerca de 0 (≤ 0,014).
- **Resumen:** el modelo le gana a las dos líneas base en todos los horizontes, salvo el NDMI a 1 día, que empata con la persistencia.

## Clima observado frente a GEOS-S2S (pronóstico real de la NASA)

- **En A no hay diferencia:** las filas con pronóstico (mar → sep-2026, 107 670 filas, 15 miembros) tienen el mismo error con los dos climas. El modelo no usa clima (paso 6), así que el pronóstico del clima no lo degrada.
- **El pronóstico del clima sí importa en B.** Error en sumas de 10 días durante 2026:

| | Cruda | Corregida |
|---|---|---|
| Lluvia, sesgo | +24 a +29 mm | +18 a +19 mm |
| Lluvia, MAE | 28 a 32 mm | 22 a 24 mm |
| ETo, sesgo | −11 mm | −6 mm |

La corrección con la climatología de los hindcasts (Drift) ayuda, pero GEOS-S2S sigue sobreestimando la lluvia.

## Semáforo B hacia adelante (contra el TDR de Nataima 2021–22)

| NDVI que recibe B | AUC macro | Recall rojo |
|---|---|---|
| Observado | 0,77 | 0,77 |
| Pronosticado a 1–2 días | 0,74 | 0,67 |
| Pronosticado a 3–5 días | 0,71 | 0,75 |
| Pronosticado a 6–10 días | 0,74 | 0,86 |
| Pronosticado a 11–20 días | 0,76 | 0,84 |
| Pronosticado a 21–30 días | 0,75 | 0,94 |

El semáforo apenas se degrada cuando recibe el NDVI pronosticado.

## Prithvi (diseño del informe §8)

**Relleno de huecos con nubes falsas** (79 fechas HLS, 3 nubes por fecha):

| Método | RMSE NDVI | RMSE NDMI | Datos que usa |
|---|---|---|---|
| Interpolación temporal | 0,079 | 0,063 | pasado y futuro |
| **Prithvi** | **0,093** | **0,077** | solo el pasado |
| Último valor despejado | 0,110 | 0,094 | solo el pasado |
| Vecino espacial | 0,245 | 0,210 | mismo día |

- **Para rellenar la historia** gana la interpolación, porque usa también la fecha siguiente.
- **En operación, cuando hoy está nublado y no hay futuro**, gana Prithvi frente al último valor despejado.

**Como variable del pronóstico** no entra (p de DM entre 0,08 y 0,98). Además, HLS solo existe desde 2017–2018.

## Spark

No se usa. Lo decide la ecuación de costo (`docs/arquitectura_produccion_predictor.md` §5.5): la preparación de datos toma ~1 min y cabe en memoria. **No hubo medición pandas frente a Spark** (no existe `spark_vs_pandas.csv`): se decidió con la ecuación, con el OK del usuario.

## Infraestructura

- Todo corrió en Kaggle y en local, con US$ 0 en AWS.
- Verificado al cierre: **0 instancias y 0 volúmenes** en us-west-2.
- Los modelos de uso (`predictor_ndvi.joblib` y `predictor_ndmi.joblib`, ~90 MB cada uno) están en la salida de Kaggle (`sira-predictor-2`). En el repo va `models/pronosticador_a/meta.json`: variables, versiones y error esperado.
