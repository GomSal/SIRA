# Handover: entrenamiento del Modelo A (Pronosticador de NDVI + NDMI)

**Para:** un chat nuevo cuya **única tarea** es entrenar el Modelo A y comprobar si una arquitectura batch con **Spark** abarata el proceso.
**Después:** los resultados se verifican en el chat original. Este chat **no** edita `docs/informe_preliminar.md` ni `HANDOVER.md`.
**Fecha:** 18-nov-2026. Entrega del proyecto: 23-nov-2026.

---

## 0. Reglas de trabajo con el usuario (aprendidas en este proyecto)

- **Comunicación:** español, respuestas **cortas y claras**, sin jerga interna. Si algo importante cambia, se dice primero y en una frase.
- **No quitar ni simplificar nada del diseño sin argumento y sin el OK del usuario.** Tampoco descartar datos reales porque son imperfectos: el usuario lo rechaza, y con razón.
- **No afirmar nada sin verificarlo:** con el dato, el archivo o la fuente.
- **Metodología del curso, 8 pasos obligatorios:**
  1. Análisis exploratorio
  2. Calidad de datos
  3. Partición en entrenamiento, validación y prueba
  4. Definición de métricas
  5. Estandarización
  6. Selección de variables (reducción de dimensionalidad)
  7. Ajuste y selección de modelos
  8. Evaluación

  **El modelo se elige en validación; la prueba queda sellada y se mide una vez.**
- **Comparar varios modelos** (ver §4) y reportar siempre frente a líneas base simples.
- **Antes de lanzar AWS:** decir costo y tiempo, y **esperar el OK explícito** del usuario.
- **Commits:** en español, **sin** líneas `Co-Authored-By` de Claude o Anthropic. No subir `data/raw/*.pdf` ni `data/s3/`.

## 1. Dónde encaja A (leer primero)

**Estado actual del Modelo B, el semáforo de humedad del suelo:**
- **Código y resultados:** `scripts/semaforo_clasificador.py`, `reports/semaforo_clasificador/RESULTADOS.md` y `reports/semaforo_clasificador_ndvi_ndmi/RESULTADOS.md`.
- **Clases**, con umbrales del artículo Martínez-Vega et al. 2025:
  - verde ≥ 30 % de humedad;
  - amarillo 27,1–30 %;
  - rojo < 27,1 %.
- **Modelo elegido en validación:** **ARIMAX**. Prueba sellada: AUC-ROC macro 0,75–0,76 y recall rojo 0,65–0,71.
- **Entradas de B, por franja o pixel y por día:**
  - riego del agricultor;
  - lluvia CHIRPS;
  - ETo FAO-56 con NASA POWER;
  - **NDVI** (y NDMI en la variante) de Sentinel-2;
  - balance de agua S(t) = S(t−1)·e^(−1/τ) + lluvia + riego − Kc·ETo, con Kc = 1,0·NDVI + 0,45, y déficit Dr FAO-56;
  - días desde siembra y etapa.

  Tabla de entrenamiento: `reports/humedad_unificada/tabla_entrenamiento*.csv`. Cómo se construye: `scripts/modelo_humedad_unificado.py`.

**Qué tiene que dar A, para que B corra hacia adelante:**
1. **NDVI y NDMI de cada pixel** de 1 a 30 días adelante, en la escala del NDVI y NDMI de Sentinel-2 que usa B.
2. **Clima de los próximos días:** lluvia y ETo, propagados con el pronóstico **GEOS-S2S** de la NASA (GMAO).
3. Con eso, B da el **semáforo futuro por pixel**: "si no riegas, en N días pasas a amarillo o rojo". El riego futuro entra como escenario, no se pronostica.

**Leer también:**
- `docs/informe_preliminar.md` §8: diseño previo de A, con relleno de nubes con Prithvi validado con nubes falsas y evaluación en tres niveles. **Se mantiene salvo que el usuario decida otra cosa.** El nivel "pasado por el Corrector" ahora es "pasado por el semáforo B".
- `docs/HANDOVER_modelo_B.md` §6 y `cloud/modelo_b/lanzar.py`: patrón EC2 con salvaguardas, ya probado en esta cuenta.

## 2. Datos (el histórico más grande posible)

| Fuente | Qué | Desde | Notas |
|---|---|---|---|
| **NASA POWER** (diario) | T máx y mín, humedad del aire, viento, radiación → ETo FAO-56; precipitación | Verificar (meteorología desde 1981; radiación desde 1984) | Ya hay código: `cloud/ronda1_b/job_b.py` (E2) |
| CHIRPS (diario) | Lluvia, la elegida para B (`reports/comparacion_lluvia.md`) | 1981 | No es NASA (UCSB/USGS); se usa porque B se entrenó con ella. Comparar con la precipitación de POWER |
| **GEOS-S2S** (NASA GMAO) | Pronóstico subestacional (semanas a meses): precipitación, temperatura, humedad, viento, radiación | Verificar los **hindcasts** disponibles | **Verificar acceso, formato, resolución y plazos antes de diseñar.** Hacen falta los hindcasts para medir su error y corregir su sesgo frente a POWER y CHIRPS en el sitio |
| Sentinel-2 L2A | NDVI (B08, B04, 10 m) y NDMI (B8A, B11, 20 m) por pixel | ~2017 en la zona | Planetary Computer, sin cuenta (`planetary_computer.sign`). Misma QC que B: máscara SCL + control de bruma (§4 del informe) |
| Landsat 7/8/9 C2 L2 / HLS (NASA) | NDVI y NDMI a 30 m | Landsat 8 desde 2013 (HLS L30 desde 2013) | Para alargar la historia: **calibrar a la escala de Sentinel-2** con fechas coincidentes (ya hay pares en `data/processed/modelo_b/pixeles_lote_s2_landsat.csv`) |

**Área:**
- El lote de Nataima (lon −74,9621, lat 4,1895) **más los lotes de arroz vecinos** de la meseta de Ibagué y el Espinal, en una ventana de varios km.
- A **no necesita datos de campo**: la respuesta correcta es la imagen que el satélite tomó después.
- Hay que identificar los pixeles de arroz y su fecha de siembra por la curva del NDVI. Ya se validó que el NDVI reconstruye las 3 siembras del artículo: GS1 2021-05-27, GS2 2021-12-04, GS3 2022-07-09.

## 3. Los 8 pasos aplicados a A

1. **Exploración:** fechas útiles por año y sensor (nubes de abril a junio); curvas de NDVI y NDMI por ciclo; relación con lluvia y ETo.
2. **Calidad:**
   - máscara de nubes por pixel y control de bruma;
   - relleno de huecos según §8 del informe (Prithvi frente a interpolación, validado con nubes falsas);
   - marcar cada valor como observado o estimado.
3. **Partición cronológica con ventana deslizante** (rolling origin):
   - entrenar con el pasado y validar con el bloque siguiente, avanzando en el tiempo;
   - **prueba sellada = el periodo más reciente**, que incluye las temporadas de Nataima 2021–2022 si quedan al final, o el último año disponible;
   - **nunca separar al azar.**
4. **Métricas, por horizonte** (1, 3, 7, 15 y 30 días):
   - RMSE y MAE del NDVI y del NDMI;
   - **skill frente a dos líneas base obligatorias:** persistencia (el último valor observado) y climatología (la curva típica por días desde siembra);
   - además, el acierto del semáforo de B usando el NDVI pronosticado frente al NDVI observado.
5. **Estandarización:** ajustada solo con el entrenamiento de cada ventana.
6. **Selección de variables:** rezagos del índice, días desde siembra, lluvia y ETo acumuladas (observadas en entrenamiento; las de GEOS-S2S en operación). Usar reducción de dimensionalidad (PCA si entra Prithvi).
   **Regla:** una variable entra solo si el IC 95 % de su mejora en validación excluye el 0. Así se evita el sobreajuste a la validación que ya pasó con B (ver `reports/modelo_b/RESULTADOS.md`).
7. **Modelos candidatos:**
   - **ARIMAX** (el que ganó en B);
   - Random Forest;
   - XGBoost;
   - Gradient Boosting;
   - una red recurrente pequeña (LSTM o GRU) si el volumen lo permite;
   - las dos líneas base.

   Se elige en validación por skill medio en los horizontes; en empate, el más simple.
8. **Evaluación:**
   - prueba sellada, una vez;
   - IC por bootstrap de bloques de tiempo;
   - error por horizonte y por etapa;
   - **dos escenarios de clima:** (a) con el clima observado (techo) y (b) con el pronóstico GEOS-S2S de entonces (hindcast), que es lo realista.

## 4. Spark: ¿abarata el proceso? (comprobarlo, no suponerlo)

- **Qué paralelizar:** extracción de las series por pixel (miles a millones de pixel·fecha), QC, relleno, calibración Landsat → S2, cruce con clima y construcción de ejemplos por horizonte.
- **Comparación obligatoria, mismo pipeline y mismos datos:**
  - (a) Python / pandas en una sola máquina;
  - (b) **PySpark** (local[*] en una EC2 y, si se justifica, EMR Serverless o un cluster pequeño).
- **Qué medir:** tiempo total, costo en US$ (precio on-demand vigente) y memoria pico, a 2–3 tamaños de área (un lote, 1 km², la ventana completa).
- **Reportar con honestidad:** si Spark no abarata a este tamaño, se dice. Igual queda la evidencia de procesamiento batch con Spark que pide el curso. Kafka y Kinesis no se usan.
- **Almacenamiento:**
  - S3, bucket `sira-809411919665-usw2` (us-west-2), capas bronce / plata / oro, en Parquet particionado por fecha;
  - evidencia en el repo.

## 5. Infraestructura

- **Cuenta y región:** perfil AWS por defecto = usuario IAM `hyperdev` (cuenta 809411919665), región `us-west-2`.
- **Salvaguardas:** las de `docs/HANDOVER_modelo_B.md` §6, reutilizando `cloud/modelo_b/lanzar.py`:
  - tope duro con terminación automática;
  - topes por etapa;
  - QA en la máquina con proyección de tiempo antes de la corrida larga;
  - vigilante de progreso;
  - `trap EXIT` que sube log y estado;
  - URLs prefirmadas insertadas con Python y probadas;
  - 0 bytes CR;
  - 0 instancias y 0 volúmenes antes y después.
- **Antes de cada lanzamiento:** costo y tiempo, y OK del usuario.
- **Venv local:** `C:\Users\santi\SIRA\.venv` (Python 3.11; instalar con `pip install --only-binary=:all:`). statsmodels ya está.

## 6. Qué dejar en el repositorio

- `cloud/modelo_a/`: lanzador, job, requirements y pipeline Spark.
- `scripts/` para lo local.
- `reports/modelo_a/`:
  - exploración;
  - calidad;
  - particiones;
  - validación;
  - selección;
  - prueba por horizonte;
  - comparación con las líneas base;
  - semáforo con NDVI pronosticado;
  - `spark_vs_pandas.csv` (tiempo, costo y memoria por tamaño);
  - `RESULTADOS.md` corto y en español claro.
- `models/pronosticador_a/`: modelo de uso + meta (variables, versiones, error esperado por horizonte).
- **Git:** rama `modelo-humedad`, remoto `origin` (GomSal/SIRA).

## 7. Terminado cuando

- [ ] A está entrenado con los 8 pasos, elegido en validación y medido una vez en la prueba sellada, con el error por horizonte frente a persistencia y climatología.
- [ ] Está medido con el clima observado y con GEOS-S2S (hindcast).
- [ ] El semáforo de B corre hacia adelante con el NDVI y el clima pronosticados, y se mide cuánto se degrada frente al NDVI observado.
- [ ] Spark frente a pandas está medido en tiempo y costo, con la conclusión dicha sin adornos.
- [ ] Hay 0 instancias y 0 volúmenes al final. Todo está subido.
- [ ] Mensaje final al usuario: resultado en 3 o 4 líneas y el commit. Se verifica en el chat original.
