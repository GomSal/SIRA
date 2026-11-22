# README — Componente de Big Data en SIRA y Alineación con la Rúbrica de Evaluación (`SI7006`)

**Proyecto Integrador 1:** SIRA (Sistema Inteligente de Riego para Arroz en el Alto Magdalena)  
**Universidad:** Universidad EAFIT — Maestría en Ciencia de Datos y Analítica  
**Materias Integradas:**  
- **SI7006** — Almacenamiento y Procesamiento de Grandes Datos (*Foco principal de este documento*)  
- **SI7009** — Aprendizaje Automático  
- **SI7007** — Visualización de Datos  
**Documentos Relacionados:** [`02_BigData_Arch.md`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/A_SIRA_ARCH/02_BigData_Arch.md) · `sira_flujo_entrenamiento.html` · [`HANDOVER.md`](https://github.com/GomSal/SIRA/blob/main/HANDOVER.md) · `s00271-025-01025-w.pdf` · `CRITERIOS_EVAL_PROYECTO_INTEGRADOR_1_unificado.docx`

---

## 1. Propósito y Alcance del Módulo de Big Data en SIRA

**SIRA** tiene como objetivo predecir la **humedad volumétrica del suelo ($\text{VWC}$) por píxel de 10 m** y generar recomendaciones oportunas de riego (🔴 **Déficit** $< 27,1\%$, 🟡 **Óptimo** $27,1\text{–}30,0\%$, 🟢 **Exceso** $> 30,0\%$) para prevenir el estrés hídrico y optimizar el uso del agua en los cultivos de arroz del Alto Magdalena, partiendo de la verdad de campo experimental del **C.I. Nataima de Agrosavia** (El Espinal, Tolima; Martínez-Vega et al. (2025)).

### ¿Qué problema real resuelve Big Data dentro del proyecto?

En SIRA existe una asimetría fundamental entre los datos de tierra y los datos satelitales/climáticos:
1. **Datos en tierra escasos y acotados en el tiempo:** Las mediciones reales de humedad del suelo con sonda TDR en Nataima corresponden a un fragmento temporal reducido (3 temporadas agrícolas entre 2021 y 2022: `GS1`, `GS2` y `GS3`, con **198 mediciones puntuales** sobre un lote de $0,96\text{ ha}$).
2. **Necesidad de operar con imágenes satelitales actuales y corregir bruma/baja resolución:** Para que el modelo de aprendizaje automático funcione hoy en día (2026+) sobre las parcelas arroceras, debe superar dos obstáculos:
   - La **bruma atmosférica** (cirros delgados, neblina y aerosoles frecuentes en el valle del Magdalena) que altera la reflectancia óptica sin activar siempre las máscaras binarias de nubes.
   - La **baja resolución espacial** de las fuentes de contexto ($30\text{ m}$ en HLS / bloques de $480\text{ m}$ en **Prithvi-EO-2.0** y $20\text{ m}$ en la banda $\text{SWIR}$ de Sentinel-2 frente a franjas de cultivo de $23,5\text{ m}$ de ancho).
3. **El puente desde Big Data:** A diferencia de las mediciones en tierra, **los datos climáticos (CHIRPS, NASA POWER e IDEAM) son diarios, masivos y abarcan décadas completas sin vacíos temporales**. El subsistema de Big Data ingesta, limpia, cruza y almacena estas series históricas masivas junto con el archivo multianual de imágenes satelitales para entrenar una **regresión hidroclimática-espectral** (nodo `Clima por día` en `sira_flujo_entrenamiento.html`). Esta regresión permite hacer el *tuning* del modelo de Machine Learning, corrigiendo desviaciones por bruma o resolución y proyectando el balance hídrico diario sobre la parcela real.

---

## 2. Objetivos Específicos del Componente de Big Data

1. **OBJ-BD1 · Ingesta Automatizada y Caracterización Multi-fuente:**  
   Construir un pipeline automatizado en lote (*batch*) que extraiga y valide datos de tres naturalezas:
   - **Clima diario masivo:** Precipitación satelital/estaciones de **CHIRPS v2.0** ($0,05^\circ$), variables meteorológicas y radiativas de **NASA POWER** ($0,5^\circ$, serie completa para FAO-56), auditoría histórica de estaciones **IDEAM** (`datos.gov.co`) y pronósticos a 7 días (**Open-Meteo**).
   - **Observación de la Tierra (Rásters Multiespectrales):** Escenas de **Sentinel-2 L2A** ($10\text{–}20\text{ m}$) y **HLS** ($30\text{ m}$, 6 bandas armonizadas Landsat 8/9 + Sentinel-2).
   - **Verdad de Campo:** Series digitalizadas y validadas de humedad TDR, riego diario y fenología del C.I. Nataima (2021–2022).

2. **OBJ-BD2 · Almacenamiento Distribuido Lakehouse por Zonas (Arquitectura Medallion):**  
   Implementar un **Data Lakehouse en Amazon S3** estructurado en tres zonas (**Bronce, Plata y Oro**) utilizando el formato transaccional abierto **Apache Iceberg** sobre archivos columnares **Apache Parquet** (compresión Snappy), gobernado por **AWS Glue Data Catalog** y consultable mediante **Amazon Athena** y **PostgreSQL (Supabase)**.

3. **OBJ-BD3 · Procesamiento Distribuido Hidroclimático y Espectral en PySpark:**  
   Ejecutar transformaciones distribuidas en **Apache Spark (AWS Glue)** que calculen:
   - **Paso `2c` (CHIRPS):** Ventanas móviles acumuladas de precipitación ($1, 3, 5, 7\text{ y }15\text{ días}$), media móvil de 5 días e índice de entrada reciente de agua ($\text{SWI}$) con decaimiento exponencial.
   - **Paso `2d` (NASA POWER):** Evapotranspiración de referencia diaria ($\text{ET}_o$) mediante la ecuación física **FAO-56 Penman-Monteith**, Déficit Hídrico Atmosférico ($\text{AtWD}$ de 5 días), Déficit de Presión de Vapor ($\text{VPD}$), Grados-Día de Crecimiento ($\text{GDD}$) e Índice de Claridad Solar ($K_t = R_s / R_{so}$).
   - **Pasos `1a–3b` (Satélite y Prithvi):** Enmascaramiento de nubes/sombras (`SCL` y `QA_PIXEL`), cálculo de $\text{NDMI}, \text{NDWI}, \text{NDVI}, \text{MNDWI}$ a $10\text{–}20\text{ m}$ y compresión PCA ($1.024 \rightarrow 10\text{–}20$ componentes) de los *embeddings* de **Prithvi-EO-2.0**.

4. **OBJ-BD4 · Regresión Clima-Satélite, *Tuning* de Bruma y Emparejamiento Espaciotemporal (`Paso 4`):**  
   Cruzar en paralelo el historial climático diario (`silver.clima_dia`) con el historial óptico para calibrar el corrector de bruma y baja resolución, y ejecutar el emparejamiento espaciotemporal (`Parcela × Fecha ±1 día` mediante *Broadcast Join* de polígonos) que genera `gold.tabla_entrenamiento` sin fuga de información (*Leave-One-Season-Out*).

5. **OBJ-BD5 · Despliegue en Nube, Persistencia de Modelos y Consumo Operativo (5:00 a.m.):**  
   Orquestar el flujo diario a las 5:00 a.m. con **EventBridge + Step Functions**, versionar los artefactos de los modelos (**A, B y C** + corrector climático) en S3, exponer el servicio de inferencia mediante API y publicar la capa Oro en **Supabase** para alimentar el mapa de calor diario ($10\text{ m}$), el semáforo de riego y los dos ciclos de verificación en **Lovable** y **Amazon Athena**.

---

## 3. Cumplimiento Detallado de la Rúbrica de Calificación (`SI7006`)

A continuación se detalla cómo el diseño y la implementación de SIRA responden a cada uno de los **7 criterios oficiales** de la materia **SI7006 — Almacenamiento y Procesamiento de Grandes Datos** según el documento `CRITERIOS_EVAL_PROYECTO_INTEGRADOR_1_unificado.docx`, apuntando al nivel **Excelente (4.5 – 5.0)**.

### Resumen Ejecutivo de Pesos y Estrategia de Cumplimiento (`SI7006`)

| # | Criterio de Evaluación (`SI7006`) | Peso | Exigencia Nivel Excelente (4.5 – 5.0) | Implementación Concreta en SIRA |
|---|---|---|---|---|
| **1** | **Arquitectura de referencia y flujo de datos** | **15 %** | Arquitectura explícita (Lakehouse por zonas), justificada por volumen, velocidad y latencia; diagrama E2E coherente. | Arquitectura **Lakehouse Medallion (Bronce–Plata–Oro)** en AWS S3 + Iceberg. Proceso *batch* diario (5:00 a.m.) justificado porque la humedad del suelo y las fuentes climáticas son diarias (no requieren streaming). Diagramas E2E en [`02_BigData_Arch.md`](https://github.com/GomSal/SIRA/blob/Big-Data_Arch/A_SIRA_ARCH/02_BigData_Arch.md) y `sira_flujo_entrenamiento.html`. |
| **2** | **Ambiente tecnológico** | **10 %** | Desplegado en nube/clúster (AWS S3, Glue, Athena) elegido por requerimientos y costo; reproducible y con IAM seguro. | Ecosistema **100 % Serverless en AWS** (`S3` + `Glue PySpark` + `Glue Data Catalog` + `Athena` + `Step Functions` + `EventBridge`) + `Colab GPU` (inferencia Prithvi) + `Supabase`. Costo mínimo por pago por uso y guías reproducibles paso a paso. |
| **3** | **Origen e ingesta de datos** | **15 %** | Fuentes caracterizadas (tipo, formato, volumen, frecuencia); modo de ingesta justificado, automatizado, con calidad y manejo de errores. | 6 fuentes reales caracterizadas (`CHIRPS`, `NASA POWER`, `IDEAM`, `Sentinel-2`, `HLS`, `Campo Nataima`). Ingesta *batch* automatizada con validación de rangos físicos, detección de huecos en IDEAM y reintentos con *exponential backoff*. |
| **4** | **Almacenamiento** | **15 %** | Almacenamiento distribuido (S3) por zonas, formato columnar (Parquet/Iceberg), particionamiento acorde a consultas y catálogo SQL/NoSQL justificado. | **S3** dividido en `bronze/`, `silver/`, `gold/`. Tablas **Apache Iceberg / Parquet (Snappy)** particionadas por `(anio, mes)`. Catálogo en **AWS Glue Data Catalog** (consultas analíticas en **Athena**) y **PostgreSQL en Supabase** (lectura OLTP $< 50\text{ ms}$ desde la app). |
| **5** | **Procesamiento distribuido** | **20 %** | Framework distribuido (PySpark/Spark SQL) para limpieza, ingeniería de características y preparación ML; paralelismo real sin colectar al driver; orquestación y métricas. | Jobs en **PySpark (AWS Glue)**: funciones de ventana (`Window`) para lluvias 1/3/5d y $\text{AtWD}$, cálculo vectorial de $\text{ET}_o$ FAO-56, filtrado de nubes/bruma, regresión climática y **Broadcast Join** de parcelas con 0 bytes de *shuffle* de la tabla grande. Orquestado con **Step Functions**. |
| **6** | **Despliegue de modelos y consumo** | **20 %** | Modelo persistido y versionado (S3/MLflow), publicado vía API funcional; resultados en motor SQL/NoSQL y consumidos desde dashboard o consultas. | Modelos **A, B y C** + PCA y regresor climático persistidos y versionados en `s3://.../models/`. Inferencia expuesta por **API REST (FastAPI / AWS Lambda)**, resultados escritos en `gold.prediccion_pixel_dia` (Athena) y sincronizados en **Supabase** para el dashboard **Lovable**. |
| **7** | **Sustentación** | **30 %*** | Cada integrante explica y defiende las decisiones de arquitectura e implementación y demuestra el pipeline funcionando. | Defensa estructurada con evidencia empírica: por qué se descartó IDEAM ($-36\%$ a $-63\%$ de sesgo y sin `GS2`) y ERA5 ($+142\%$), cómo la regresión climática corrige la bruma y demostración en vivo del pipeline y la app. |

*\*Nota: Se conservan los pesos textuales del documento preliminar `CRITERIOS_EVAL_PROYECTO_INTEGRADOR_1_unificado.docx`.*

---

### Desarrollo Punto por Punto para Obtener 5.0 en Cada Criterio de `SI7006`

#### Criterio 1: Arquitectura de Referencia y Flujo de Datos (15 %)
- **Arquitectura elegida:** **Data Lakehouse con Arquitectura Medallion (Bronce $\rightarrow$ Plata $\rightarrow$ Oro)** combinada con una capa de *Serving* híbrida (Analítica en **Amazon Athena** + Operacional en **Supabase PostgreSQL**).
- **Justificación por Volumen, Velocidad y Latencia frente a alternativas (Lambda / Kappa):**
  - **Velocidad y Latencia:** La humedad volumétrica del suelo en el cultivo de arroz cambia en escala de días, las pasadas satelitales ocurren cada 5 a 8 días (Sentinel-2 / Landsat) y las bases climáticas globales (CHIRPS / NASA POWER) se publican con cadencia diaria. Por tanto, **una arquitectura Kappa o Lambda con colas de streaming continuo (Kafka / Kinesis) es innecesaria y sobrecostosa**. Una arquitectura **Batch Diaria** ejecutada a las 5:00 a.m. cumple el requerimiento de negocio entregando el mapa de calor antes de que el agricultor abra las compuertas de riego a las 6:00 a.m.
  - **Volumen:** Mientras que la verdad de campo en Nataima es pequeña (198 registros TDR), el archivo histórico diario de clima (1981–2026, $>16.500$ días por grilla) cruzado con las escenas multiespectrales de Sentinel-2 ($10\text{ m}$) y HLS ($30\text{ m}$) sobre el Alto Magdalena genera millones de registros píxel-día que requieren almacenamiento desacoplado en S3 y tablas columnares Iceberg.

#### Criterio 2: Ambiente Tecnológico (10 %)
- **Selección de Servicios por Requerimientos y Costo:**
  - **Amazon S3:** Almacenamiento de objetos de alta durabilidad ($99,999999999\%$) y bajo costo ($\approx \$0,023\text{ USD/GB-mes}$) para rásters COG y archivos Parquet.
  - **AWS Glue (PySpark Serverless) + Glue Data Catalog:** Elimina el costo de mantener clústeres EMR encendidos 24/7; solo se facturan los minutos de ejecución del lote diario.
  - **Google Colab GPU (T4 / L4):** Utilizado de forma específica para la inferencia pesada del *backbone* congelado **Prithvi-EO-2.0 (300M parámetros)** sobre los recortes HLS, exportando los *embeddings* hacia S3.
  - **Amazon Athena:** Motor SQL *serverless* que consulta directamente las tablas Iceberg en S3 cobrando únicamente por terabyte escaneado (minimizado gracias a la compresión Snappy y el particionamiento `anio/mes`).
  - **Supabase (PostgreSQL) + Lovable:** Base de datos relacional gestionada con soporte geoespacial para servir en $< 50\text{ ms}$ la cuadrícula actual de $10\text{ m}$ y capturar eventos del usuario (botón *«Regué hoy»*).
- **Seguridad y Reproducibilidad:** Roles IAM con principio de mínimo privilegio (`sira-glue-role`, `sira-stepfunctions-role`), variables de entorno y secretos fuera del código fuente, y scripts de aprovisionamiento y ejecución documentados paso a paso.

#### Criterio 3: Origen e Ingesta de Datos (15 %)
- **Caracterización Exhaustiva de Fuentes Reales:**
  1. **CHIRPS v2.0:** Externa · Ráster GeoTIFF / JSON · Resolución $0,05^\circ$ · Actualización diaria · Serie 1981–2026.
  2. **NASA POWER:** Externa (API REST) · JSON · Resolución $0,5^\circ$ · Actualización diaria · Serie 1981–2026 con 0 % de vacíos.
  3. **IDEAM (`datos.gov.co` `s54a-sgyg`):** Externa (API SODA) · JSON/CSV · Estaciones puntuales en Tolima · Usada para auditoría de calidad y contraste regional.
  4. **Sentinel-2 L2A (ESA Copernicus / GEE):** Externa · Ráster GeoTIFF COG (`B3, B4, B8` a $10\text{ m}$; `B8A, B11, SCL, AOT` a $20\text{ m}$) · Frecuencia cada 5 días.
  5. **HLS v2.0 (`HLSL30` / `HLSS30` NASA):** Externa · Ráster GeoTIFF COG ($30\text{ m}$, 6 bandas armonizadas + `Fmask`) · Frecuencia cada 2–4 días combinados.
  6. **Verdad de Campo C.I. Nataima:** Científica (Martínez-Vega et al. (2025)) · CSV (`nataima_diario.csv`) · 198 mediciones TDR validadas con 11 pruebas automatizadas en `pytest`.
- **Validaciones en Ingesta:** Verificación de esquemas (*Schema-on-Read*), validación de rangos biofísicos (reflectancias $\in [0, 1]$, humedad relativa $\in [0, 100]\%$, radiación solar $R_s \ge 0$, precipitación $\ge 0\text{ mm}$), control de duplicados por `(grid_id, fecha)` y manejo de errores HTTP en llamadas a las APIs de NASA POWER y ClimateSERV.

#### Criterio 4: Almacenamiento (15 %)
- **Organización por Zonas en S3:**
  - **Zona Bronce (`bronze/`):** Datos crudos inmutables tal como llegan de las fuentes (JSON de APIs climáticas, GeoTIFFs de recortes satelitales y CSV digitalizado del artículo), particionados por fuente y fecha de ingesta.
  - **Zona Plata (`silver/`):** Cinco tablas tipadas y limpias en formato **Apache Iceberg / Parquet**: `silver.clima_dia`, `silver.auditoria_ideam`, `silver.pixel_dia_sentinel2`, `silver.prithvi_bloque_dia` y `silver.campo_nataima`.
  - **Zona Oro (`gold/`):** Tablas refinadas listas para consumo analítico y de ML: `gold.regresion_clima_espectral`, `gold.tabla_entrenamiento`, `gold.prediccion_pixel_dia` y `gold.verificacion_diaria`.
- **Justificación SQL Analítico (Athena/Iceberg) + SQL Transaccional (Supabase PostgreSQL):**
  - **Iceberg + Athena (OLAP):** Ideal para escaneos masivos columnares de series históricas de clima (1981–2026), cálculo de agregaciones por campaña agrícola y entrenamiento de modelos.
  - **PostgreSQL en Supabase (OLTP):** Ideal para consultas puntuales de baja latencia desde la aplicación web **Lovable** (leer las ~95 celdas de $10\text{ m}$ del día actual en milisegundos e insertar en tiempo real el registro cuando el agricultor presiona el botón *«Regué hoy»*).

#### Criterio 5: Procesamiento Distribuido (20 %)
- **Uso Correcto de Paralelismo en PySpark (Sin `collect()` ni pandas en el driver):**
  1. **Ventanas deslizantes distribuidas (`Window.partitionBy("grid_id").orderBy("fecha")`):** Cálculo paralelo de lluvias acumuladas ($1, 3, 5, 7, 15\text{ días}$), medias móviles de 5 días y Déficit Hídrico Atmosférico ($\text{AtWD}$).
  2. **Operaciones columnares vectorizadas:** La ecuación completa de **FAO-56 Penman-Monteith** ($\Delta, \gamma, e_s, e_a, \text{VPD}, R_n, K_t, \text{ET}_o$) y los índices espectrales ($\text{NDMI}, \text{NDWI}, \text{NDVI}, \text{MNDWI}$) se ejecutan con funciones nativas de `pyspark.sql.functions` (`F.exp`, `F.sqrt`, `F.pow`, `F.greatest`), aprovechando el motor *Catalyst Optimizer* y *Tungsten* de Spark.
  3. **Broadcast Hash Join Espaciotemporal:** Al unir los millones de registros históricos de píxeles/clima con el catálogo de polígonos de parcelas y franjas de tratamiento (`CD`, `MIRI`, `AWD+MIRI`), se transmite (*broadcast*) la tabla liviana de geometrías/parcelas a los ejecutores (`F.broadcast(df_parcelas)`), logrando **0 MB de tráfico de red por *shuffle*** de la tabla masiva.
  4. **Orquestación Automatizada:** Máquina de estados en **AWS Step Functions** disparada diariamente por **Amazon EventBridge Scheduler**, con bloques paralelos y políticas de reintento (`Retry`).

#### Criterio 6: Despliegue de Modelos y Consumo (20 %)
- **Persistencia y Versionado:** Los artefactos del pipeline de modelado —el reductor `PCA` de Prithvi, el modelo de regresión climática corrector de bruma y los modelos supervisados de humedad (**Modelo A: Índices + Clima**, **Modelo B: A + Prithvi**, **Modelo C: Prithvi + cabeza lineal**)— se serializan y versionan en `s3://sira-lakehouse-alto-magdalena/models/` junto con su manifiesto JSON de hiperparámetros, métricas de validación *Leave-One-Season-Out* (`RMSE` y `macro-F1`) y estadísticas de estandarización ($\mu, \sigma$ de entrenamiento).
- **Publicación vía API y Consumo Dual:**
  - Un endpoint de inferencia (**FastAPI / AWS Lambda**) carga el modelo campeón desde S3, recibe el vector de un píxel/parcela (índices satelitales + `Clima por día` + días desde siembra + último riego) y devuelve la humedad predicha $\hat{\theta}_{\text{VWC}}$ (%), la clase del semáforo y la recomendación en $\text{L/m}^2$.
  - Los resultados diarios quedan disponibles simultáneamente para **consultas SQL analíticas en Amazon Athena** (histórico y tablas de verificación) y en **Supabase $\rightarrow$ App Lovable** (mapa de calor interactivo para el agricultor).

#### Criterio 7: Sustentación (30 %)
- El equipo cuenta con trazabilidad completa y argumentos cuantitativos verificables para defender cada decisión técnica:
  - Por qué la resolución de $23,5\text{ m}$ de las franjas de Nataima obliga a combinar **Sentinel-2 ($10\text{ m}$)** para el detalle intraparcela con **Prithvi-EO-2.0 ($30\text{ m}$ HLS)** para el contexto macroambiental.
  - Por qué se eligió **CHIRPS** ($+14\%$ de sesgo, usando acumulados de 1, 3 y 5 días) y **NASA POWER** (0 % de vacíos para FAO-56) descartando **IDEAM** (estaciones a 15–36 km sin datos en `GS2` y sesgo de $-36\%$ a $-63\%$) y **ERA5-Land** ($+142\%$ de sesgo).
  - Cómo la **regresión climática diaria** permite ajustar (*tuning*) los errores de bruma y baja resolución para predecir hoy en día sobre cualquier lote usando el conocimiento aprendido en Nataima 2021–2022.

---

## 4. Articulación con las Rúbricas de Aprendizaje Automático (`SI7009`) y Visualización (`SI7007`)

El pipeline de Big Data está diseñado para alimentar directamente los criterios de evaluación de las otras dos asignaturas del Proyecto Integrador:

| Materia | Criterios Clave de la Rúbrica | Cómo el Pipeline de Big Data Garantiza el Nivel Excelente (4.5 – 5.0) |
|---|---|---|
| **SI7009 · Aprendizaje Automático** | • **1. Formulación (15 %)**<br>• **2. EDA e Ing. de Características (15 %)**<br>• **3. Entrenamiento y Validación sin fuga (25 %)**<br>• **4. Métricas y Diagnóstico (15 %)**<br>• **5. Métrica de Negocio (15 %)**<br>• **6. Caso Aplicado (10 %)** | • **Ingeniería de características reproducible:** Las variables climáticas (`lluvia_1d, 3d, 5d`, `eto_fao56_mm`, `atwd_5d_mm`, `swi`), la corrección de bruma ($\text{NDMI}_{\text{tuned}}$) y la compresión PCA de Prithvi ($1.024 \rightarrow 15$) se generan en un pipeline auditable.<br>• **Cero fuga de información (*Data Leakage*):** La tabla `gold.tabla_entrenamiento` conserva el identificador de temporada (`GS1`, `GS2`, `GS3`) para ejecutar validación **Leave-One-Season-Out (3 vueltas)**, calculando medias, desviaciones y PCA únicamente sobre las 2 temporadas de entrenamiento.<br>• **Impacto de negocio cuantificado:** Traduce la predicción de humedad en ahorro de agua ($48\%$ a $65\%$ con `AWD+MIRI` frente a los $20.000\text{ m}^3/\text{ha}$ de `CD`). |
| **SI7007 · Visualización de Datos** | • **1. Argumentación Visual (20 %)**<br>• **2. Construcción del Mensaje (20 %)**<br>• **3. Dashboard Interactivo (30 %)**<br>• **4. Sustentación (30 %)** | • **Entrega de baja latencia para exploración:** La capa Oro y Supabase alimentan la aplicación **Lovable** con el mapa de calor de ~95 celdas de $10\text{ m}$ sobre Nataima, el semáforo de riego (🔴/🟡/🟢), la barra de confianza según la antigüedad de la última imagen despejada, el botón interactivo *«Regué hoy»* y los paneles de verificación diaria (pronóstico vs. observado). |

---

## 5. Estructura Recomendada del Repositorio para el Componente Big Data

Para mantener una separación limpia respecto a actividades académicas previas y garantizar mantenibilidad hacia el lanzamiento real de SIRA, se organiza el código bajo la siguiente estructura modular:

```text
SIRA/
├── 02_BigData_Arch.md                           # Arquitectura detallada de Clima (CHIRPS/POWER/IDEAM) y Tuning
├── README_BigData.md                            # Objetivos de Big Data y matriz de cumplimiento de rúbrica SI7006
├── HANDOVER.md                                  # Estrategia integral, decisiones técnicas y estado del proyecto
├── sira_flujo_entrenamiento.html                # Diagrama interactivo oficial del flujo de entrenamiento
├── bigdata_pipeline/
│   ├── ingestion/
│   │   ├── fetch_chirps_daily.py                # Descarga automatizada de precipitación CHIRPS v2.0
│   │   ├── fetch_nasa_power_daily.py            # Cliente API REST Agroclimatology de NASA POWER
│   │   ├── fetch_ideam_audit.py                 # Extracción SODA API datos.gov.co (s54a-sgyg) para auditoría
│   │   └── fetch_openmeteo_forecast.py          # Ingesta diaria de pronóstico meteorológico (+7 días)
│   ├── spark_jobs/
│   │   ├── job_01_clima_por_dia.py              # PySpark: Pasos 2c (lluvia 1/3/5d) y 2d (ETo FAO-56 y AtWD) -> Silver
│   │   ├── job_02_satelite_indices_prithvi.py   # PySpark: Pasos 1a, 2a, 1b, 2b, 3b (Índices S2 + PCA Prithvi) -> Silver
│   │   ├── job_03_regresion_bruma_y_join.py     # PySpark: Tuning de bruma climática + Paso 4 (Emparejar) -> Gold
│   │   └── job_04_inferencia_y_verificacion.py  # PySpark/Batch 5am: Predicción diaria + Ciclos 1 y 2 -> Gold & Supabase
│   ├── orchestration/
│   │   └── stepfunctions_sira_daily.asl.json    # Máquina de estados para ejecución batch diaria (5:00 a.m.)
│   ├── sql/
│   │   ├── ddl_iceberg_tables.sql               # Creación de tablas Silver y Gold en AWS Glue Catalog / Athena
│   │   └── analytics_queries_athena.sql         # Consultas de verificación climática, sesgo IDEAM y estrés hídrico
│   └── api/
│       └── app_inference_api.py                 # Microservicio FastAPI / Lambda para inferencia y botón "Regué hoy"
```
