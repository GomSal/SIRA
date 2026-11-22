# README — Iteración B (`B_SIRA_ARCH`): Arquitectura Big Data Completa Alineada a `GomSal/SIRA@modelo-humedad`

**Proyecto Integrador 1:** SIRA (Sistema Inteligente de Riego para Arroz en el Alto Magdalena) — Equipo 10 (Universidad EAFIT)  
**Repositorio GitHub Local (Fuente Única):** [`la raíz del repositorio (`./`)`](../) (`https://github.com/GomSal/SIRA/tree/modelo-humedad`, **HEAD `8fde03d`**, 21-nov-2026)  
**Cuenta AWS Activa:** `929570730700` (`admin-gomsal`) · **Región:** `us-east-2` (Ohio) · **CloudFormation:** `0 stacks`  
**Lakehouse S3:** `s3://sira-b-lakehouse-929570730700/` · **Base de Datos Athena:** `sira_b_lakehouse_db` (**13 tablas**)

---

## 1. Reorganización del Proyecto y Evolución hasta HEAD `8fde03d`

1. **Separación limpia del repositorio GitHub (`SIRA\github\`):**  
   Todo el código, datos procesados, reportes y resultados del repositorio remoto (`GomSal/SIRA`, rama `modelo-humedad`) viven centralizados y versionados con Git en [`github/`](../). Los scripts de `B_SIRA_ARCH` leen directamente desde esa carpeta sin copias duplicadas.
2. **Iteración A archivada (`A_SIRA_ARCH/`):**  
   Conserva intacta la arquitectura inicial (flujo diario único, regresión climática de bruma, PCA de Prithvi y Open-Meteo a 7 días) junto con sus recursos previos en AWS (`sira-lakehouse-clima-929570730700` y `sira_clima_db`).
3. **Iteración B vigente (`B_SIRA_ARCH/`) — Acoplada a los 3 modelos cerrados en `modelo-humedad` (`708d4de` → `8fde03d`):**
   - **Modelo A — Predictor Espectral (`Random Forest`, 200 árboles para `NDVI` y `NDMI`, `h = 0..30 d`):**
     Usa **20 features espectrales, de rezago y fenológicas** (**sin clima ni PCA de Prithvi**). Prueba sellada (`167.939` filas, commit `8ed983d`): `NDVI` $R^2 = 0{,}791$ (`RMSE 0,120`, *skill* `+0,28`); `NDMI` $R^2 = 0{,}768$ (`RMSE 0,093`, *skill* `+0,29`). Reproducibilidad QA local (`qa_reproduce_a.json`): $r = 0{,}995$ (NDVI) y $r = 0{,}998$ (NDMI).
   - **Modelo B — Semáforo Supervisado con Riego Registrado (`ARIMAX(1)` oficial sin Prithvi + Reentreno Etapa 1):**
     - **Oficial (commit `ec84551`):** `ARIMAX(1)` sin Prithvi sobre 7 exógenas (`S_tau20`, `S_tau5`, `Dr_k0.5`, `riego_5d`, `dias_desde_riego`, `eto_5d`, `ndvi`). Prueba sellada (198 TDR): **`AUC-ROC macro = 0,754 [0,678–0,814]`** y **`12/17 rojos detectados`**, superando a RF (`0,743 / 7`), XGBoost (`0,729 / 6`), GB+Prithvi (`0,708 / 4`) y Logística (`0,681 / 7`).
     - **Reentreno B Etapa 1 (commit `8fde03d`, 21-nov):** Evaluadas 17 variantes sin contadores de riego, gana el **reservorio exponencial `S` con $\alpha = 0{,}5$ y memoria larga**: **`S_tau45_a0.5`** (`AUC val 0,786`, elegido en 3/4 particiones y con todos los datos) y **`S_tau60_a0.5`** (`AUC val 0,790`, Spearman `0,608`). El pipeline Big Data ya calcula y arrastra ambos reservorios en Plata y Oro.
   - **Modelo C — Semáforo Físico Operativo SIN Riego Registrado (commit `1a592aa`, `scripts/operacion/semaforo_c.py`):**
     Resuelve el hallazgo del commit `708d4de` (*sin riego registrado el clasificador B cae a AUC 0,43–0,57*). No se entrena: inicializa día 0 ($Dr_0$) comparando `NDMI` de hoy contra la **curva normal de 10 años por DAS** (`z ≥ -0,67` → lleno; `z ≤ -1,28` → `4,34 mm`), propaga FAO-56 (`TAW = 6,7 mm`, `p = 0,65`, `DSAT = 26,475 mm`, `k = 0,5`) por los **5 miembros de GEOS-S2S-3 corregido** con $K_c = 1{,}0\cdot\text{NDVI}_A + 0{,}45$, toma la mediana `DRM` (`suficiente` / `media` / `baja`) y aplica el **control de anomalía del `NDMI` pronosticado por A** (degrada `media → baja` y marca en morado `suficiente` como *"posible enfermedad o deficiencia nutricional"*). Cubre **400 ha (2×2 km, 20.286 píxeles reales)** con mapas PNG y visor Leaflet `mapa.html` publicados en S3.
   - **`Prithvi-EO-2.0-300M-TL`:** Exclusivo para **relleno operativo de huecos (*gap-filling*) en días nublados hoy** sobre recortes HLS2 ($224\times 224$, 30 m; RMSE `0,093` NDVI / `0,077` NDMI vs. `0,110 / 0,094` del último despejado).

---

## 2. Mapa de Documentación y Código en `B_SIRA_ARCH/`

| Archivo | Qué contiene |
|---|---|
| [`README_B_BigData.md`](README_B_BigData.md) | Este resumen ejecutivo y alineación con la rúbrica `SI7006`. |
| [`02_B_BigData_Arch.md`](02_B_BigData_Arch.md) | Arquitectura técnica completa: Medallion, 3 modelos (A, B, C), 20 + 9 agregados, Prithvi *gap-fill* y ecuación Spark (`1 vs. 100 predios`). |
| [`03_B_Guia_Paso_a_Paso_AWS_Consola.md`](03_B_Guia_Paso_a_Paso_AWS_Consola.md) | **Cómo se creó paso a paso toda la infraestructura en AWS (`us-east-2`)** + evidencia verificada en vivo en Athena. |
| [`04_B_Manual_de_Uso_E2E.md`](04_B_Manual_de_Uso_E2E.md) | **Manual de uso y guía de lectura detallada**: qué dice cada capa, tabla, columna, mapa, color y respuesta JSON de la API, más cómo operarlo día a día. |
| `bigdata_pipeline/ingestion/fetch_b_bronze_data.py` | Ingesta a Capa Bronce leyendo directamente desde `SIRA\github`. |
| `bigdata_pipeline/spark_jobs/job_b01..b04_*.py` | 4 Jobs PySpark AWS Glue 4.0 (`sira-b-01` Imágenes, `sira-b-02` Clima+GEOS, `sira-b-03` A+B, `sira-b-04` Modelo C). |
| `bigdata_pipeline/orchestration/stepfunctions_b_*.asl.json` | 3 Máquinas de Estados (Diario `B01→B03→B04`, Cada 5 días `B02→Parallel(B03||B04)`, Mensual `B02`). |
| `bigdata_pipeline/sql/ddl_and_analytics_b_athena.sql` | DDL de `sira_b_lakehouse_db` (**13 tablas externas Parquet**) + 6 consultas verificadas. |
| `bigdata_pipeline/api/app_b_inference_api.py` | Microservicio dual: `POST /regar_hoy` (Modelo B, <50 ms) y `GET /semaforo_c` (Modelo C, 20.286 píxeles). |
| `bigdata_pipeline/deploy_and_run_b_pipeline.py` | Orquestador E2E local (`--local-only`) y aprovisionador directo AWS (`--aws-only`, `0 stacks` CloudFormation). |

---

## 3. Cumplimiento de la Rúbrica `SI7006` (Grandes Datos)

| Criterio `SI7006` | Evidencia Concreta en `B_SIRA_ARCH` |
|---|---|
| **1. Arquitectura y Flujo (15 %)** | Lakehouse Medallion (`bronce/`, `plata/`, `oro/`) con 3 cadencias batch (diario, cada 5 días, mensual) y ramificación `Parallel` para los dos semáforos operativos (B con riego y C sin riego). Sin streaming justificado. |
| **2. Ambiente Tecnológico (10 %)** | 100 % Serverless en AWS `us-east-2` (`S3`, `Glue 4.0`, `Step Functions`, `EventBridge Scheduler`, `Athena`, `IAM`, `0 stacks CloudFormation`) + decisión matemática `SPARK_ECUACION.md` (`1 vs. 100 predios`). |
| **3. Origen e Ingesta (15 %)** | Ingesta directa y reproducible desde `SIRA\github` (`Sentinel-2`, `Landsat 7/8/9`, `HLS2`, `CHIRPS`, `NASA POWER`, `GEOS-S2S-3` NRT/Drift, 198 TDR, curva 10 años, 20.286 píxeles Modelo C y auditoría `IDEAM`). |
| **4. Almacenamiento (15 %)** | Parquet (Snappy) en S3 con particionamiento físico por patrón de consulta (`predio_id=/fecha=`, `anio=`, `version=`, `emision=`) y 13 tablas catalogadas en `sira_b_lakehouse_db`. |
| **5. Procesamiento Distribuido (20 %)** | 4 Jobs PySpark Glue 4.0 con `Window` (bruma, rezagos, fenología), `Broadcast Join` (calibración, clima por celda de 55 km, curva normal 10 años) y `applyInPandas` (balances recursivos B y C sin `collect()` masivo). |
| **6. Despliegue y Consumo (20 %)** | Metadatos versionados de A/B/C/Prithvi en `s3://.../models/`, 6 consultas SQL verificadas en Athena, mapas PNG + visor Leaflet `mapa.html` en S3 y microservicio dual `app_b_inference_api.py`. |
