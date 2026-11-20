# Modelo predictor (A): costo con 10 años de historia, paso por paso

**Decisión de partida (usuario, 19-nov):** 10 años de historia. Viene de `reports/modelo_a/anios/`: es el L más corto donde NDVI y NDMI no se distinguen del mejor, con p ≈ 0,16 en los dos.

**Qué datos hacen falta:**
- **El modelo final** se entrena con nov-2015 → nov-2025.
- **Las 3 particiones de validación** (años nov-2022 → nov-2025) usan cada una sus 10 años previos. Por eso se baja **desde nov-2012**.
- Son unas **1 410 imágenes**: 767 fechas de Sentinel-2 y unas 643 escenas de Landsat 7/8/9.

**Área:** ventana de **5 × 5 km** alrededor de la finca, a 10 m (250 000 pixeles).
- El estudio de años mostró que **1 500 pixeles de 30 m ya dan 1,3 millones de ejemplos por partición**.
- La tesela completa de Sentinel-2 (110 × 110 km) obligaría a bajar 652 GB y no hace falta.

**Base de los tiempos:** todos salen de mediciones (`bench_*.json`, `anios_log.txt`, `reports/modelo_b/prithvi_info.json`) o de extrapolaciones lineales indicadas.

## Paso por paso

| Paso | Qué se hace | Operaciones | Tiempo en este PC (16 hilos) | US$ |
|---|---|---|---|---|
| **0. Bajar datos** | 1 410 imágenes en la ventana (≈ 7 GB) + HLS para Prithvi (≈ 940 recortes de 224 × 224) + GEOS-S2S por punto (74 inicios × 5 miembros × 3 variables) + clima (listo) | lectura por bloques de COG; GEOS-S2S ≈ 30 s por archivo (medido) | ≈ 25 min + 20 min + 35 min | 0 |
| **1. Exploración** | fechas útiles por año, sensor y mes; curvas por ciclo; relación con lluvia y ETo | 3,5 × 10⁸ pixel·fecha | minutos | 0 |
| **2. Calidad** | ver §2 | 160 ns por pixel·fecha (medido) ≈ 1 min; Prithvi ≈ 1 840 inferencias | ≈ 1 h | 0 |
| **3. Partición** | ventana deslizante (ver §3) | — | — | 0 |
| **4. Métricas** | ver §4 | — | — | 0 |
| **5. Estandarización** | z-score ajustado solo con el entrenamiento de cada partición (para GRU y ARIMAX; los árboles no lo necesitan) | lineal | segundos | 0 |
| **6. Selección de variables** | grupos (rezagos, días desde siembra, lluvia, ETo, día del año, sensor, Prithvi con PCA); entra un grupo solo si su mejora en validación tiene IC 95 % que excluye el 0; incluye la curva de aprendizaje que fija N | ≈ 200 ajustes de XGBoost de 14 s (medido a 1,3 M filas) | ≈ 1 h | 0 |
| **7. Ajuste y selección** | ARIMAX, Random Forest, XGBoost, Gradient Boosting y GRU; 3 particiones × NDVI y NDMI | ver tabla de abajo | ≈ 6–7 h | 0 |
| **8. Evaluación** | prueba sellada una vez; bootstrap de bloques; por horizonte y etapa; clima observado frente a GEOS-S2S corregido; semáforo B hacia adelante | ≈ 2 núcleo-hora | ≈ 15 min | 0 |
| **Spark frente a pandas (§4 del handover)** | mismo pipeline (pasos 0–2 y ejemplos) a 3 tamaños: lote, 1 km² y 5 × 5 km, en EC2 c7i.4xlarge | — | ≈ 2 h en EC2 | **≈ 1,5** |

**Paso 7 en detalle**, con N ≈ 1,3 M ejemplos por partición:

| Modelo | Configuraciones | Ajustes | Tiempo por ajuste | Núcleo-hora |
|---|---|---|---|---|
| XGBoost | 8 | 48 | 14 s medido (16 hilos) | 3 |
| Random Forest | 6 | 36 | ≈ 400 s (16 hilos) | 64 |
| Gradient Boosting (sklearn) | 6 | 36 | ≈ 2 900 s (1 hilo) | 29 |
| GRU pequeña (las dos salidas juntas) | 4 | 12 | ≈ 210 s (16 hilos) | 11 |
| ARIMAX (1 500 series de pixel) | 2 | 6 × 1 500 | 0,44 s por serie | 1 |
| **Total del paso 7** | | | | **≈ 108** |

**Total de todo:** ≈ 122 núcleo-hora.

| Dónde | Tiempo | Costo |
|---|---|---|
| Este PC | ≈ 9–10 h | US$ 0 |
| EC2 c7i.4xlarge | ≈ 8 h | ≈ US$ 5,5 |
| Kaggle CPU (4 núcleos) | ≈ 31 h, unas 3 sesiones | US$ 0 |

## 2. Calidad: cómo se depuran los datos malos

1. **Nubes y sombras por pixel:**
   - Sentinel-2: clase SCL ∈ {vegetación, suelo, agua};
   - Landsat: bits de QA_PIXEL (relleno, nube dilatada, cirro, nube, sombra) = 0.
2. **Bruma** (regla del informe §4): se descarta la fecha si el NDVI cae más de 0,10 frente a la fecha anterior y a la siguiente, o si el azul es ≥ 0,075 en plena temporada.
3. **Rangos físicos:** reflectancias > 0 (esto quita el bandeado de Landsat 7 SLC-off) e índices en (−1, 1).
4. **Calibración Landsat → Sentinel-2:** recta por plataforma con pares del mismo pixel a ≤ 2 días.
   - L8: R² 0,65 en NDVI y 0,78 en NDMI.
   - L7 y L9: R² 0,54–0,55 en NDVI y 0,75 en NDMI.
5. **Huecos (Prithvi):** **no hacen falta para entrenar A**, porque los ejemplos son pares de fechas despejadas. Sí hacen falta en operación (cuando la última imagen está nublada) y para B (NDVI diario).
   - Se valida como dice el informe §8: Prithvi frente a interpolación temporal frente a vecinos espaciales, con nubes falsas sobre fechas despejadas.
   - Prithvi en CPU: 0,19 s por recorte (medido en el Modelo B).
   - **Límite:** HLS en Planetary Computer existe solo desde ago-2017 (L30) y dic-2018 (S30). Para 2015–2018 el relleno solo puede ser temporal.
6. **Marca por valor:** observado o estimado.

## 3. Partición

- **Prueba sellada:** nov-2025 → nov-2026.
  - El modelo se entrena una vez con nov-2015 → nov-2025 y se mide una sola vez.
  - CHIRPS definitivo llega hasta sep-2026; oct–nov-2026 necesitan CHIRPS preliminar (por verificar).
- **Validación con ventana deslizante:** 3 bloques de un año (nov-2022 → nov-2025). Cada uno se pronostica con un modelo entrenado con sus 10 años previos.
- **Sin fuga de información:** los objetivos del entrenamiento son anteriores al inicio del bloque de validación. Nunca se separa al azar.

## 4. Métricas: qué se mide en un pronosticador

- **A pronostica un número** (el NDVI o NDMI de t+h), no una clase. **AUC-ROC no aplica**: es para clasificadores.
- **Error por horizonte** (1, 3, 7, 15 y 30 días): RMSE, MAE y sesgo.
- **Skill frente a las dos líneas base:** 1 − RMSE / RMSE de la línea base.
  - **Persistencia:** el último valor observado.
  - **Climatología:** la curva típica por días desde la siembra.
  - Skill > 0 significa que el modelo sirve más que no hacer nada.
- **Incertidumbre:** con los 5 miembros de GEOS-S2S sale un rango. Se mide si el rango del 80 % contiene el valor real el 80 % de las veces (cobertura) y el CRPS.
  - Aquí sí importa la "probabilidad", pero como calibración del rango, no como AUC.
- **Efecto en el semáforo B**, que sí es un clasificador: se mide su AUC-ROC macro y su recall de rojo usando el NDVI pronosticado frente al NDVI observado. Eso dice cuánto se degrada el semáforo futuro.
