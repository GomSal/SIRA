# README — Iteración B Unificada (`B_SIRA_ARCH`): Arquitectura Big Data Conectada a ML (`876be25`) y App Lovable

**Proyecto Integrador 1:** SIRA (Sistema Inteligente de Riego para Arroz en el Alto Magdalena) — Equipo 10 (Universidad EAFIT)  
**Repositorio ML (Fuente Científica):** [`GomSal/SIRA@modelo-humedad`](https://github.com/GomSal/SIRA/tree/modelo-humedad) (**HEAD `876be25`**, 22-nov-2026, emisión **`2026-11-19`**)  
**Cuenta AWS Activa:** `929570730700` (`admin-gomsal`) · **Región:** `us-east-2` (Ohio) · **CloudFormation:** `0 stacks`  
**Lakehouse S3:** `s3://sira-b-lakehouse-929570730700/` · **Base de Datos Athena:** `sira_b_lakehouse_db` (**13 tablas**)  
**API Pública en Producción para Lovable (Lambda Function URL · CORS):** `https://s435rizz22wj6qd3mv37doq76m0widfd.lambda-url.us-east-2.on.aws/`

---

## 1. Arquitectura Consolidada ML ↔ Big Data ↔ App (Limpia: 2 Jobs Glue + 1 Lambda)

La iteración B evolucionó de 4 Jobs aproximados a una **tubería operativa unificada y depurada en AWS**, dejando únicamente los recursos que realmente necesita la producción y eliminando el ruido heredado (9 Glue Jobs obsoletos, 2 Step Functions viejas y 2 EventBridge Schedules viejos):

1. **Artefactos reales de ML en S3 (`subir_aws.py` + `actualizar_datos.py`, con verificación SHA-256):**
   - `models/pronosticador_a/rf/`: **`predictor_ndvi.joblib`** (92,2 MB) y **`predictor_ndmi.joblib`** (95,3 MB) — Random Forest reales (200 árboles, `scikit-learn 1.6.1`, 20 features espectrales/fenológicas sin clima ni Prithvi) + tablas de calibración, curva normal 10 años y corrección GEOS-S2S.
   - `models/semaforo_b/operacion/`: **`arimax.json`** ($\sigma = 3{,}12\,\%$, margen $1{,}5\sigma = 4{,}68\text{ mm}$) y **`calendarios_riego.csv`** (3 tratamientos × 3 temporadas Nataima).
   - `plata/cubo_a/`: **`cubo.npz`** (297,6 MB, 400 ha a 10 m), `cubo_fechas.csv` (escenas hasta `2026-11-18`) y `clima.csv` (`2001–2026`, CHIRPS hasta `2026-09-30` + relleno NASA POWER `PRECTOTCORR` hasta `2026-11-19`).
   - `bronce/geos_s2s_a/geos_nrt.csv`: pronóstico subestacional NASA GMAO GEOS-S2S-3 (5 miembros × 81 días).
2. **Job 1 — `sira-b-02-lote-clima-y-geos` (AWS Glue 4.0 PySpark):**
   - Procesa los 26 años de clima observado (`plata/cubo_a/clima.csv` → `plata/clima_observado/`) y aplica la corrección de sesgo de los **5 miembros GEOS-S2S-3** con la climatología sellada de 10 años (`2015-11-18..2025-11-17`) escribiendo `oro/geos_s2s_corregido/`.
3. **Job 2 — `sira-b-05-inferencia-unificada-ml` (AWS Glue 5.0 · Python 3.11 · `scikit-learn==1.6.1`):**
   - Con `--fecha_hoy=auto` selecciona automáticamente la última fecha con lluvia observada en `clima.csv` (**`2026-11-19`**) e incluye una **guardia monotónica** sobre `latest.json` para jamás retroceder a una emisión anterior.
   - Ejecuta la cadena oficial exacta por píxel de 10 m sobre las **400 ha (20.286 píxeles agrícolas; `6.761` en cultivo al 19-nov)**:
     - **Modelo A (`.joblib` reales):** detecta siembra por curva NDVI y pronostica NDVI/NDMI de hoy a +30 días.
     - **Tramo 1 (`siembra → hoy`):** `b_entradas.propagar()` con CHIRPS + relleno `PRECTOTCORR` de NASA POWER (`50` días de hueco al 19-nov) + ETo POWER + NDVI observado + 3 calendarios `CD`.
     - **Día 0 (`hoy`) — Modelo B `ARIMAX(1)`:** estima $\mu_0$ y aplica el margen una sola vez: $Dr_0 = 1{,}5\cdot(30 - \mu_0) + 1{,}5\cdot\sigma$ (`4,68 mm`).
     - **Tramo 2 (`hoy → +30 d`) — `Dr_modificada` FAO-56:** propaga sin riego y sin margen adicional (`TAW = 6,7 mm`, `p = 0,65`, `DSAT = 26,475 mm`, `k = 0,5`) promediando los **5 miembros de GEOS-S2S-3** desde `oro/geos_s2s_corregido/`.
     - **Alarma tardía NDMI:** marca morada únicamente sobre píxeles `suficiente` (verdes) sin alterar `media → baja`.
   - Publica simultáneamente **Parquet Oro para Athena** (`40` cols en `oro_semaforo_c_pixeles`, `25` cols en `oro_semaforo_c_resumen`), **App Lovable (`oro/semaforo_app/` en `formato: 2`, 31 días diarios)**, **`simulador_riego.json`**, mapas PNG + `mapa.html` y registra particiones automáticamente en Glue Data Catalog.
4. **API Serverless en Producción para Lovable (`sira-b-api-serving`, AWS Lambda Python 3.11 + Function URL + CORS):**
   - Sin capas externas (solo stdlib + `boto3`, ZIP de `6,1 KB`), caché en memoria invalidada por `latest.json`, compresión `gzip` automática (`232 KB` para `/semaforo`) y fallback resiliente en `POST /regar_hoy`.
   - Expone `GET /health`, `GET /latest`, `GET /zonas` (Tolima + 47 municipios), `GET /resumen`, `GET /semaforo` (6.761 píxeles × 31 días), `GET /semaforo_c` y **`POST /regar_hoy`** (`< 5 ms` en caliente, física exacta FAO-56 sin re-correr Modelo A).

---

## 2. Mapa de Documentación y Código en `B_SIRA_ARCH/`

| Archivo | Qué contiene |
|---|---|
| [`README_B_BigData.md`](README_B_BigData.md) | Este resumen ejecutivo unificado y cumplimiento de rúbrica `SI7006`. |
| [`02_B_BigData_Arch.md`](02_B_BigData_Arch.md) | Arquitectura técnica completa ML ↔ Big Data ↔ App (2 Glue Jobs, 3 Step Functions secuenciales, Lambda Function URL y criterio Spark). |
| [`03_B_Guia_Paso_a_Paso_AWS_Consola.md`](03_B_Guia_Paso_a_Paso_AWS_Consola.md) | Inventario limpio en AWS (`us-east-2`, `0 stacks` CloudFormation), auditoría en consola y evidencia en vivo (`2026-11-19`). |
| [`04_B_Manual_de_Uso_E2E.md`](04_B_Manual_de_Uso_E2E.md) | Manual de uso E2E, guía de lectura de tablas/JSON (`2026-11-19`), integración Lovable y guion de sustentación. |
| [`05_B_Historia_Conexion_ML_BigData_y_App.md`](05_B_Historia_Conexion_ML_BigData_y_App.md) | Historia completa de la conexión ML ↔ Big Data ↔ App Lovable: hallazgos, limpieza AWS y operación diaria. |
| `bigdata_pipeline/spark_jobs/job_b02_lote_clima_y_geos.py` | Job AWS Glue 4.0 PySpark (`sira-b-02`): clima 26 años (CHIRPS+POWER) + corrección sesgo GEOS-S2S (10a sellados). |
| `bigdata_pipeline/spark_jobs/job_b05_inferencia_unificada_ml.py` | Job AWS Glue 5.0 (`sira-b-05`): inferencia unificada ML real (`--fecha_hoy=auto` + guardia monotónica) → Athena + Lovable. |
| `bigdata_pipeline/orchestration/stepfunctions_b_*.asl.json` | 3 Máquinas de Estados secuenciales limpias (`sira-b-05`, `sira-b-02 → sira-b-05`, `sira-b-02`). |
| `bigdata_pipeline/sql/ddl_and_analytics_b_athena.sql` | DDL de `sira_b_lakehouse_db` (**13 tablas**, esquema actualizado de 40/25 cols) + 6 consultas verificadas. |
| `bigdata_pipeline/api/app_b_inference_api.py` | Código oficial de la AWS Lambda `sira-b-api-serving` (Function URL con CORS único + simulador `POST /regar_hoy` FAO-56). |
| `bigdata_pipeline/deploy_and_run_b_pipeline.py` | Orquestador E2E local y aprovisionador directo AWS (`boto3`, `0 stacks` CloudFormation, protege salidas vivas de ML). |

---

## 3. Cumplimiento de la Rúbrica `SI7006` (Grandes Datos)

| Criterio `SI7006` | Evidencia Concreta Verificada en Vivo (`us-east-2`) |
|---|---|
| **1. Arquitectura y Flujo (15 %)** | Lakehouse Medallion (`bronce/`, `plata/`, `oro/`) multi-cadencia (diario, 5 días, mensual) en Step Functions uniendo clima (`sira-b-02`) con inferencia real ML (`sira-b-05`) y entrega serverless (`Lambda`) a Lovable. |
| **2. Ambiente Tecnológico (10 %)** | 100 % Serverless en AWS `us-east-2` (`S3`, `Glue 4.0 & 5.0`, `Step Functions`, `EventBridge Scheduler`, `Athena`, `Lambda Function URL`, `IAM`, **`0 stacks` CloudFormation**, cero EC2/NAT) + justificación cuantitativa `SPARK_ECUACION.md`. |
| **3. Origen e Ingesta (15 %)** | Fuentes satelitales y climáticas reales (`cubo.npz` hasta `2026-11-18`, `clima.csv` `2001–2026` CHIRPS + relleno POWER hasta `2026-11-19`, `GEOS-S2S-3` NRT/Drift, 198 TDR Nataima, auditoría IDEAM) con verificación SHA-256. |
| **4. Almacenamiento (15 %)** | Parquet (Snappy) particionado (`predio_id=/fecha=`, `anio=`, `version=`, `emision=`) + JSON comprimido gzip (`formato: 2`) en S3 privado, con 13 tablas catalogadas en `sira_b_lakehouse_db` y registro automático de particiones. |
| **5. Procesamiento Distribuido (20 %)** | PySpark Glue 4.0 (`Window`, `Broadcast Join`) en `sira-b-02` + Glue 5.0 (`scikit-learn==1.6.1`) ejecutando los `.joblib` reales de Modelo A y balance `A + B + FAO-56` en `sira-b-05` (`194 s` `SUCCEEDED`). |
| **6. Despliegue y Consumo (20 %)** | 6 consultas SQL verificadas en Athena con paridad 1:1 respecto a ML (`2026-11-19`: `99,6 %` verde hoy, `98,4 %` rojo en 3–5 d, `1,6 %` en 1–2 d), visor `mapa.html` en S3 y AWS Lambda Function URL viva en Lovable (`8/8 PASS`). |
