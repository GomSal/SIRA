# 03_B — Guía Paso a Paso: Infraestructura Local y en AWS (`B_SIRA_ARCH` Limpio · HEAD `876be25`)

**Proyecto:** SIRA (Iteración B Unificada — `B_SIRA_ARCH`) — Equipo 10, Universidad EAFIT  
**Repositorio Fuente (GitHub):** [`GomSal/SIRA@modelo-humedad`](https://github.com/GomSal/SIRA/tree/modelo-humedad) (commit **`876be25`**, 22-nov-2026, emisión **`2026-11-19`**)  
**Cuenta AWS Activa:** `929570730700` (`admin-gomsal`) · **Región:** `us-east-2` (Ohio)  
**Regla de Aprovisionamiento:** **0 stacks en AWS CloudFormation** (creación 100 % directa vía `boto3` desde [`bigdata_pipeline/deploy_and_run_b_pipeline.py`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/B_SIRA_ARCH/bigdata_pipeline/deploy_and_run_b_pipeline.py))  
**Endpoint API Público en Producción (Lovable):** `https://s435rizz22wj6qd3mv37doq76m0widfd.lambda-url.us-east-2.on.aws/`

---

## Resumen de Evolución e Integración Real ML ↔ Big Data ↔ App (`8fde03d` → `876be25`)

| Commit / Hito | Cambio en ML / S3 | Cómo se Conectó y Depuró en AWS (`B_SIRA_ARCH`) |
|---|---|---|
| `d77ae9c` · `558117c` | Producto unificado `A (RF) + B (ARIMAX día 0 con margen 4,68 mm) + Dr FAO-56 (+30 d sin margen)`; guarda `arimax.json`, `calendarios_riego.csv` y prueba dinámica (`reports/b_reentreno/dinamica/`). Nuevo esquema de **39 columnas** en `pixeles.csv.gz`. | Creado **`sira-b-05-inferencia-unificada-ml`** (Glue 5.0, Python 3.11, `scikit-learn==1.6.1`), actualizado `oro_semaforo_c_pixeles` (40 cols), `oro_semaforo_c_resumen` (25 cols) y Bloque 6 en `oro_verificacion_clima_cultivo`. |
| `90a401b` · `86aa09b` · `318a6bc` | `exportar_app.py` + `subir_aws.py` + relleno hueco CHIRPS con NASA POWER (`PRECTOTCORR`) + `semaforo.json` en **`formato: 2`** (31 días diarios × píxeles con `lat`/`lon`). | `sira-b-02` conectado a `plata/cubo_a/clima.csv` (26 años) y `bronce/geos_s2s_a/geos_nrt.csv`; desplegada AWS Lambda **`sira-b-api-serving`** + Function URL con CORS nativo único y `gzip`. |
| `876be25` + Limpieza AWS (23-nov) | `actualizar_datos.py` trae nuevas escenas hasta `2026-11-18` y lluvia POWER hasta **`2026-11-19`** (hueco `50 d` POWER, `6.761` píxeles en cultivo). | **Depuración AWS:** eliminados 9 Glue Jobs obsoletos (`sira-*` de Iteración A + `sira-b-01/03/04`), 2 Step Functions viejas y 2 EventBridge Schedules viejos. Activado **`--fecha_hoy=auto`** + **guardia monotónica** en `sira-b-05` y fallback resiliente en Lambda `POST /regar_hoy`. |

---

## Inventario Completo y Limpio de Recursos Activos en AWS (`929570730700` · `us-east-2`)

| Servicio AWS | Nombre Exacto del Recurso | Rol Exacto en `B_SIRA_ARCH` Limpio |
|---|---|---|
| **AWS CloudFormation** | *(Ninguno — **`0 stacks`**)* | Verificado vía `cfn.list_stacks()` (Regla Cero CloudFormation). |
| **Amazon S3 (Lakehouse)** | `sira-b-lakehouse-929570730700` *(510 MB)* | Bucket 100 % privado (`BlockPublicAccess = True`): `bronce/`, `plata/` (incl. `plata/cubo_a/`), `oro/` (incl. `oro/semaforo_app/` y `oro/semaforo_c_mapas/`), `models/pronosticador_a/rf/`, `models/semaforo_b/operacion/`, `scripts/`. |
| **AWS IAM (4 Roles)** | `sira-glue-service-role`<br>`sira-stepfunctions-role`<br>`sira-scheduler-role`<br>**`sira-b-lambda-api-role`** | Roles de mínimo privilegio (el rol Lambda solo tiene `s3:GetObject` y `s3:ListBucket` acotados a `oro/semaforo_app/*` y `models/semaforo_b/operacion/*`). |
| **AWS Glue (2 Jobs)** | **`sira-b-02-lote-clima-y-geos`** (Glue 4.0 PySpark)<br>**`sira-b-05-inferencia-unificada-ml`** (Glue 5.0) | `sira-b-02` procesa los 26 años de clima (`2001–2026`) + GEOS-S2S con climatología sellada de 10 años.<br>`sira-b-05` (Python 3.11 · `sklearn==1.6.1` · `--fecha_hoy=auto`) ejecuta los `.joblib` reales + `arimax.json` + FAO-56, escribe Parquet Athena, exporta `oro/semaforo_app/` (`formato: 2`) + `simulador_riego.json`, protege `latest.json` con guardia monotónica y registra particiones en Glue Catalog. |
| **AWS Step Functions (3)** | `sira-b-flujo-diario-imagenes-inferencia`<br>`sira-b-flujo-geos-5dias`<br>`sira-b-flujo-recalibracion-mensual` | Diario: `sira-b-05` (`--fecha_hoy=auto`).<br>Cada 5 días: `sira-b-02 → sira-b-05` (`--fecha_hoy=auto`, `--prefer_oro_geos=true`).<br>Mensual: `sira-b-02`. |
| **EventBridge Scheduler (3)** | `sira-b-horario-diario-5am`<br>`sira-b-horario-geos-5dias`<br>`sira-b-horario-recalibracion-mensual` | Todos `ENABLED` en zona `America/Bogota` (`cron(0 5 * * ? *)`, `rate(5 days)`, `cron(0 6 1 * ? *)`). Los 2 horarios viejos de Iteración A fueron eliminados. |
| **Amazon Athena** | Base de datos `sira_b_lakehouse_db` | **13 tablas Parquet externas** (esquema actualizado 40/25 cols + 6 bloques verificación; emisiones `2026-11-15` y **`2026-11-19`**). |
| **AWS Lambda + Function URL** | **`sira-b-api-serving`** (Python 3.11 · 512 MB · 6,1 KB ZIP) | URL activa en Lovable: `https://s435rizz22wj6qd3mv37doq76m0widfd.lambda-url.us-east-2.on.aws/` · CORS nativo único (`8/8 PASS`) · `gzip` automático (`232 KB`) · `POST /regar_hoy` (`< 5 ms` en caliente) con fallback resiliente. |

---

## Recorrido de Auditoría en la Consola de AWS (`us-east-2`)

1. **CloudFormation (`0 stacks`):** confirmar `0 stacks` activos en `us-east-2`.
2. **Amazon S3 (`sira-b-lakehouse-929570730700`):** revisar `models/pronosticador_a/rf/` (`.joblib`), `models/semaforo_b/operacion/` (`arimax.json`), `plata/cubo_a/` (`cubo.npz`, `clima.csv`), `oro/semaforo_c_pixeles/emision=2026-11-19/` y `oro/semaforo_app/latest.json`.
3. **AWS Glue Studio (`ETL jobs`):** confirmar que solo existen **2 Jobs limpios** (`sira-b-02-lote-clima-y-geos` y `sira-b-05-inferencia-unificada-ml`) y revisar en `sira-b-05` los parámetros `--additional-python-modules = scikit-learn==1.6.1,joblib,pyproj,Pillow` y `--fecha_hoy = auto`.
4. **AWS Step Functions (`State machines`):** abrir **`sira-b-flujo-geos-5dias`** (`sira-b-02 → sira-b-05`) y **`sira-b-flujo-diario-imagenes-inferencia`** (`sira-b-05`).
5. **AWS Lambda (`Functions` → `sira-b-api-serving`):** abrir la Function URL (`/health` y `/latest`) directamente en el navegador y verificar respuesta JSON limpia en `emision=2026-11-19`.
6. **Amazon Athena (`Query editor` → `sira_b_lakehouse_db`):** correr `Q4` y `Q5` verificando paridad exacta con Lovable (`2026-11-19`: `99,6 %` suf hoy, `98,4 %` rojo en 3–5 d, `1,6 %` en 1–2 d, `20.286` píxeles totales).

---

## Evidencia Verificada en Vivo en AWS (`us-east-2` · 22–23 nov 2026)

### 1. Ejecuciones Verificadas de Jobs y Step Functions

| Recurso | Ejecución / RunId | Estado | Duración | Evidencia Verificada |
|---|---|---|---|---|
| `sira-b-05-inferencia-unificada-ml` | `jr_d394d0c384a8...dfa7` | **`SUCCEEDED`** | **`194 s`** | `--fecha_hoy=auto` resolvió **`2026-11-19`** desde `plata/cubo_a/clima.csv`, consumió `oro/geos_s2s_corregido`, generó `simulador_riego.json`, registró partición Athena y actualizó `latest.json` (`2026-11-23T05:00:00+00:00`). |
| `sira-b-flujo-geos-5dias` | `ml-bd-e2e-1795384261` | **`SUCCEEDED`** | `363 s` | Encadenamiento verificado `sira-b-02` (Clima 26a + GEOS) $\rightarrow$ `sira-b-05` (`"geos_fuente": "oro/geos_s2s_corregido"`). |
| `sira-b-flujo-recalibracion-mensual` | `audit-fix-1795361848` | **`SUCCEEDED`** | `135 s` | `sira-b-02` (Recalibración climática mensual). |
| **AWS CloudFormation** | `cfn.list_stacks()` | **`0 stacks`** | — | Regla Cero CloudFormation preservada al 100 %. |

### 2. Consultas Verificadas en Vivo en Amazon Athena (`sira_b_lakehouse_db`, 0 duplicados)

| # | Tabla Consultada | QueryId Verificado en Athena | Resultado Exacto en Vivo (`us-east-2`) |
|---|---|---|---|
| **Q1** | `plata_indices_pixel` | `69c8fab9-4404-4d80-adfc-7a4e34f9b99e` | `S2`: `4.680` filas (`NDVI 0,539`, `NDMI 0,160`) · `L` calibrado: `493` filas (`NDVI 0,577`). Total `5.173`. |
| **Q2** | `oro_geos_s2s_corregido` | `8f9ece55-2901-419c-95df-25294adb9892` | Emisión `2026-11-12`: **`5` miembros**, `225` filas, lluvia `3,52 → 2,67 mm/d`, `ETo = 3,78 mm/d`. |
| **Q3** | `plata_clima_observado` *(26 años)* | `0b15e50f-0a35-43f9-9b4d-80da2281af90` | Serie real `2001–2026` (`9.453` días): `2026`, `2025` (`1.762,0 mm`), `2024` (`1.419,1 mm`). |
| **Q4** | `oro_semaforo_c_resumen` *(emisión `2026-11-19`)* | **`98551748-bcc4-40b3-94e1-5c3330a28bda`** | `emision=2026-11-19`: `h=0` `67,61 ha` (**`99,6 %` suf · `0,4 %` med · `0,0 %` baja · `11,4 %` marca**, hueco `50 d power`); `h=7` `100 %` baja (`4.457 m³`); `h=15` `100 %` med (`1.276 m³`); `h=30` `100 %` baja (`4.377 m³`). También conserva histórico `2026-11-15`. |
| **Q5** | `oro_semaforo_c_pixeles` *(emisión `2026-11-19`)* | **`1fe3543f-4414-4937-9973-4383b43f4aee`** | `1_de_1_a_2_dias`: **`111` px** (`1,11 ha`, `1,6 %`) · `2_de_3_a_5_dias`: **`6.650` px** (`66,50 ha`, **`98,4 %`**) · `fuera_de_cultivo`: `13.525` px. **Total = `20.286` píxeles (`6.761` en cultivo, 0 duplicados).** |
| **Q6** | `oro_verificacion_clima_cultivo` | `f2ea70f6-0b02-44b8-9600-c92dd5202f46` | **`37` filas** cubriendo los **6 bloques oficiales** (incluyendo `6_PRUEBA_DINAMICA_B_VS_FAO56`). |

### 3. Pruebas en Vivo de AWS Lambda Function URL (`sira-b-api-serving` · `8/8 PASS` sobre `2026-11-19`)

Base URL: `https://s435rizz22wj6qd3mv37doq76m0widfd.lambda-url.us-east-2.on.aws`

| Prueba HTTP (con `Origin: https://sira.lovable.app`) | Estado | Tamaño / Codificación | Cabecera CORS (`ACAO`) | Resultado Verificado |
|---|---|---|---|---|
| `OPTIONS /regar_hoy` | `200 OK` | `0 B` | `['https://sira.lovable.app']` *(única)* | Preflight limpio |
| `GET /health` | `200 OK` | `661 B` | `['https://sira.lovable.app']` | `estado=OK`, `7` rutas |
| `GET /latest` | `200 OK` | `317 B` | `['https://sira.lovable.app']` | `emision=2026-11-19`, `generado_por=aws-glue:sira-b-05-inferencia-unificada-ml` |
| `GET /zonas` | `200 OK` | `28.208 B` | `['https://sira.lovable.app']` | Tolima + **47 municipios** + zona piloto Nataima |
| `GET /resumen` | `200 OK` | `6.146 B` | `['https://sira.lovable.app']` | `h0`: `99,6 %` suf · `0,4 %` med · `0,0 %` baja · `11,4 %` marca (`hueco=50 d power`) |
| `GET /semaforo` (`Accept-Encoding: gzip`) | `200 OK` | **`231.781 B` (`gzip`)** | `['https://sira.lovable.app']` | `formato=2`, **`6.761` píxeles × `31` días** (`1,42 MB` → `232 KB`) |
| `GET /semaforo_c?lat=4.1895&lon=-74.9621` | `200 OK` | `3.058 B` | `['https://sira.lovable.app']` | `h0=suficiente (-7,61 mm)`, `dias_rojo=3` |
| `POST /regar_hoy` (`30 mm`, `dia_aplicacion=2`) | `200 OK` | `3.435 B` | `['https://sira.lovable.app']` | `sin_riego rojo=d3` $\rightarrow$ `con_riego rojo=d6` (**`+3 días`**, `geos_fuente=oro/geos_s2s_corregido`) |
