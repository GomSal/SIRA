# SIRA · Rama `Big-Data_Arch` (Arquitectura Lakehouse AWS)

Esta rama contiene **exclusivamente la arquitectura, pipelines, infraestructura AWS y documentación de Big Data** del proyecto **SIRA** (Equipo 10 · Universidad EAFIT · `SI7006`), completamente autocontenida y lista para que cualquier colaborador la ejecute o despliegue en AWS.

> **Otras ramas del repositorio:**
> * **Portada maestra y guía de replicación integral:** [`main`](https://github.com/GomSal/SIRA/tree/main)
> * **Código y entrenamiento de Machine Learning:** [`modelo-humedad`](https://github.com/GomSal/SIRA/tree/modelo-humedad)
> * **Carpetas base de documentación consolidada:** [`General-Documentation`](https://github.com/GomSal/SIRA/tree/General-Documentation)

---

## Diagrama de Flujo Big Data (`B_SIRA_ARCH`)

![Diagrama de Flujo General Big Data SIRA](Diagrama_flujo.jpeg)

---

## Estructura de esta Rama

```text
Big-Data_Arch/
├── README.md                         # Esta guía rápida de la rama Big Data
├── Diagrama_flujo.jpeg               # Diagrama general Lakehouse Medallion + Parallel B || C
├── requirements_bigdata.txt          # Dependencias Python exactas del subsistema Big Data
├── B_SIRA_ARCH/                      # ITERACIÓN B (VIGENTE — AWS us-east-2, 0 CloudFormation)
│   ├── README_B_BigData.md           # Resumen ejecutivo y rúbrica SI7006
│   ├── 02_B_BigData_Arch.md          # Arquitectura detallada, 20+7 agregados y criterio Spark
│   ├── 03_B_Guia_Paso_a_Paso_AWS_Consola.md   # Cómo se creó paso a paso + evidencia 3/3 Step Functions & 42/42 Athena
│   ├── 04_B_Manual_de_Uso_E2E.md     # Cómo leer cada carpeta/tabla/mapa + manual de uso
│   └── bigdata_pipeline/             # Ingesta, 4 Glue PySpark Jobs, 3 Step Functions, 13 tablas Athena, API Dual y datos Parquet
└── A_SIRA_ARCH/                      # ITERACIÓN A (Archivada para trazabilidad histórica)
    ├── README_BigData.md · 02_... · 03_... · 04_... · Diagrama_flujo_A.jpeg
    └── bigdata_pipeline/
```

---

## Recursos Desplegados y Verificados en AWS (`us-east-2` · `0 stacks` CloudFormation)

| Servicio | Recursos Activos (`sira-b-*`) | Estado Verificado |
|---|---|---|
| **Amazon S3** | `s3://sira-b-lakehouse-929570730700/` (`bronce/`, `plata/`, `oro/`, `oro/semaforo_c_mapas/`, `models/`, `scripts/`) | Deduplicado (`clear_s3_prefix`) |
| **AWS Glue 4.0 (4 Jobs)** | `sira-b-01-lote-imagenes-pixel` · `sira-b-02-lote-clima-y-geos` · `sira-b-03-lote-inferencia-ab` · `sira-b-04-lote-semaforo-c` | `4/4 SUCCEEDED` |
| **AWS Step Functions (3)** | `sira-b-flujo-recalibracion-mensual` (`135 s`) · `sira-b-flujo-diario-imagenes-inferencia` (`499 s`) · `sira-b-flujo-geos-5dias` (`256 s`, `Parallel[03 \|\| 04]`) | `3/3 SUCCEEDED` en vivo |
| **EventBridge Scheduler (3)** | `sira-b-horario-diario-5am` · `sira-b-horario-geos-5dias` · `sira-b-horario-recalibracion-mensual` (`America/Bogota`) | `ENABLED` |
| **Amazon Athena** | Base `sira_b_lakehouse_db` (`13` tablas Parquet externas + `6` consultas analíticas) | `42/42 SUCCEEDED` |

---

## Ejecución Rápida para Colaboradores

```bash
git clone -b Big-Data_Arch https://github.com/GomSal/SIRA.git SIRA-BigData
cd SIRA-BigData
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1   |   Linux/macOS: source .venv/bin/activate
pip install -r requirements_bigdata.txt

# 1. Corrida local autocontenida (construye las 13 tablas Plata y Oro en Parquet)
python B_SIRA_ARCH/bigdata_pipeline/deploy_and_run_b_pipeline.py --local-only

# 2. Probar API Dual (<50 ms: POST /regar_hoy + GET /semaforo_c)
python B_SIRA_ARCH/bigdata_pipeline/api/app_b_inference_api.py

# 3. Desplegar / sincronizar en AWS (usa `aws configure` o env var AWS_CREDENTIALS_CSV)
python B_SIRA_ARCH/bigdata_pipeline/deploy_and_run_b_pipeline.py --aws-only
```

*(Opcional — para re-ingestar archivos crudos desde un clon actualizado de la rama `modelo-humedad`, exporta `SIRA_REPO_PATH=/ruta/a/modelo-humedad` y ejecuta `python B_SIRA_ARCH/bigdata_pipeline/ingestion/fetch_b_bronze_data.py`).*

---

## Documentación de la Rama
* [Resumen Ejecutivo y Rúbrica `SI7006`](B_SIRA_ARCH/README_B_BigData.md)
* [Arquitectura Big Data Detallada](B_SIRA_ARCH/02_B_BigData_Arch.md)
* [Guía Paso a Paso de Creación Local y AWS](B_SIRA_ARCH/03_B_Guia_Paso_a_Paso_AWS_Consola.md)
* [Guía de Lectura Detallada y Manual de Uso E2E](B_SIRA_ARCH/04_B_Manual_de_Uso_E2E.md)
* [Iteración A Archivada (`A_SIRA_ARCH`)](A_SIRA_ARCH/README_BigData.md)
