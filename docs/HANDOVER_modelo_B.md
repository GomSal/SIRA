# Handover: entrenamiento del Modelo B (Corrector satélite → dron)

**Para:** un chat nuevo cuya **única tarea** es entrenar el Modelo B en AWS EC2 y dejar los resultados en el repositorio.
**Después:** los resultados se verifican en el chat original del proyecto. Este chat **no** edita `docs/informe_preliminar.md` ni `HANDOVER.md`.
**Fecha:** 17-nov-2026. Entrega del proyecto: 23-nov-2026.

---

## 0. Reglas de trabajo con el usuario

- Español, respuestas **cortas y claras**, sin jerga interna. Si algo importante cambia, se dice primero y en una frase.
- No afirmar nada sin verificarlo (con el dato, el archivo o la fuente).
- **Antes de lanzar EC2:** decir costo y tiempo estimados, y **esperar el OK explícito** del usuario.
- Commits **sin** líneas `Co-Authored-By` de Claude o Anthropic.
- No subir `data/raw/martinez_vega_2025.pdf` (marca de agua de la editorial) ni `data/s3/`.
- Calidad de curso real, no una prueba de juguete: lo que pide la metodología, bien hecho.

## 1. Qué leer primero (en este orden)

1. `docs/informe_preliminar.md`, §1 a §6 completos (§6 es **el diseño que hay que implementar**: los 8 pasos del profesor), §11.6 y §11.9 (pruebas de factibilidad del Corrector).
2. `scripts/corrector_dron_v2.py`: implementación de referencia. De ahí salen la carga de datos, el control de calidad, la referencia del dron y el bootstrap por bloques móviles. **Reutilizar su lógica**, no reinventarla.
3. `cloud/ronda1_b/lanzar.py`: patrón de lanzamiento EC2 con salvaguardas, ya probado en esta cuenta. El nuevo lanzador parte de ahí.

## 2. Qué aprende el Modelo B

- **Objetivo:** el índice que mediría el dron (NDVI y NDRE, Fig. 7 del artículo) en cada franja o píxel y fecha. Regresión, un corrector por índice:
  - **NDVI:** Sentinel-2 por franja + Landsat 7/8/9 por píxel de 30 m.
  - **NDRE:** solo Sentinel-2 (Landsat no tiene borde rojo).
- **No** predice humedad del suelo ni agua. Las columnas `vwc_*` de `reports/correlacion_ndvi_ndre_datos.csv` **no se usan** como entrada ni como objetivo.

## 3. Datos de entrada (todos en el repo)

| Archivo | Contenido | Notas |
|---|---|---|
| `reports/correlacion_ndvi_ndre_datos.csv` | Sentinel-2 por franja y fecha: `ndvi`, `ndre`, `das`, `temporada`, `franja` | `franja` = nombre del tratamiento **de GS1** (oeste MIRI, centro AWD+MIRI, este CD). Única fuente de NDRE |
| `data/processed/modelo_b/pixeles_lote_s2_landsat.csv` | Píxeles del lote, S2 (5086 filas) y Landsat (516), con `frac_MIRI`, `frac_AWD+MIRI`, `frac_CD`, `frac_lote`, `ndvi`, `ndwi`, `ndmi`, `mndwi`, `str` | Copia congelada de la salida E1 de la ronda 1 (`data/s3/...`, que está fuera de git). Las columnas `frac_*` también usan los nombres de GS1 = posición física |
| `data/processed/dron_indices.csv` | Dron: `temporada, etapa, tratamiento, indice, valor` (27 medias por índice) | Digitalizado de la Fig. 7, validado contra la Tabla 4 |
| `reports/dron/revision_gs{1,2,3}.csv` | `ndvi_lote`, `azul_lote` por fecha S2 | Para el control de bruma |
| `reports/dron/corrector_dron_v2.csv` | Resultados de factibilidad (regresión lineal) | **Prueba de no regresión** (§5, paso 2) |

Constantes del proyecto (ya están en `corrector_dron_v2.py`):
- Siembras: GS1 2021-05-27, GS2 2021-12-04, GS3 2022-07-09. Ventana: días 25 a 115 desde la siembra.
- Rotación "oeste" (posición física → tratamiento): GS1 MIRI / AWD+MIRI / CD; GS2 AWD+MIRI / CD / MIRI; GS3 CD / MIRI / AWD+MIRI.
- Día de cada etapa del dron: vegetativa 30, reproductiva 60, maduración 100. Interpolación PCHIP por temporada × tratamiento × índice, **solo para emparejar fechas**.
- Error estándar del dron por etapa (Tabla 4, × √3): diccionario `EE` del script.
- Control de calidad: se descarta una fecha si el NDVI medio del lote cae > 0,10 frente a la anterior **y** a la siguiente (serie conjunta S2 + Landsat), o si en S2 el azul del lote es ≥ 0,075 entre los días 25 y 115.
- Pureza: S2 píxeles ≥ 90 % dentro de una franja; Landsat píxeles con `frac_lote ≥ 0,5`, referencia = mezcla de tratamientos ponderada por área.

**Tamaños esperados tras el control de calidad** (sirven de contrato de datos): NDVI 23 fechas y 141 pares; NDRE 14 fechas y 42 pares. Si no salen, se para y se investiga antes de seguir.

## 4. Variables candidatas y cómo construirlas

- Para S2 por franja: `ndvi`, `ndre` del CSV de correlación; `ndwi` y `ndmi` = media de los píxeles S2 con ≥ 90 % en esa franja, misma fecha.
- Para Landsat por píxel: `ndvi`, `ndwi`, `ndmi` del píxel.
- `sensor_L` = 1 si Landsat, 0 si S2.
- **Relativo al lote:** índice − media del lote ese día y con ese sensor.
- `das` (días desde siembra).
- Prohibidas: tratamiento, temporada, `vwc_*`, posición de la franja.

Conjuntos fijados de antemano (de menos a más), por corrector:

| Conjunto | NDVI (S2 + Landsat) | NDRE (solo S2) |
|---|---|---|
| F0 | ndvi | ndre |
| F1 | + sensor_L | + ndvi |
| F2 | + ndvi relativo al lote | + ndre relativo al lote |
| F3 | + das | + das |
| F4 | + ndwi, ndmi | + ndwi, ndmi |

## 5. Los 8 pasos, en concreto

Todo en un solo script determinista `cloud/modelo_b/job_modelo_b.py` (semillas fijas), con salidas en `--out`. Cada paso escribe sus archivos al terminar.

1. **Análisis exploratorio** → `eda/`: fechas por temporada y sensor; series de tiempo por franja junto a la del dron; dispersión satélite vs dron por temporada y sensor; matriz de correlación de candidatas; diferencias entre tratamientos (dron vs satélite). Figuras PNG + `eda_resumen.csv`.
2. **Calidad de datos** → `calidad_fechas.csv` (cada fecha con su motivo de descarte o "ok"), `tabla_limpia_{ndvi,ndre}.csv`. Asserts: tamaños del §3, sin NaN en objetivo, ninguna columna prohibida. **No regresión:** la regresión lineal con F0/F1 y una temporada afuera debe reproducir el `r` de `corrector_dron_v2.csv` con diferencia ≤ 0,01; si no, se para (`ERROR_NO_REGRESION`).
3. **Partición** → `particiones.csv`: validación cruzada anidada por temporada. Afuera: 1 temporada de prueba sellada. Adentro: entrenar con una y validar con la otra, en los dos sentidos; el RMSE de validación es el promedio de los dos sentidos ponderado por el número de fechas de validación. Además, la verificación cronológica GS1 → GS2 → GS3. Si un sentido de entrenamiento tiene < 5 fechas (NDRE GS1 tiene 3), se reporta y se marca.
4. **Métricas** (fijas antes de ver resultados):
   - principal: **RMSE con peso igual por fecha** (la fecha es la unidad independiente);
   - reporte: RMSE sin ponderar (comparable con §11.9), MAE, r de Pearson, mejora frente a la línea base (media del entrenamiento);
   - secundaria, solo fechas S2 con las 3 franjas: % de fechas en que acierta cuál franja está más baja, y tau de Kendall medio del orden de las 3.
5. **Estandarización:** `StandardScaler` dentro de un `Pipeline` de scikit-learn, ajustado solo con el entrenamiento de cada partición.
6. **Selección de variables:** cada conjunto F0–F4 × cada modelo × cada hiperparámetro se evalúa en validación. Control: coeficientes que Lasso anula e importancia por permutación en validación → `importancia.csv`.
7. **Ajuste y selección de modelos:** rejillas sugeridas (pequeñas, por los pocos datos):
   - lineal (sin hiperparámetros); Huber `epsilon` {1,35; 1,5; 2}, `alpha` {1e-4; 1e-2};
   - Ridge `alpha` logspace(−3, 3, 13); Lasso `alpha` logspace(−4, 0, 9);
   - splines aditivos: `SplineTransformer(n_knots ∈ {3, 4, 5}, degree=3)` + Ridge `alpha` {0,1; 1; 10} (sustituye a pyGAM, que da problemas con numpy/scipy recientes);
   - Random Forest: 500 árboles, `max_depth` {2, 3, 4, None}, `min_samples_leaf` {3, 5, 10}, `max_features` {1,0; "sqrt"};
   - XGBoost: `n_estimators` {100, 300}, `max_depth` {1, 2, 3}, `learning_rate` {0,03; 0,1}, `min_child_weight` {3, 5}, `subsample` 0,8.
   - **Regla de selección:** gana el menor RMSE de validación. Empate = el IC 95 % de la diferencia de RMSE de validación entre el mejor y el candidato (200 remuestras por bloques móviles de las fechas de validación, sin reentrenar) incluye 0 → se elige el más simple. Orden de simplicidad: lineal < Huber < Ridge < Lasso < splines < RF < XGBoost; dentro de una familia, menos variables primero.
   - Salidas: `validacion_interna.csv` (todas las combinaciones), `seleccion.csv` (ganador por temporada de prueba y por qué).
8. **Evaluación:**
   - reentrenar lo elegido con las 2 temporadas y medir **una vez** en la sellada → `prueba.csv`, `predicciones_prueba.csv`;
   - bootstrap por bloques móviles de 2 fechas dentro de cada temporada + ruido del dron, **1000 repeticiones**, reentrenando la configuración elegida → `bootstrap_IC.csv` (IC 95 % de RMSE, MAE, r y % de repeticiones que superan a la línea base);
   - errores por etapa y por sensor → `errores_por_etapa_sensor.csv`;
   - estabilidad: **200 repeticiones** de la selección completa sobre remuestras → `estabilidad_seleccion.csv` (cuántas veces gana cada modelo);
   - verificación cronológica → `cronologica.csv`;
   - figuras: predicho vs dron por temporada; series por franja con predicción;
   - modelo de uso: la configuración más elegida, reentrenada con las 3 temporadas → `models/corrector_b/{ndvi,ndre}.joblib` + `{ndvi,ndre}_meta.json` (variables, hiperparámetros, versiones de librerías, sha256 de cada entrada, commit de git, error esperado = el de las pruebas).

Al final, `RESULTADOS.md` corto y en español claro: una tabla por paso, el modelo elegido por índice, sus métricas de prueba con IC frente a la línea base y frente a §6.11, y qué no salió bien. Sin adornos.

## 6. Infraestructura: EC2 on-demand con salvaguardas

**Cuenta:** perfil AWS por defecto = usuario IAM `hyperdev` (cuenta 809411919665). **Región:** `us-west-2` (ahí está el bucket `sira-809411919665-usw2` de la ronda 1). Prefijo: `modelo_b/<AAAAMMDD-HHMMSS>/`.

**Carpeta nueva** `cloud/modelo_b/`: `lanzar.py` (adaptado de `cloud/ronda1_b/lanzar.py`), `job_modelo_b.py`, `requirements.txt` con versiones fijas, `datos/` (copias de los archivos del §3), `ultima_corrida.json`.

**Versiones:** las del venv local (`.venv`, Python 3.11): numpy 2.4.6, pandas 3.1.1, scipy 1.17.1, scikit-learn 1.9.1, más xgboost y matplotlib y joblib. Instalar xgboost también en el venv local con `pip install --only-binary=:all: xgboost`. En EC2 (Ubuntu 24.04, Python 3.12) instalar **las mismas versiones** desde `requirements.txt`.

**Máquina sugerida:** `c7i.2xlarge` (8 vCPU), paralelizando con joblib. Confirmar el precio on-demand vigente antes de pedir el OK (del orden de US$0,36/h). Con tope de 90 min, el costo máximo es ≈ US$0,55, más centavos de S3 y SNS.

### 6.1 Salvaguardas de tiempo (paran y apagan)

1. `shutdown -h +90` al arrancar + `InstanceInitiatedShutdownBehavior=terminate` → a los 90 min la instancia se **termina** pase lo que pase. Disco con `DeleteOnTermination=true`, verificado después de lanzar.
2. `timeout 10m` para la prueba QA y `timeout 60m` para la corrida; si se pasa → estado `TOPE_TIEMPO`.
3. **Estimación antes de la corrida larga:** la QA mide el tiempo de una repetición de bootstrap y de una selección completa, y proyecta el total. Si la proyección supera 50 min → estado `ESTIMADO_EXCEDE` y se apaga sin correr.
4. **Vigilante de progreso:** el job actualiza un archivo `latido` en cada paso y cada 50 repeticiones de bootstrap. Un proceso en segundo plano del user-data revisa cada 60 s: si el latido tiene más de 8 min → estado `ERROR_SIN_PROGRESO`, mata el proceso y se apaga.

### 6.2 Salvaguardas de error (paran y apagan)

1. Sin `set -u`; `trap final EXIT`: ante cualquier final sube log, estado y lo construido (`out.tar.gz`) y ejecuta `shutdown -h now`.
2. El job envuelve cada paso: excepción → estado `ERROR_PASO<n>` con el traceback en el log y código de salida ≠ 0.
3. Contrato de datos (tamaños, columnas, sin NaN) → `ERROR_DATOS`; no regresión → `ERROR_NO_REGRESION`; NaN o infinito en métricas o predicciones → `ERROR_NUMERICO`; fallo de pip → `ERROR_PIP`.
4. Log y estado se suben a S3 cada 60 s; cada paso sube un parcial.

### 6.3 Alerta inmediata (3 capas)

1. **Correo (AWS):** tema SNS `sira-alertas` con suscripción por correo. **Pedir al usuario el correo** y avisarle que debe confirmar el mensaje de AWS una vez. Regla de EventBridge por corrida: evento "EC2 Instance State-change Notification" con `state ∈ {shutting-down, stopped, terminated}` para el `instance-id` lanzado → SNS. Avisa en el momento en que la máquina se apaga, sea por éxito, error o tope. La política del tema debe permitir publicar a `events.amazonaws.com`. Borrar la regla al terminar.
2. **En el chat:** usar la herramienta `Monitor` con un bucle que lee `status.txt` de S3 cada 30 s y termina cuando el estado es final (`OK`, `ERROR*`, `TOPE*`, `QA_FALLIDO`, `ESTIMADO_EXCEDE`, `TERMINADO_INESPERADO*`) **o** cuando el log no cambia en más de 5 min (máquina colgada o sin red). En ese segundo caso, terminar la instancia desde local con `aws ec2 terminate-instances`.
3. **Al usuario:** apenas termine el `Monitor`, decirle el estado y las últimas 30 líneas del log.

### 6.4 Lista de verificación antes de lanzar (obligatoria)

- [ ] 0 instancias encendidas y 0 volúmenes sueltos en `us-west-2` (y revisar `us-east-1`).
- [ ] La instancia de la ronda 1 (`i-0ce4e8afc042e6081`) está terminada.
- [ ] **Corrida local completa en modo QA** (`python cloud/modelo_b/job_modelo_b.py --qa --out <scratch>`): imprime "QA OK" y pasa la prueba de no regresión.
- [ ] Corrida local con repeticiones reducidas (p. ej. 20 de bootstrap y 5 de estabilidad) para estimar el tiempo total.
- [ ] 0 bytes CR en `.py` y `.sh` empaquetados (contados con Python). Los CSV se normalizan a LF al empaquetar.
- [ ] URLs prefirmadas insertadas con Python (nunca sed) y probadas con PUT + lectura de vuelta.
- [ ] Ningún marcador `__...__` sin reemplazar en el user-data.
- [ ] Suscripción SNS confirmada por el usuario y regla de EventBridge creada para la instancia.
- [ ] Memoria y tiempo calculados; costo dicho al usuario; **OK explícito del usuario**.

Después de la corrida: bajar `out.tar.gz`, verificar 0 instancias y 0 volúmenes, borrar la regla de EventBridge y guardar `ultima_corrida.json`.

**Nota honesta para el usuario:** los datos son pocos (≤ 141 pares). Sin el bootstrap ni la estabilidad, el entrenamiento tarda minutos en un portátil. EC2 aporta la reproducibilidad fuera del portátil, las 1000 + 200 repeticiones y la evidencia en S3. Si la estimación local da menos de 5 min en total, decírselo al usuario. Aun así se corre en EC2, que es lo que él decidió.

## 7. Qué dejar en el repositorio

```
cloud/modelo_b/                 lanzar.py, job_modelo_b.py, requirements.txt, datos/, ultima_corrida.json
reports/modelo_b/               eda/, calidad_fechas.csv, particiones.csv, validacion_interna.csv, seleccion.csv,
                                importancia.csv, prueba.csv, predicciones_prueba.csv, bootstrap_IC.csv,
                                errores_por_etapa_sensor.csv, estabilidad_seleccion.csv, cronologica.csv,
                                figuras, run.log, RESULTADOS.md
models/corrector_b/             ndvi.joblib, ndre.joblib, *_meta.json
tests/test_modelo_b.py          contrato de datos + no regresión (corre en segundos)
```

**Git:** repositorio `C:\Users\santi\SIRA`, rama `modelo-humedad`, remoto `origin` (GomSal/SIRA). Commits en español, sin `Co-Authored-By`. Push a `origin modelo-humedad`.

## 8. Terminado cuando

- [ ] Los 8 pasos corrieron en EC2 con estado `OK`, y las salidas del §7 están en el repo.
- [ ] La prueba de no regresión pasó.
- [ ] Cada temporada de prueba se usó **una sola vez**, después de la selección (queda en `seleccion.csv` el orden de los eventos).
- [ ] `RESULTADOS.md` dice, por índice: modelo elegido, variables, métricas de prueba con IC 95 %, mejora frente a la línea base, estabilidad de la selección, y la comparación con §6.11.
- [ ] 0 instancias y 0 volúmenes al final; regla de EventBridge borrada.
- [ ] Mensaje final al usuario: qué se entrenó, el resultado en 3 o 4 líneas, el commit, y que se verifica en el chat original.
