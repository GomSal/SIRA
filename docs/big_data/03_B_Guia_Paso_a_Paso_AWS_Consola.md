# 03_B — Guía Paso a Paso: Cómo Se Creó la Estructura Local y en AWS (`B_SIRA_ARCH` · HEAD `8fde03d`)

**Proyecto:** SIRA (Iteración B — `B_SIRA_ARCH`) — Equipo 10, Universidad EAFIT  
**Repositorio Fuente (GitHub):** [`GomSal/SIRA@modelo-humedad`](https://github.com/GomSal/SIRA/tree/modelo-humedad) (commit **`8fde03db`**, 2026-11-21)  
**Clon Local Exclusivo del Repo:** [`la raíz del repositorio (`./`)`](https://github.com/GomSal/SIRA/tree/modelo-humedad)  
**Cuenta AWS Activa:** `929570730700` (`admin-gomsal`) · **Región:** `us-east-2` (Ohio)  
**Regla de Aprovisionamiento:** **0 stacks en AWS CloudFormation** (creación 100 % directa vía `boto3` desde [`deploy_and_run_b_pipeline.py`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/deploy_and_run_b_pipeline.py))  
**Convivencia:** Todos los recursos de esta iteración llevan el prefijo `sira-b-*` y la base `sira_b_lakehouse_db`, dejando intacta la Iteración A ([`A_SIRA_ARCH/`](https://github.com/GomSal/SIRA/tree/Big-Data_Arch/A_SIRA_ARCH) y `sira-lakehouse-clima-929570730700`).

> **Propósito de este documento:** Documentar paso a paso **exactamente cómo se organizó la carpeta local y cómo se creó cada recurso en AWS** con base en las últimas actualizaciones del repositorio (`708d4de` → `8fde03d`), junto con el paso a paso para auditarlo en la Consola de AWS y la evidencia en vivo (`42/42` sentencias `SUCCEEDED` en Athena).  
> Para aprender a **leer e interpretar cada carpeta, tabla, columna, color de mapa y JSON de la API**, consulta [`04_B_Manual_de_Uso_E2E.md`](04_B_Manual_de_Uso_E2E.md).

---

## Resumen de Cambios Incorporados desde `GomSal/SIRA@modelo-humedad` (`708d4de` → `8fde03d`)

Antes de montar los recursos en AWS se sincronizaron los 6 últimos commits de la rama `modelo-humedad`:

| Commit | Hallazgo / Cambio en el Repositorio | Cómo se Reflejó en la Infraestructura AWS (`B_SIRA_ARCH`) |
|---|---|---|
| `708d4de` | Sin riego registrado, Semáforo B cae a AUC `0,43–0,57` (B discrimina régimen de riego). | Se separaron explícitamente dos caminos operativos en Oro y en la API: **Modo B (con riego)** y **Modo C (sin riego)**. |
| `1a592aa` | Creación del **Modelo C (`scripts/operacion/semaforo_c.py`)** sobre 400 ha (`20.286` píxeles, 2×2 km): `Dr0` desde NDMI vs. curva normal 10 años $\rightarrow$ balance FAO-56 × 5 miembros GEOS-S2S + control anomalía NDMI (alerta morada fitosanitaria) + 4 PNG + visor Leaflet `mapa.html`. | Se creó el **4.º Job PySpark** (`sira-b-04-lote-semaforo-c`), el bloque **`Parallel`** en Step Functions, **3 nuevas tablas Athena** (`oro_curva_normal_10anios`, `oro_semaforo_c_pixeles`, `oro_semaforo_c_resumen`), la carpeta S3 `oro/semaforo_c_mapas/` y el endpoint `GET /semaforo_c`. |
| `8ed983d` | Métricas absolutas selladas del Modelo A (`167.939` filas: NDVI $R^2=0{,}791$, `RMSE=0,120`, `WAPE=16,6 %`; NDMI $R^2=0{,}768$, `RMSE=0,093`, `WAPE=33,6 %`). | Incorporado en `models/pronosticador_a/paso8_metricas_absolutas.csv` y Bloque 2 de `oro_verificacion_clima_cultivo`. |
| `5a069f4` · `ec84551` | Consolidación oficial del **Modelo B = `ARIMAX(1)` sin Prithvi** (AUC macro `0,754 [0,678–0,814]`, **12/17 rojos** detectados en prueba sellada). | Artefacto `models/semaforo_b/modelo_de_uso_oficial_arimax.json` + Bloque 4 (`4_MODELO_B_FAMILIAS_ROC`) en Athena. |
| `8fde03d` | **Reentreno B Etapa 1 (`scripts/b_etapa1_ecuacion.py`)**: de 17 ecuaciones sin contadores de riego, gana el reservorio exponencial de memoria larga con $\alpha=0{,}5$ (`S_tau60_a0.5` AUC `0,790`; **`S_tau45_a0.5`** AUC `0,786`, elegido en 3/4 particiones y todos los datos). | Agregadas las columnas `s_tau45_a05` y `s_tau60_a05` en `plata_estado_hidrico_pixel`, `oro_semaforo` y `job_b03`, más Bloque 5 (`5_REENTRENO_B_ETAPA1`) en Athena. |

---

## ETAPA 0 · Reorganización Local del Repositorio en `SIRA\github`

Para evitar copias duplicadas del repositorio dentro del pipeline Big Data:

1. **Clonación limpia de la rama `modelo-humedad` en carpeta dedicada:**
   ```powershell
   cd <ruta-local-SIRA>
   git clone --branch modelo-humedad --single-branch https://github.com/GomSal/SIRA.git github
   git -C github rev-parse HEAD
   # Salida verificada: 8fde03db52e178a193663341d85b20d3529bcebe
   ```
2. **Eliminación de copias antiguas duplicadas:**
   - Se eliminó la semilla temporal anterior (`B_SIRA_ARCH\bigdata_pipeline\data\bronce\repo_modelo_humedad\`) y cualquier copia scratch.
   - Se verificó que `A_SIRA_ARCH\` permanezca intacta como respaldo histórico de la Iteración A.
3. **Fuente única de verdad:** tanto [`fetch_b_bronze_data.py`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/ingestion/fetch_b_bronze_data.py) como [`deploy_and_run_b_pipeline.py`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/deploy_and_run_b_pipeline.py) leen directamente desde `la raíz del repositorio (`./`)`.

---

## ETAPA 1 · Ingesta Local hacia la Capa Bronce (`fetch_b_bronze_data.py`)

Se ejecutó:
```powershell
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\ingestion\fetch_b_bronze_data.py
```

El script transformó los insumos reales de `SIRA\github` en archivos Apache Parquet (Snappy) y artefactos JSON/PNG/HTML dentro de `B_SIRA_ARCH/bigdata_pipeline/data/`:

| Carpeta Generada | Origen Exacto en `SIRA\github\` | Contenido Materializado |
|---|---|---|
| `data/bronce/sentinel2/` | `data/sentinel2/nataima_s2_diario.csv` | `5.086` filas Sentinel-2 L2A (10–20 m). |
| `data/bronce/landsat/` | `data/landsat/nataima_landsat_diario.csv` | `516` filas Landsat 7/8/9 (30 m). |
| `data/bronce/hls/` | `data/hls/` (recortes `.tif`) | Manifiesto de `41` escenas HLS2 ($224\times 224$ px) para gap-fill con `Prithvi-EO-2.0`. |
| `data/bronce/chirps/` y `power/` | `data/nasa_power/` y `data/procesados/nataima_diario.csv` | `730` días observados de lluvia CHIRPS + meteorología/ETo FAO-56. |
| `data/bronce/geos_s2s/` | `data/geos_s2s/geos_nrt.csv` y `geos_drift.csv` | `41.805` filas NRT (5 miembros × 81 d) + `8.008` filas de drift histórico (2001–2020). |
| `data/bronce/campo_nataima/` | `data/procesados/nataima_diario.csv` y `tabla_entrenamiento_unificada.csv` | `198` mediciones TDR reales de campo (Martínez-Vega et al., 2025). |
| `data/bronce/ideam_auditoria/` | `reports/ideam_auditoria/` | Evidencia auditable del descarte operativo de IDEAM. |
| `data/bronce/semaforo_c_repo/` | `reports/operacion_ejemplo/pixeles.csv.gz` | **`20.286` píxeles reales** (400 ha) del Modelo C. |
| `data/oro/curva_normal_10anios/` | `reports/modelo_a/entrenamiento_final/paso8_curva_normal.csv` | Curva sana de referencia de 10 años (`16` tramos `das_b = 0..150`). |
| `data/oro/semaforo_c_mapas/` | `reports/operacion_ejemplo/` | `semaforo_h00.png`, `h07.png`, `h15.png`, `h30.png`, `resumen.json`, **`mapa.html`** (Leaflet/Esri) y `qa_reproduce_a.json`. |
| `data/models/` | `reports/modelo_a/`, `reports/semaforo_clasificador_ndvi_prithvi/`, `reports/b_reentreno/etapa1/` | Metadatos y métricas oficiales de **Modelos A, B, C, Reentreno Etapa 1 y Prithvi**. |

---

## ETAPA 2 · Procesamiento Local de los 4 Lotes (`--local-only`)

Se ejecutó:
```powershell
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\deploy_and_run_b_pipeline.py --local-only
```

En esta etapa se construyeron todas las tablas Plata y Oro localmente antes de subirlas a AWS:
1. **Lote 1 (`plata/indices_pixel`):** calibración lineal Landsat→S2, filtro determinístico de bruma (caída $>0{,}10$ o banda azul $\ge 0{,}075$), detección de siembra y cálculo de las **20 features del Modelo A** (`5.173` filas despejadas: `4.680` S2 + `493` Landsat).
2. **Lotes 2 y 3 (`plata/clima_observado`, `plata/geos_s2s_crudo`, `oro/correccion_coeficientes`, `oro/geos_s2s_corregido`):** consolidación CHIRPS+POWER (`730` días), pivoteo GEOS-S2S (`20.205` filas en `70` emisiones), coeficientes versionados (`v2026_11`) y ETo escalada FAO-56.
3. **Lote 4A (`oro/pronostico_predictor`):** inferencia del **Modelo A** para horizontes $h = 1\dots 30\text{ d}$ (sin clima, sin Prithvi).
4. **Lote 4B (`plata/estado_hidrico_pixel`, `models/semaforo_b/arimax1_params.json`, `oro/semaforo`):** ajuste GLS/Prais-Winsten de `ARIMAX(1)` sobre los 198 datos TDR, propagación recursiva de `S_tau20`, `S_tau5`, `Dr_k0.5` más los reservorios largos **`S_tau45_a05`** y **`S_tau60_a05`** del Reentreno Etapa 1, para 5 miembros × 2 escenarios (`sin_riego` vs. `riego_30mm_hoy`).
5. **Lote 4C (`oro/semaforo_c_pixeles`, `oro/semaforo_c_resumen`):** materialización de los **20.286 píxeles reales** de Modelo C (`emision=2026-11-12`) y el resumen zonal de los 4 horizontes (`h = 0, 7, 15, 30 d`).
6. **Verificación Consolidada (`oro/verificacion_clima_cultivo`):** `33` filas cubriendo los **5 bloques oficiales de validación** del repositorio.

---

## ETAPA 3 · Cómo Se Creó Cada Recurso en AWS (`us-east-2`) Paso a Paso

Todo el despliegue se ejecutó con un único comando directo contra la API de AWS (`boto3`, **0 stacks CloudFormation**):
```powershell
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\deploy_and_run_b_pipeline.py --aws-only
```

### Inventario Completo de Recursos Montados en AWS (`929570730700` · `us-east-2`)

| Paso | Servicio AWS | Nombre Exacto del Recurso | Rol Exacto en `B_SIRA_ARCH` (`HEAD 8fde03d`) |
|---|---|---|---|
| **3.0** | **AWS CloudFormation** | *(Ninguno — **`0 stacks`**)* | Verificado vía `cfn.list_stacks()` antes de aprovisionar. |
| **3.1** | **Amazon S3 (Lakehouse)** | `sira-b-lakehouse-929570730700` | Lakehouse Medallion (`bronce/`, `plata/`, `oro/`, `models/`, `scripts/`, `athena-results/`), incluyendo `oro/semaforo_c_mapas/` (`4 PNG` + `mapa.html` + `resumen.json`). |
| **3.2** | **AWS IAM (3 Roles)** | `sira-glue-service-role`<br>`sira-stepfunctions-role`<br>`sira-scheduler-role` | Roles existentes verificados para Glue $\rightarrow$ S3, Step Functions $\rightarrow$ Glue y EventBridge $\rightarrow$ Step Functions. |
| **3.3** | **AWS Glue 4.0 (Job 1)** | `sira-b-01-lote-imagenes-pixel` | **Lote 1 Diario (`2×G.1X`):** Calibración Landsat$\rightarrow$S2 (`Broadcast`), filtro de bruma y 20 features Modelo A (`plata/indices_pixel/`). |
| **3.3** | **AWS Glue 4.0 (Job 2)** | `sira-b-02-lote-clima-y-geos` | **Lotes 2 y 3 (`2×G.1X`):** Clima observado CHIRPS+POWER + corrección sesgo GEOS-S2S-3 (5 miembros) + ETo escalada (`oro/geos_s2s_corregido/`). |
| **3.3** | **AWS Glue 4.0 (Job 3)** | `sira-b-03-lote-inferencia-ab` | **Lote 4A+4B (`2×G.1X`):** Predictor A (1–30 d) + Semáforo B oficial `ARIMAX(1)` sin Prithvi + reservorios `S_tau45_a05`/`S_tau60_a05` (`oro/semaforo/`). |
| **3.3** | **AWS Glue 4.0 (Job 4 · NUEVO)** | **`sira-b-04-lote-semaforo-c`** | **Lote 4C (`2×G.1X`):** Semáforo físico Modelo C sin riego (`NDMI` vs. curva 10 años $\rightarrow$ FAO-56 `Dr` × 5m + control fitosanitario morado) hacia `oro/semaforo_c_pixeles/` y `oro/semaforo_c_resumen/`. |
| **3.4** | **AWS Step Functions (SM 1)** | `sira-b-flujo-diario-imagenes-inferencia` | Flujo diario encadenado: `sira-b-01` $\rightarrow$ `sira-b-03` $\rightarrow$ **`sira-b-04`**. |
| **3.4** | **AWS Step Functions (SM 2)** | `sira-b-flujo-geos-5dias` | Flujo cada 5 días con estado **`Parallel`**: `sira-b-02` $\rightarrow$ **`[sira-b-03 (Modelo B) || sira-b-04 (Modelo C)]`**. |
| **3.4** | **AWS Step Functions (SM 3)** | `sira-b-flujo-recalibracion-mensual` | Flujo mensual (día 1): recalibración de coeficientes de sesgo GEOS-S2S (`sira-b-02`). |
| **3.5** | **EventBridge Scheduler (1)** | `sira-b-horario-diario-5am` | `cron(0 5 * * ? *)` (`America/Bogota`, `ENABLED`) $\rightarrow$ SM 1. |
| **3.5** | **EventBridge Scheduler (2)** | `sira-b-horario-geos-5dias` | `rate(5 days)` (`America/Bogota`, `ENABLED`) $\rightarrow$ SM 2. |
| **3.5** | **EventBridge Scheduler (3)** | `sira-b-horario-recalibracion-mensual` | `cron(0 6 1 * ? *)` (`America/Bogota`, `ENABLED`) $\rightarrow$ SM 3. |
| **3.6** | **Glue Catalog / Athena** | Base de datos: `sira_b_lakehouse_db` | **13 tablas Parquet externas** (`4 plata_*` + `9 oro_*`) + **6 consultas analíticas** verificadas (`42/42 SUCCEEDED`). |

### Detalle Técnico de Qué Hizo Cada Paso Interno en AWS

1. **Paso 3.0 — Verificación de Región y Regla Cero CloudFormation:**
   - El script validó `us-east-2` y consultó `cfn.list_stacks()`, confirmando **`0 stacks activos`**.
2. **Paso 3.1 — Sincronización Limpia y sin Duplicados en Amazon S3 (`s3://sira-b-lakehouse-929570730700/`):**
   - Subió todos los archivos estáticos de `bronce/`, `plata/estado_hidrico_pixel/`, `oro/curva_normal_10anios/`, `oro/verificacion_clima_cultivo/`, `oro/predios/`, `models/` (A, B, C, Reentreno Etapa 1, Prithvi) y los **4 scripts PySpark + SQL** en `scripts/`.
   - Subió los mapas del Modelo C a `oro/semaforo_c_mapas/` asignando cabeceras HTTP correctas (`image/png`, `text/html; charset=utf-8`, `application/json; charset=utf-8`).
   - **Control de idempotencia (`clear_s3_prefix`):** antes de cargar los 9 prefijos particionados de Plata y Oro, purgó cualquier `.snappy.parquet` residual de ejecuciones anteriores de Spark para garantizar **cero filas duplicadas** en Athena (`idx=59` particiones, `cli=2`, `geos_crudo=12`, `coef=1`, `geos_corr=12`, `pred_a=1`, `sem_b=1`, `sem_c_px=1`, `sem_c_res=1`).
3. **Paso 3.2 — Verificación de Roles IAM:**
   - Reutilizó sin duplicar permisos los roles existentes `sira-glue-service-role`, `sira-stepfunctions-role` y `sira-scheduler-role`.
4. **Paso 3.3 — Registro de los 4 Jobs PySpark en AWS Glue 4.0:**
   - Cada job fue creado/actualizado con `GlueVersion="4.0"`, `PythonVersion="3"`, `WorkerType="G.1X"`, `NumberOfWorkers=2`, `Timeout=20`, logs continuos de CloudWatch y métricas activas, apuntando a sus rutas S3 exactas en `DefaultArguments`:
     - [`job_b01_lote_imagenes_pixel.py`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/spark_jobs/job_b01_lote_imagenes_pixel.py) $\rightarrow$ `sira-b-01-lote-imagenes-pixel`
     - [`job_b02_lote_clima_y_geos.py`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/spark_jobs/job_b02_lote_clima_y_geos.py) $\rightarrow$ `sira-b-02-lote-clima-y-geos`
     - [`job_b03_lote_inferencia_ab.py`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/spark_jobs/job_b03_lote_inferencia_ab.py) $\rightarrow$ `sira-b-03-lote-inferencia-ab`
     - [`job_b04_lote_semaforo_c.py`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/spark_jobs/job_b04_lote_semaforo_c.py) $\rightarrow$ `sira-b-04-lote-semaforo-c`
5. **Paso 3.4 — Definición ASL de las 3 Máquinas en AWS Step Functions:**
   - Cargó las definiciones JSON desde `B_SIRA_ARCH/bigdata_pipeline/orchestration/`:
     - [`stepfunctions_b_diario_imagenes_ab.asl.json`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/orchestration/stepfunctions_b_diario_imagenes_ab.asl.json) (`sira-b-01` $\rightarrow$ `sira-b-03` $\rightarrow$ `sira-b-04`).
     - [`stepfunctions_b_geos_5dias.asl.json`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/orchestration/stepfunctions_b_geos_5dias.asl.json) (`sira-b-02` $\rightarrow$ **`Parallel`**[`sira-b-03` || `sira-b-04`]).
     - [`stepfunctions_b_recalibracion_mensual.asl.json`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/orchestration/stepfunctions_b_recalibracion_mensual.asl.json) (`sira-b-02`).
6. **Paso 3.5 — Programación en Amazon EventBridge Scheduler:**
   - Actualizó las 3 reglas en zona horaria `America/Bogota` con `State="ENABLED"` y `FlexibleTimeWindow={"Mode": "OFF"}`.
7. **Paso 3.6 — Reconstrucción del Catálogo SQL (`13 tablas`) en Amazon Athena:**
   - Ejecutó de principio a fin [`ddl_and_analytics_b_athena.sql`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/sql/ddl_and_analytics_b_athena.sql): `1 CREATE DATABASE`, `13 DROP TABLE IF EXISTS`, `13 CREATE EXTERNAL TABLE` (con los esquemas actualizados incluyendo `s_tau45_a05`, `s_tau60_a05` y las 3 tablas nuevas del Modelo C), `9 MSCK REPAIR TABLE` para descubrir particiones S3 y **`6 SELECT`** de auditoría.

---

## ETAPA 4 · Recorrido Paso a Paso en la Consola de AWS (Para Auditoría o Sustentación)

1. **CloudFormation (`0 stacks`):** entra a [https://console.aws.amazon.com/](https://console.aws.amazon.com/) $\rightarrow$ selecciona **`US East (Ohio) us-east-2`** $\rightarrow$ busca **`CloudFormation`** $\rightarrow$ **Stacks (Active)** $\rightarrow$ confirma **`0 stacks`**.
2. **Amazon S3 (`sira-b-lakehouse-929570730700`):** recorre `bronce/`, `plata/indices_pixel/predio_id=NATAIMA_01/fecha=...` (59 fechas), `plata/estado_hidrico_pixel/`, `oro/curva_normal_10anios/`, `oro/semaforo/`, `oro/semaforo_c_pixeles/`, `oro/semaforo_c_resumen/`, `oro/semaforo_c_mapas/` (`mapa.html` y 4 `.png`) y `models/`.
3. **AWS Glue Studio (`ETL jobs`):** verifica los **4 jobs `sira-b-01` a `sira-b-04`**; abre `sira-b-04-lote-semaforo-c` (pestaña **Script**) para mostrar el cálculo de `Dr0` vs. curva 10 años, la propagación FAO-56 de los 5 miembros y la bandera morada fitosanitaria.
4. **AWS Step Functions (`State machines`):** abre **`sira-b-flujo-geos-5dias`** y mira el grafo visual (**Workflow**) mostrando el bloque **`Parallel`** ejecutando en simultáneo el **Semáforo B (con riego)** y el **Semáforo C (sin riego)** sin volver a correr el Modelo A.
5. **Amazon EventBridge (`Scheduler` $\rightarrow$ `Schedules`):** confirma los 3 horarios `sira-b-horario-*` en estado **`ENABLED`** (`America/Bogota`).
6. **Amazon Athena (`Query editor`):** selecciona **Database:** `sira_b_lakehouse_db`, verifica **13 tablas** en el panel izquierdo y *Query result location* = `s3://sira-b-lakehouse-929570730700/athena-results/`.

---

## ETAPA 5 · Evidencia Verificada en Vivo en AWS (`42/42 SUCCEEDED`, Cero Duplicados)

Resultado real de las 6 consultas analíticas ejecutadas en Amazon Athena (`sira_b_lakehouse_db`, `us-east-2`) al finalizar la reorganización:

| # | Tabla Consultada | QueryId Verificado en Athena | Resultado Exacto en Vivo (`us-east-2`) |
|---|---|---|---|
| **Q1** | `plata_indices_pixel` | `6b5709a5-a7bc-49f9-bdfc-9e54fb3aaefe` | `S2`: **`4.680`** filas (`NDVI 0,539`, `NDMI 0,160`, `edad_l1 19,5 d`) · `L` calibrado: **`493`** filas (`NDVI 0,577`, `NDMI 0,182`, `edad_l1 24,9 d`). Total = `5.173` (coincide 1:1 con local). |
| **Q2** | `oro_geos_s2s_corregido` | `0d8f235a-4d4c-44fe-a9f8-754744e31058` | Emisión `2026-11-12`: **`5` miembros**, **`225` filas**, lluvia cruda `3,52 mm/d` $\rightarrow$ corregida **`2,67 mm/d`**, `ETo corregida = 3,78 mm/d`. |
| **Q3** | `oro_semaforo` *(Modelo B + Reentreno)* | `7c7c17b6-2b85-49dc-9742-be6eca76b319` | Ventana `01-05 d`: `sin_riego` da `VWC = 26,32 %` (`P(rojo) = 0,820`, `S_tau45_a05 = 16,4`, `S_tau60_a05 = 20,5`) vs. `riego_30mm_hoy` que eleva a **`VWC = 27,15 %`** (`P(rojo) = 0,751`, `S_tau45_a05 = 45,1`, `S_tau60_a05 = 49,6`). |
| **Q4** | `oro_semaforo_c_resumen` *(Modelo C · 400 ha)* | `963aee4f-6f7a-426e-8cd0-a474d378f784` | `h=0` (`2026-11-15`): `68,68 ha` cultivo (`33,9 %` suf · `30,4 %` med · `35,7 %` baja · `1.065 m³`). `h=7`: `100 %` baja (`6,70 L/m²`, `4.530 m³`). `h=15`: `96,9 %` baja (`3.890 m³`). `h=30`: `98,8 %` baja (`4.264 m³`). |
| **Q5** | `oro_semaforo_c_pixeles` *(Modelo C · 20.286 px)* | `e9e1093e-14f4-451e-9da3-dc9a5d83923d` | `0_ya_hoy`: **`2.454` px** (`24,54 ha`, `Dr0 = 4,34 mm`) · `1_de_1_a_2_dias`: `512` px (`5,12 ha`) · `2_de_3_a_5_dias`: **`3.745` px** (`37,45 ha`) · `3_de_6_a_30_dias`: `50` px (`0,50 ha`) · `4_no_llega_en_30d`: `107` px (`1,07 ha`) · `fuera_de_cultivo`: `13.418` px (`134,18 ha`). Total exacto = **`20.286` píxeles**. |
| **Q6** | `oro_verificacion_clima_cultivo` *(5 bloques)* | `b2dcd0cf-4a42-414c-b57f-5f0b7260227d` | `33` métricas reales de los 5 bloques (`1_CLIMA_GEOS_S2S` MAE `32,476 → 24,086 mm`, `2_MODELO_A_NDVI/NDMI_ABSOLUTO`, `3_SEMAFORO_B_ADELANTE`, `4_MODELO_B_FAMILIAS_ROC` `ARIMAX(1) = 0,754`, `5_REENTRENO_B_ETAPA1` `S_tau60 = 0,790` / `S_tau45 = 0,786`). |

### Verificación en Vivo de las 3 Máquinas de Estados en AWS Step Functions (Post-Auditoría · 2026-11-22)

Las 3 máquinas de estados se ejecutaron completas en vivo en `us-east-2` tras corregir la inferencia de particiones `DateType → StringType` en PyArrow (`job_b03` / `job_b04`), evitar condiciones de carrera en `Parallel` (`--skip_modelo_a=true`) y alinear al 100 % los esquemas Parquet con Athena:

| Máquina de Estados | Ejecución Verificada | Estado | Duración | Flujo Ejecutado |
|---|---|---|---|---|
| `sira-b-flujo-recalibracion-mensual` | `audit-fix-1795361848` | **`SUCCEEDED`** | `135 s` | `sira-b-02` |
| `sira-b-flujo-diario-imagenes-inferencia` | `audit-fix-1795361984` | **`SUCCEEDED`** | `499 s` | `sira-b-01` → `sira-b-03` → `sira-b-04` |
| `sira-b-flujo-geos-5dias` | `audit-fix-1795362483` | **`SUCCEEDED`** | `256 s` | `sira-b-02` → **`Parallel[sira-b-03 || sira-b-04]`** |

Tras finalizar las 3 corridas reales de Glue PySpark, se ejecutaron `8/8 MSCK REPAIR TABLE` y `6/6` consultas `SELECT` en Amazon Athena (`c51fdd98`, `07fdd659`, `f311f750`, `f7f2a97e`, `151252eb`, `e0d542fa`), todas **`SUCCEEDED`** con conteos exactos 1:1 y cero duplicados (`5.173` filas en `plata_indices_pixel`, `225` filas en emisión `2026-11-12` de GEOS-S2S y `20.286` píxeles en `oro_semaforo_c_pixeles`).
