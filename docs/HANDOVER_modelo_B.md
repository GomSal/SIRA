# Handover: entrenamiento del Modelo B (Corrector satélite → dron)

**Para:** un chat nuevo cuya **única tarea** es entrenar el Modelo B en AWS EC2 y dejar los resultados en el repositorio.
**Después:** los resultados se verifican en el chat original del proyecto. Este chat **no** edita `docs/informe_preliminar.md` ni `HANDOVER.md`.
**Fecha:** 17-nov-2026. Entrega del proyecto: 23-nov-2026.

---

## 0. Reglas de trabajo con el usuario

- Español, respuestas **cortas y claras**, sin jerga interna. Si algo importante cambia, se dice primero y en una frase.
- **No quitar ni simplificar nada del diseño** (clima, riego, Prithvi, índices) sin argumentarlo y sin el OK del usuario.
- No afirmar nada sin verificarlo (con el dato, el archivo o la fuente).
- **Antes de lanzar EC2:** decir costo y tiempo estimados, y **esperar el OK explícito** del usuario.
- Commits **sin** líneas `Co-Authored-By` de Claude o Anthropic.
- No subir `data/raw/martinez_vega_2025.pdf` (marca de agua de la editorial) ni `data/s3/`.
- Calidad de curso real, no una prueba de juguete: lo que pide la metodología, bien hecho.

## 1. Qué leer primero (en este orden)

1. `docs/informe_preliminar.md`, §1 a §6 completos. §6 es **el diseño que hay que implementar**: el flujo de la propuesta ordenado según los 8 pasos del profesor (diagrama en §6.2). También §7 (balance de agua), §11.6 y §11.9 (pruebas de factibilidad del Corrector).
2. `HANDOVER.md` §2: decisiones del proyecto. Prithvi congelado + modelo encima es lo prometido a los jurados, y la comparación con y sin Prithvi es obligatoria.
3. `scripts/corrector_dron_v2.py`: implementación de referencia. De ahí salen la carga de datos, el control de calidad, la referencia del dron y el bootstrap por bloques móviles. **Reutilizar su lógica**, no reinventarla.
4. `cloud/ronda1_b/job_b.py`: cómo se bajaron Sentinel-2 y Landsat de Planetary Computer, cómo se calcularon los índices y el clima (E1, E2).
5. `cloud/ronda1_b/lanzar.py`: patrón de lanzamiento EC2 con salvaguardas, ya probado en esta cuenta. El nuevo lanzador parte de ahí.

## 2. Qué aprende el Modelo B

**Agua + clima entran → la planta responde → sale la recomendación de agua.** El Modelo B es la planta: estima, desde el satélite, el estado del cultivo que mediría el dron. Ese estado alimenta el Kc del balance de agua (§7 del informe).

- **Respuesta:** el índice del dron (Fig. 7 del artículo) en cada franja o píxel y fecha. Regresión, un corrector por índice:
  - **NDVI** y **OSAVI**: Sentinel-2 por franja + Landsat 7/8/9 por píxel de 30 m;
  - **NDRE**: solo Sentinel-2 (Landsat no tiene borde rojo).
- **Entradas:** índices del satélite + Prithvi + clima + riego + días desde siembra (§4).
- Respecto a la propuesta original, **lo único que cambió es la respuesta**: antes era la humedad TDR; ahora es el índice del dron. Las columnas `vwc_*` de `reports/correlacion_ndvi_ndre_datos.csv` **no se usan** como entrada ni como respuesta.

## 3. Datos de entrada

| Archivo | Contenido | Notas |
|---|---|---|
| `reports/correlacion_ndvi_ndre_datos.csv` | Sentinel-2 por franja y fecha: `ndvi`, `ndre`, `das`, `temporada`, `franja` | `franja` = nombre del tratamiento **de GS1** (oeste MIRI, centro AWD+MIRI, este CD). Única fuente de NDRE |
| `data/processed/modelo_b/pixeles_lote_s2_landsat.csv` | Píxeles del lote, S2 (5086 filas) y Landsat (516): `frac_MIRI`, `frac_AWD+MIRI`, `frac_CD`, `frac_lote`, `ndvi`, `ndwi`, `ndmi`, `mndwi`, `str`, `nir`, `swir1`, `swir2` | Copia congelada de la salida E1 de la ronda 1. `frac_*` usan los nombres de GS1 = posición física. Reflectancia en 0–1 |
| `data/processed/modelo_b/clima_dia.csv` | Por día, 2021–2022: `lluvia` (**CHIRPS**), `eto` (FAO-56 con NASA POWER), acumulados de 1, 3, 5 y 10 días, `balance_5d`, `balance_10d`, `T2M_MAX`, `T2M_MIN`, `RH2M`, `WS2M`, `ALLSKY_SFC_SW_DWN` | Copia congelada de E2. **No usar `PRECTOTCORR`** (lluvia de POWER): la decidida es CHIRPS (`reports/comparacion_lluvia.md`) |
| `data/processed/nataima_diario.csv` | Por temporada × día × tratamiento: `riego_mm` (Fig. 4), `lluvia_mm` del artículo | Fuente del riego. `vwc_*` no se usa |
| `data/processed/dron_indices.csv` | Dron: `temporada, etapa, tratamiento, indice, valor`; índices NDVI, NDRE, OSAVI (27 medias por índice) | Digitalizado de la Fig. 7, validado contra la Tabla 4 |
| `reports/dron/revision_gs{1,2,3}.csv` | `ndvi_lote`, `azul_lote` por fecha S2 | Para el control de bruma |
| `reports/dron/corrector_dron_v2.csv` | Resultados de factibilidad (regresión lineal) | **Prueba de no regresión** (§5, paso 2) |
| `data/interim/fechas_despejadas_lote.csv` | Fechas despejadas por colección, incluidas `hls2-s30` y `hls2-l30` | HLS despejadas entre los días 25 y 115: GS1 8, GS2 6, GS3 3 (antes del control de bruma) |
| **HLS (se baja en la corrida)** | Planetary Computer, colecciones `hls2-s30` y `hls2-l30`, sin cuenta (`planetary_computer.sign`) | Para Prithvi (§5, paso 2) |

Constantes del proyecto (ya están en `corrector_dron_v2.py`):
- Lote: lon −74,9621, lat 4,1895. Siembras: GS1 2021-05-27, GS2 2021-12-04, GS3 2022-07-09. Ventana: días 25 a 115 desde la siembra.
- Rotación "oeste" (posición física → tratamiento): GS1 MIRI / AWD+MIRI / CD; GS2 AWD+MIRI / CD / MIRI; GS3 CD / MIRI / AWD+MIRI.
- Día de cada etapa del dron: vegetativa 30, reproductiva 60, maduración 100. Interpolación PCHIP por temporada × tratamiento × índice, **solo para emparejar fechas**.
- Error estándar del dron por etapa (Tabla 4, × √3): diccionario `EE` del script. Para OSAVI no hay EE en la Tabla 4: usar el de NDVI y decirlo.
- Control de calidad: se descarta una fecha si el NDVI medio del lote cae > 0,10 frente a la anterior **y** a la siguiente (serie conjunta S2 + Landsat), o si en S2 el azul del lote es ≥ 0,075 entre los días 25 y 115. HLS: máscara Fmask + el mismo control de bruma.
- Pureza: S2 píxeles ≥ 90 % dentro de una franja; Landsat píxeles con `frac_lote ≥ 0,5`, referencia = mezcla de tratamientos ponderada por área.

**Tamaños esperados tras el control de calidad** (contrato de datos): NDVI 23 fechas y 141 pares; NDRE 14 fechas y 42 pares; OSAVI igual que NDVI. Si no salen, se para y se investiga antes de seguir.

## 4. Entradas candidatas y cómo construirlas

**Índices (la planta vista desde el satélite):**
- S2 por franja: `ndvi`, `ndre` del CSV de correlación. `ndwi`, `ndmi`, `mndwi`, `str` y `osavi` = media de los píxeles S2 con ≥ 90 % en esa franja, misma fecha.
- Landsat por píxel: los mismos, salvo NDRE.
- **OSAVI** = (NIR − rojo) / (NIR + rojo + 0,16). El rojo no está en la tabla, pero se despeja exacto del NDVI: rojo = NIR · (1 − NDVI) / (1 + NDVI) (en `job_b.py` el NDVI usa B08 en S2 y `nir08` en Landsat, igual que la columna `nir`). Verificarlo con 2 o 3 píxeles contra la imagen original.
- `sensor_L` = 1 si Landsat, 0 si S2.
- Cada índice **relativo al lote**: índice − media del lote ese día con ese sensor.

**Clima (agua y demanda que entran; igual para las 3 franjas un mismo día):** `lluvia_1d/3d/5d/10d`, `eto`, `eto_5d/10d`, `balance_5d/10d`, `T2M_MAX`, `ALLSKY_SFC_SW_DWN`.

**Riego (agua que entra):** por franja, el riego del tratamiento que tiene esa franja en esa temporada (rotación "oeste"): acumulado desde la siembra, últimos 5 días y últimos 10 días. En Landsat, mezcla ponderada por área. Nota: el riego distingue tratamientos; en la app solo existe si el agricultor lo reporta. Por eso B1 se evalúa **con y sin riego**.

**Prithvi (estado del lote y su entorno):** componentes PCA de su resumen (paso 2). Un bloque de 480 m cubre todo el lote: un solo vector por fecha, igual para las 3 franjas.

**Tiempo:** `das`.

**Prohibidas:** tratamiento, temporada, posición de la franja, `vwc_*`, `PRECTOTCORR`.

**Variantes** (comparación obligatoria de la propuesta):

| Variante | Entradas candidatas |
|---|---|
| B1 | índices + clima + riego + das |
| B1 sin riego | índices + clima + das |
| B2 | B1 + componentes PCA de Prithvi |
| B3 | solo Prithvi (PCA) + cabeza pequeña |

B2 y B3 solo existen en fechas con HLS. **B1, B2 y B3 se comparan en las mismas filas** (las que tienen HLS); además B1 se reporta con todas sus filas.

## 5. Los 8 pasos, en concreto

Un solo script determinista `cloud/modelo_b/job_modelo_b.py` (semillas fijas), por etapas, con salidas en `--out`. Cada paso escribe sus archivos al terminar.

1. **Análisis exploratorio** → `eda/`: fechas por temporada y sensor (S2, Landsat, HLS); series de tiempo de cada índice por franja junto a la del dron; dispersión satélite vs dron por temporada y sensor; lluvia, ETo y riego por temporada y tratamiento; matriz de correlación de todas las candidatas; diferencias entre tratamientos (dron vs satélite). Figuras PNG + `eda_resumen.csv`.
2. **Calidad y preparación de datos:**
   - control de calidad → `calidad_fechas.csv` (cada fecha y sensor con su motivo de descarte o "ok");
   - índices, clima y riego por fila;
   - **Prithvi:**
     - bajar HLS (`hls2-s30`: B02, B03, B04, B8A, B11, B12; `hls2-l30`: B02, B03, B04, B05, B06, B07; más Fmask) en un recorte de 224 × 224 píxeles de 30 m centrado en el lote, solo en fechas despejadas de cada temporada;
     - correr **Prithvi-EO-2.0-300M-TL congelado** (pesos oficiales de IBM-NASA en Hugging Face; verificar el nombre exacto del repositorio y usar su código y su normalización), con hasta 4 fechas: la del día y las 3 HLS despejadas anteriores de la misma temporada;
     - guardar el vector de 1024 del bloque que contiene el lote, promediado en el tiempo → `prithvi_embeddings.csv` (fecha × 1024, se sube al repo);
   - emparejar con la referencia del dron → `tabla_limpia_{ndvi,ndre,osavi}.csv`. Las filas S2 y Landsat 8/9 reciben el vector Prithvi de su misma fecha HLS (±1 día); Landsat 7 no tiene HLS.
   - **Asserts:** tamaños del §3, sin NaN en la respuesta, ninguna columna prohibida.
   - **No regresión:** la regresión lineal con solo el índice (y `sensor_L` en NDVI) y una temporada afuera debe reproducir el `r` de `corrector_dron_v2.csv` con diferencia ≤ 0,01; si no, se para (`ERROR_NO_REGRESION`).
3. **Partición** → `particiones.csv`: validación cruzada anidada por temporada.
   - Afuera: 1 temporada de prueba sellada.
   - Adentro: entrenar con una y validar con la otra, en los dos sentidos. El RMSE de validación es el promedio de los dos sentidos, ponderado por el número de fechas de validación.
   - Además, la verificación cronológica GS1 → GS2 → GS3.
   - Si un sentido de entrenamiento tiene < 5 fechas (NDRE GS1 tiene 3), se reporta y se marca.
4. **Métricas** (fijas antes de ver resultados):
   - principal: **RMSE con peso igual por fecha** (la fecha es la unidad independiente);
   - reporte: RMSE sin ponderar (comparable con §11.9), MAE, r de Pearson y mejora frente a la línea base (media del entrenamiento);
   - secundaria, solo fechas S2 con las 3 franjas: % de fechas en que acierta cuál franja está más baja, y tau de Kendall medio del orden de las 3.
5. **Estandarización:** `StandardScaler` y el `PCA` de Prithvi (k ∈ {3, 5, 10, 20}) dentro de un `Pipeline` de scikit-learn, ajustados **solo con el entrenamiento** de cada partición.
6. **Selección de variables**, dentro de cada variante:
   - **selección hacia adelante** guiada por el RMSE de validación interna: se empieza con el índice del mismo nombre que la respuesta y se agrega la candidata que más baje el RMSE; se para cuando ninguna lo baja más de 1 % o al llegar a 4 entradas;
   - control: coeficientes que Lasso anula e importancia por permutación en validación → `importancia.csv`, `seleccion_variables.csv`.
7. **Ajuste y selección de modelos.** Rejillas sugeridas, pequeñas por los pocos datos:
   - lineal, sin hiperparámetros;
   - Huber: `epsilon` {1,35; 1,5; 2}, `alpha` {1e-4; 1e-2};
   - Ridge: `alpha` logspace(−3, 3, 13);
   - Lasso: `alpha` logspace(−4, 0, 9);
   - splines aditivos: `SplineTransformer(n_knots ∈ {3, 4, 5}, degree=3)` + Ridge `alpha` {0,1; 1; 10}. Sustituye a pyGAM, que da problemas con numpy y scipy recientes;
   - Random Forest: 500 árboles, `max_depth` {2, 3, 4, None}, `min_samples_leaf` {3, 5, 10}, `max_features` {1,0; "sqrt"};
   - XGBoost: `n_estimators` {100, 300}, `max_depth` {1, 2, 3}, `learning_rate` {0,03; 0,1}, `min_child_weight` {3, 5}, `subsample` 0,8;
   - B3, cabeza pequeña: Ridge, o una red de una capa (`MLPRegressor(hidden_layer_sizes=(16,), alpha ∈ {1e-3; 1e-1})`) sobre la PCA de Prithvi.

   **Regla de selección:**
   - gana el menor RMSE de validación;
   - hay empate cuando el IC 95 % de la diferencia de RMSE de validación entre el mejor y el candidato incluye 0. Ese IC sale de 200 remuestras por bloques móviles de las fechas de validación, sin reentrenar. Si hay empate, se elige el más simple;
   - orden de simplicidad: B1 < B2 < B3; lineal < Huber < Ridge < Lasso < splines < RF < XGBoost; menos variables primero.

   Salidas: `validacion_interna.csv` (todas las combinaciones) y `seleccion.csv` (ganador por temporada de prueba y por qué).
8. **Evaluación:**
   - reentrenar lo elegido con las 2 temporadas y medir **una vez** en la sellada → `prueba.csv`, `predicciones_prueba.csv`;
   - **comparaciones que siempre se reportan**, en la prueba y en las mismas filas: B2 vs B1 (aporte de Prithvi), B3 vs B1, B1 con vs sin riego → `comparacion_variantes.csv`;
   - bootstrap por bloques móviles de 2 fechas dentro de cada temporada + ruido del dron, **1000 repeticiones**, reentrenando la configuración elegida → `bootstrap_IC.csv` (IC 95 % de RMSE, MAE, r y % de repeticiones que superan a la línea base);
   - errores por etapa y por sensor → `errores_por_etapa_sensor.csv`;
   - estabilidad: **200 repeticiones** de la selección completa sobre remuestras → `estabilidad_seleccion.csv` (cuántas veces gana cada variante y modelo);
   - verificación cronológica → `cronologica.csv`;
   - figuras: predicho vs dron por temporada; series por franja con la predicción;
   - modelo de uso: la configuración más elegida, reentrenada con las 3 temporadas → `models/corrector_b/{ndvi,ndre,osavi}.joblib` + `*_meta.json`. El meta lleva variante, variables, hiperparámetros, versiones de librerías, sha256 de cada entrada, commit de git y error esperado (el de las pruebas).

Al final, `RESULTADOS.md` corto y en español claro:
- una tabla por paso;
- por índice: el modelo elegido y sus métricas de prueba con IC, frente a la línea base y frente a §6.11 del informe;
- cuánto aportan Prithvi, el clima y el riego;
- qué no salió bien.

Sin adornos.

## 6. Infraestructura: EC2 on-demand con salvaguardas

**Cuenta y región:**
- perfil AWS por defecto = usuario IAM `hyperdev` (cuenta 809411919665);
- región `us-west-2`, donde están el bucket `sira-809411919665-usw2` y los datos de Planetary Computer;
- prefijo de cada corrida: `modelo_b/<AAAAMMDD-HHMMSS>/`.

**Carpeta nueva `cloud/modelo_b/`:**
- `lanzar.py`, adaptado de `cloud/ronda1_b/lanzar.py`;
- `job_modelo_b.py`;
- `requirements.txt` con versiones fijas;
- `datos/`, con copias de los archivos del §3;
- `ultima_corrida.json`.

**Versiones:**
- las del venv local (`.venv`, Python 3.11): numpy 2.4.6, pandas 3.1.1, scipy 1.17.1, scikit-learn 1.9.1;
- además: xgboost, matplotlib, joblib, rasterio, pystac-client, planetary-computer, pyproj, shapely, torch (CPU), huggingface_hub y lo que pida el código oficial de Prithvi (einops, timm);
- en local instalar con `pip install --only-binary=:all:`;
- en EC2 (Ubuntu 24.04, Python 3.12), **las mismas versiones** desde `requirements.txt`. Torch CPU desde `https://download.pytorch.org/whl/cpu`.

**Máquina sugerida:**
- `c7i.2xlarge` (8 vCPU, 16 GB): Prithvi 300M en CPU con unas 20–30 fechas cabe en minutos. Disco de 40 GB.
- Si la QA mide que Prithvi en CPU pasa de 20 min, cambiar a `g4dn.xlarge` y avisar al usuario del costo.
- Confirmar el precio on-demand vigente antes de pedir el OK. `c7i.2xlarge` cuesta del orden de US$0,36/h; con tope de 120 min, el máximo es ≈ US$0,75, más centavos de S3 y SNS.

### 6.1 Salvaguardas de tiempo (paran y apagan)

1. `shutdown -h +120` al arrancar + `InstanceInitiatedShutdownBehavior=terminate` → a los 120 min la instancia se **termina** pase lo que pase. Disco con `DeleteOnTermination=true`, verificado después de lanzar.
2. Topes por etapa con `timeout`: QA 15 min; Prithvi (bajar HLS + resúmenes) 40 min; entrenamiento y evaluación 50 min. Si una etapa se pasa → estado `TOPE_TIEMPO_<etapa>`.
3. **Estimación antes de la corrida larga:** la QA mide el tiempo de Prithvi en 1 fecha, de 1 repetición de bootstrap y de 1 selección completa, y proyecta el total. Si la proyección supera 90 min → estado `ESTIMADO_EXCEDE` y se apaga sin correr.
4. **Vigilante de progreso:** el job actualiza un archivo `latido` en cada paso, cada fecha de Prithvi y cada 50 repeticiones de bootstrap. Un proceso en segundo plano del user-data revisa cada 60 s: si el latido tiene más de 8 min → estado `ERROR_SIN_PROGRESO`, mata el proceso y se apaga.

### 6.2 Salvaguardas de error (paran y apagan)

1. Sin `set -u`. `trap final EXIT`: ante cualquier final sube log, estado y lo construido (`out.tar.gz`) y ejecuta `shutdown -h now`.
2. El job envuelve cada paso: una excepción → estado `ERROR_PASO<n>`, con el traceback en el log y código de salida ≠ 0.
3. Estados de error específicos:
   - contrato de datos (tamaños, columnas, sin NaN) → `ERROR_DATOS`;
   - no regresión → `ERROR_NO_REGRESION`;
   - Prithvi sin vector para más del 30 % de las fechas HLS despejadas → `ERROR_PRITHVI`;
   - NaN o infinito en métricas o predicciones → `ERROR_NUMERICO`;
   - fallo de pip → `ERROR_PIP`.
4. Log y estado se suben a S3 cada 60 s. Cada paso sube un parcial, y `prithvi_embeddings.csv` se sube apenas existe, para no repetir esa etapa si algo falla después.

### 6.3 Alerta inmediata (3 capas)

1. **Correo (AWS):**
   - tema SNS `sira-alertas` con suscripción por correo. **Pedir al usuario el correo** y avisarle que debe confirmar una vez el mensaje de AWS;
   - regla de EventBridge por corrida: evento "EC2 Instance State-change Notification" con `state ∈ {shutting-down, stopped, terminated}` para el `instance-id` lanzado → SNS. Avisa en el momento en que la máquina se apaga, sea por éxito, error o tope;
   - la política del tema debe permitir publicar a `events.amazonaws.com`;
   - borrar la regla al terminar.
2. **En el chat:**
   - usar la herramienta `Monitor` con un bucle que lee `status.txt` de S3 cada 30 s;
   - el bucle termina cuando el estado es final (`OK`, `ERROR*`, `TOPE*`, `QA_FALLIDO`, `ESTIMADO_EXCEDE`, `TERMINADO_INESPERADO*`) **o** cuando el log no cambia en más de 5 min (máquina colgada o sin red);
   - en ese segundo caso, terminar la instancia desde local con `aws ec2 terminate-instances`.
3. **Al usuario:** apenas termine el `Monitor`, decirle el estado y las últimas 30 líneas del log.

### 6.4 Lista de verificación antes de lanzar (obligatoria)

- [ ] 0 instancias encendidas y 0 volúmenes sueltos en `us-west-2` (y revisar `us-east-1`).
- [ ] La instancia de la ronda 1 (`i-0ce4e8afc042e6081`) está terminada.
- [ ] **QA local** (`python cloud/modelo_b/job_modelo_b.py --qa --out <scratch>`): pasos 1 a 8 con repeticiones reducidas; imprime "QA OK" y pasa la prueba de no regresión.
  - Prithvi en local: con 1 fecha, si torch CPU cabe en el disco local; si no, la QA de la máquina lo cubre y se dice al usuario.
- [ ] Corrida local con repeticiones reducidas (p. ej. 20 de bootstrap y 5 de estabilidad) para estimar el tiempo total.
- [ ] 0 bytes CR en `.py` y `.sh` empaquetados (contados con Python). Los CSV se normalizan a LF al empaquetar.
- [ ] URLs prefirmadas insertadas con Python (nunca sed) y probadas con PUT + lectura de vuelta.
- [ ] Ningún marcador `__...__` sin reemplazar en el user-data.
- [ ] Suscripción SNS confirmada por el usuario y regla de EventBridge creada para la instancia.
- [ ] Memoria y tiempo calculados; costo dicho al usuario; **OK explícito del usuario**.

**Después de la corrida:**
- bajar `out.tar.gz`;
- verificar 0 instancias y 0 volúmenes;
- borrar la regla de EventBridge;
- guardar `ultima_corrida.json`.

## 7. Qué dejar en el repositorio

```
cloud/modelo_b/                 lanzar.py, job_modelo_b.py, requirements.txt, datos/, ultima_corrida.json
reports/modelo_b/               eda/, calidad_fechas.csv, prithvi_embeddings.csv, particiones.csv,
                                validacion_interna.csv, seleccion_variables.csv, seleccion.csv, importancia.csv,
                                prueba.csv, predicciones_prueba.csv, comparacion_variantes.csv, bootstrap_IC.csv,
                                errores_por_etapa_sensor.csv, estabilidad_seleccion.csv, cronologica.csv,
                                figuras, run.log, RESULTADOS.md
models/corrector_b/             ndvi.joblib, ndre.joblib, osavi.joblib, *_meta.json
tests/test_modelo_b.py          contrato de datos + no regresión (corre en segundos)
```

**Git:**
- repositorio `C:\Users\santi\SIRA`, rama `modelo-humedad`, remoto `origin` (GomSal/SIRA);
- commits en español, sin `Co-Authored-By`;
- push a `origin modelo-humedad`.

## 8. Terminado cuando

- [ ] Los 8 pasos corrieron en EC2 con estado `OK`, y las salidas del §7 están en el repo.
- [ ] La prueba de no regresión pasó.
- [ ] Se entrenaron y compararon B1, B1 sin riego, B2 y B3 para NDVI, NDRE y OSAVI.
- [ ] Cada temporada de prueba se usó **una sola vez**, después de la selección. El orden de los eventos queda en `seleccion.csv`.
- [ ] `RESULTADOS.md` dice, por índice:
  - variante y modelo elegidos, y sus variables;
  - métricas de prueba con IC 95 % y mejora frente a la línea base;
  - aporte de Prithvi, clima y riego;
  - estabilidad de la selección;
  - comparación con §6.11 del informe.
- [ ] 0 instancias y 0 volúmenes al final; regla de EventBridge borrada.
- [ ] Mensaje final al usuario: qué se entrenó, el resultado en 3 o 4 líneas, el commit, y que se verifica en el chat original.
