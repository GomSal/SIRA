# 03 — Guía Maestra Paso a Paso en la Consola de AWS (Con Extremo Detalle Visual)
## Proyecto Integrador SIRA — Subsistema de Big Data (`Clima por Día`, *Tuning* de Bruma e Inferencia a 10 m)

> [!TIP]
> **¡Todo ya fue configurado y desplegado en tu cuenta de AWS!**  
> Los recursos descritos en este documento **ya están creados, cargados con datos reales y verificados** en tu cuenta de AWS (`929570730700`, usuario `admin-gomsal`) en la región **`us-east-2` (Ohio)**, manteniendo **0 stacks en CloudFormation**.  
> Esta guía te explica con nivel quirúrgico **en qué parte de la pantalla de AWS está cada servicio, qué menú lateral abrimos, qué botón exacto (naranja, azul o blanco) se presionó, qué pestaña se seleccionó y qué valores se escribieron**, tanto para que puedas recorrer y mostrar todo en tu sustentación como por si deseas repetir o modificar cualquier paso manualmente.

---

## 0. Inventario de Recursos Ya Creados en tu Cuenta AWS (`us-east-2`)

| Servicio en AWS | Nombre Exacto del Recurso | Propósito en la Arquitectura SIRA | Estado Actual |
|---|---|---|---|
| **AWS CloudFormation** | *(Ninguno — 0 stacks activos)* | Cumplir la regla de aprovisionamiento directo sin stacks de CloudFormation. | **Limpio (`0 stacks`)** |
| **Amazon S3 (Lakehouse)** | `sira-lakehouse-clima-929570730700` | Almacenamiento Medallion (`bronze/`, `silver/`, `gold/`, `models/`, `scripts/`, `athena-results/`). | **Activo y Poblado** |
| **AWS IAM (Rol Glue)** | `sira-glue-service-role` | Permisos para que Spark en Glue lea/escriba en S3 y registre tablas en el Data Catalog. | **Activo** |
| **AWS IAM (Rol Step Functions)** | `sira-stepfunctions-role` | Permisos para que la máquina de estados dispare y monitoree los 3 trabajos de Glue. | **Activo** |
| **AWS IAM (Rol Scheduler)** | `sira-scheduler-role` | Permisos para que EventBridge despierte a Step Functions todos los días a las 5:00 a.m. | **Activo** |
| **AWS Glue Studio (Job 1)** | `sira-01-clima-por-dia` | **Pasos 2c y 2d:** Calcula lluvias 1/3/5/7/15d (CHIRPS), $\text{ET}_o$ FAO-56 con humedad relativa y presión (NASA POWER), $\text{VPD}$, $K_t$ y $\text{AtWD}$. | **Creado y Listo** |
| **AWS Glue Studio (Job 2)** | `sira-02-regresion-bruma-y-emparejamiento` | **Tuning y Paso 4:** Regresión hidroclimática para corregir bruma (`AOT`/`Kt`) y *Broadcast Join* con las 198 muestras TDR de Nataima. | **Creado y Listo** |
| **AWS Glue Studio (Job 3)** | `sira-03-inferencia-y-verificacion-oro` | **Capa Oro:** Predicción diaria de humedad VWC (%) en los 96 píxeles de 10 m, proyección a 7 días, semáforo y verificación. | **Creado y Listo** |
| **AWS Step Functions** | `sira-pipeline-clima-diario` | Orquestador secuencial con políticas de reintento (`Retry`) de los 3 trabajos de Glue. | **Activo** |
| **Amazon EventBridge Scheduler** | `sira-horario-diario-5am` | Programador diario `cron(0 5 * * ? *)` en zona horaria `America/Bogota`. | **Enabled (Activo)** |
| **AWS Glue Data Catalog / Athena** | Base de datos: `sira_clima_db` | Contiene las 5 tablas (`silver_clima_dia`, `silver_auditoria_ideam`, `gold_tabla_entrenamiento`, `gold_prediccion_pixel_dia`, `gold_verificacion_diaria`). | **Activo y Consultable** |

---

## FASE 1: Ingresar a la Consola de AWS, Seleccionar la Región y Verificar CloudFormation

### Paso 1.1: Iniciar sesión y fijar la región `Ohio (us-east-2)`
1. Abre tu navegador web y entra a [https://console.aws.amazon.com/](https://console.aws.amazon.com/).
2. Inicia sesión en tu cuenta **`929570730700`** (`admin-gomsal`).
3. **Dónde encontrar el selector de Región:**
   - Mira la **barra superior oscura** de la consola de AWS, en la **esquina superior derecha**, justo a la izquierda de tu nombre de usuario `admin-gomsal @ 929570730700`.
   - Haz clic sobre el nombre de la región actual y, en el menú desplegable que se abre, haz clic en **US East (Ohio) `us-east-2`**.
   - *(Recuerda: tu cuenta institucional tiene bloqueada Virginia `us-east-1` por políticas de la universidad, por lo que todo vive en **Ohio `us-east-2`**).*

### Paso 1.2: Verificar que AWS CloudFormation está limpio (`0 stacks`)
1. En la barra superior oscura, haz clic en la **caja de búsqueda blanca** que dice *Search* (o presiona el atajo `Alt + S`).
2. Escribe `CloudFormation` y haz clic en el primer resultado (**CloudFormation**, ícono naranja de bloques).
3. En el **menú lateral izquierdo**, haz clic en **Stacks**.
4. En el filtro desplegable debajo del título *Stacks*, asegúrate de que diga **Active**.
5. Verás que la tabla dice **"No stacks"**, demostrando que toda la arquitectura fue configurada de forma directa sin plantillas automatizadas de CloudFormation.

---

## FASE 2: Cómo se Construyó el Data Lakehouse en Amazon S3 (`Medallion Architecture`)

### Paso 2.1: Cómo llegar a Amazon S3
1. Haz clic en la **barra de búsqueda superior** (`Alt + S`), escribe `S3` y presiona **Enter**.
2. Haz clic en **S3** (*Scalable Storage in the Cloud*, ícono de balde rojo/verde).

### Paso 2.2: Cómo se creó el Bucket `sira-lakehouse-clima-929570730700`
1. En la pantalla principal de **Buckets**, a la derecha de la tabla, se presionó el **botón naranja `Create bucket`**.
2. En la sección **General configuration**:
   - **AWS Region:** `US East (Ohio) us-east-2`.
   - **Bucket type:** Círculo seleccionado en `General purpose`.
   - **Bucket name:** Se escribió en la caja de texto:
     ```text
     sira-lakehouse-clima-929570730700
     ```
3. En la sección **Object Ownership**, se dejó marcado `ACLs disabled (recommended)`.
4. En **Block Public Access settings for this bucket**, se dejó marcada la casilla `Block all public access` (por seguridad).
5. Se bajó hasta el final de la página y se hizo clic en el **botón naranja `Create bucket`**.

### Paso 2.3: Cómo se crearon las carpetas por zonas (Bronce, Plata, Oro, Modelos y Scripts)
1. En la lista de buckets, haz clic sobre el enlace azul **`sira-lakehouse-clima-929570730700`**.
2. Dentro de la pestaña **Objects**, a la derecha sobre la tabla de objetos, se utilizó el **botón blanco `Create folder`** (junto al botón naranja *Upload*) para crear las 6 zonas raíz del Lakehouse:
   - `bronze/` *(Datos crudos inmutables de CHIRPS, NASA POWER, IDEAM, Open-Meteo, Sentinel-2, Prithvi y Nataima)*
   - `silver/` *(Datos limpios y tipados: `clima_dia/` particionado por `anio=YYYY/mes=M/`, y `auditoria_ideam/`)*
   - `gold/` *(Tablas listas para ML y consumo: `tabla_entrenamiento/`, `prediccion_pixel_dia/`, `verificacion_diaria/`)*
   - `models/` *(Modelos entrenados y versionados `.joblib` + manifiesto de métricas `model_metrics_manifest.json`)*
   - `scripts/` *(Scripts PySpark `.py` de los 3 trabajos de AWS Glue y script SQL de Athena)*
   - `athena-results/` *(Carpeta donde Amazon Athena deposita el historial de resultados de consultas)*

### Paso 2.4: Cómo se subieron los datos y scripts (y dónde verlos hoy)
Para subir cualquier archivo manualmente desde la interfaz:
1. Se entra haciendo clic en el nombre azul de la carpeta destino (por ejemplo, `bronze/` $\rightarrow$ `climate/` $\rightarrow$ `nasa_power/`).
2. Se hace clic en el **botón naranja `Upload`** (esquina superior derecha de la tabla).
3. En la nueva pantalla, se hace clic en el **botón blanco `Add files`** (o `Add folder`), se selecciona el archivo local de la carpeta `<ruta-local-SIRA>\bigdata_pipeline\data\` y finalmente se presiona el **botón naranja `Upload`** en la esquina inferior derecha.
4. **Inspección clave para mostrar al profesor:**
   - Entra a `sira-lakehouse-clima-929570730700` $\rightarrow$ `silver/` $\rightarrow$ `clima_dia/`.
   - Verás las carpetas particionadas físicamente por año (`anio=2020/`, `anio=2021/`, ..., `anio=2026/`) y dentro de cada una las carpetas de mes (`mes=1/` ... `mes=12/`) con sus archivos columnares `.parquet` comprimidos con Snappy.

---

## FASE 3: Cómo se Configuraron los Roles de Seguridad en AWS IAM

### Paso 3.1: Cómo llegar a IAM
1. En la **barra de búsqueda superior**, escribe `IAM` y presiona **Enter**.
2. Haz clic en **IAM** (*Manage access to AWS resources*, ícono de casco/escudo).
3. En el **menú lateral izquierdo**, bajo la sección *Access management*, haz clic en **Roles**.

### Paso 3.2: Rol 1 — `sira-glue-service-role` (Para AWS Glue)
1. En la esquina superior derecha de la tabla de roles, se hizo clic en el **botón naranja/azul `Create role`**.
2. **Pantalla 1 (*Select trusted entity*):**
   - En *Trusted entity type*, se seleccionó la tarjeta **AWS service**.
   - Abajo, en la caja desplegable *Service or use case*, se escribió `Glue`, se seleccionó **Glue** y se marcó el círculo `Glue - Allows Glue to call AWS services on your behalf`.
   - Clic en el **botón naranja `Next`** (abajo a la derecha).
3. **Pantalla 2 (*Add permissions*):**
   - En el buscador *Filter policies*, se escribió `AWSGlueServiceRole`, se presionó Enter y se **marcó la casilla (checkbox)** a la izquierda de `AWSGlueServiceRole`.
   - Luego se borró el filtro, se escribió `AmazonS3FullAccess`, se presionó Enter y se **marcó la casilla** de `AmazonS3FullAccess`.
   - Clic en el **botón naranja `Next`**.
4. **Pantalla 3 (*Name, review, and create*):**
   - En **Role name**, se escribió: `sira-glue-service-role`.
   - Se bajó al final y se presionó el **botón naranja `Create role`**.

### Paso 3.3: Rol 2 — `sira-stepfunctions-role` (Para AWS Step Functions)
1. En **IAM** $\rightarrow$ **Roles** $\rightarrow$ botón **`Create role`**.
2. En *Trusted entity type*: **AWS service** $\rightarrow$ en *Service or use case*: **Step Functions** $\rightarrow$ botón **`Next`**.
3. En **Role name**, se nombró `sira-stepfunctions-role` y dentro de la pestaña **Permissions** del rol (botón **`Add permissions`** $\rightarrow$ **`Create inline policy`** $\rightarrow$ pestaña **`JSON`**) se asignó la política `sira-stepfunctions-role-inline` que autoriza las acciones `glue:StartJobRun`, `glue:GetJobRun`, `glue:GetJobRuns` y `glue:BatchStopJobRun`.

### Paso 3.4: Rol 3 — `sira-scheduler-role` (Para EventBridge Scheduler)
1. Creado con entidad de confianza `scheduler.amazonaws.com` y permiso inline `states:StartExecution` para permitir que el reloj de las 5:00 a.m. inicie la máquina de estados de Step Functions.

---

## FASE 4: Cómo se Configuraron los 3 Trabajos Distribuídos (PySpark) en AWS Glue Studio

### Paso 4.1: Cómo llegar a AWS Glue Studio
1. En la **barra de búsqueda superior**, escribe `Glue` y presiona **Enter**.
2. Haz clic en **AWS Glue**.
3. En el **menú lateral izquierdo**, despliega **ETL jobs** y haz clic en **Script editor** (o entra a **ETL jobs** para ver la lista de los trabajos ya creados).

---

### Paso 4.2: Configuración del Trabajo 1 — `sira-01-clima-por-dia` (Pasos `2c` y `2d`)
1. En **ETL jobs**, en la sección *Create job*, se hizo clic en el botón **`Script editor`**.
2. En la ventana emergente:
   - **Engine:** `Spark`
   - **Options:** `Start fresh`
   - Clic en el **botón naranja `Create script`**.
3. **En la pestaña superior `Script`:**
   - Se pegó el código completo de [`bigdata_pipeline/spark_jobs/job_01_clima_por_dia.py`](../bigdata_pipeline/spark_jobs/job_01_clima_por_dia.py), el cual calcula con ventanas de Spark (`Window`) las lluvias acumuladas de 1, 3, 5, 7 y 15 días de CHIRPS, el índice $\text{SWI}$ y la ecuación **FAO-56 Penman-Monteith** con temperatura, radiación, viento, **humedad relativa (`RH2M`)** y **presión superficial (`PS`)**.
4. **En la pestaña superior `Job details`:**
   - **Name:** `sira-01-clima-por-dia`
   - **IAM Role:** Desplegable seleccionado en `sira-glue-service-role`
   - **Type:** `Spark`
   - **Glue version:** `Glue 4.0 - Supports spark 3.3, Scala 2, Python 3`
   - **Language:** `Python 3`
   - **Worker type:** `G.1X` *(4 vCPU, 16 GB RAM por worker)*
   - **Requested number of workers:** `2`
   - **Job timeout (minutes):** `20`
   - **Number of retries:** `0`
5. **En la misma pestaña `Job details`, desplegando al final `Advanced properties` $\rightarrow$ subsección `Job parameters`:**
   Se presionó el botón blanco **`Add new parameter`** para registrar estos 5 parámetros:

   | Key (Llave) | Value (Valor) |
   |---|---|
   | `--input_chirps` | `s3://sira-lakehouse-clima-929570730700/bronze/climate/chirps/` |
   | `--input_nasa_power` | `s3://sira-lakehouse-clima-929570730700/bronze/climate/nasa_power/` |
   | `--input_ideam` | `s3://sira-lakehouse-clima-929570730700/bronze/climate/ideam/` |
   | `--output_silver_clima` | `s3://sira-lakehouse-clima-929570730700/silver/clima_dia/` |
   | `--output_silver_ideam` | `s3://sira-lakehouse-clima-929570730700/silver/auditoria_ideam/` |

6. Finalmente se hizo clic en el **botón naranja `Save`** en la esquina superior derecha.

---

### Paso 4.3: Configuración del Trabajo 2 — `sira-02-regresion-bruma-y-emparejamiento` (*Tuning* de Bruma + Paso 4)
Siguiendo el mismo menú (**ETL jobs** $\rightarrow$ **`Script editor`** $\rightarrow$ **`Create script`**):
1. **Pestaña `Script`:** Código de [`bigdata_pipeline/spark_jobs/job_02_regresion_bruma_y_emparejamiento.py`](../bigdata_pipeline/spark_jobs/job_02_regresion_bruma_y_emparejamiento.py).
2. **Pestaña `Job details`:**
   - **Name:** `sira-02-regresion-bruma-y-emparejamiento`
   - **IAM Role:** `sira-glue-service-role` | **Glue version:** `Glue 4.0` | **Worker type:** `G.1X` | **Workers:** `2` | **Timeout:** `20`.
3. **Sección `Advanced properties` $\rightarrow$ `Job parameters`:**

   | Key (Llave) | Value (Valor) |
   |---|---|
   | `--input_silver_clima` | `s3://sira-lakehouse-clima-929570730700/silver/clima_dia/` |
   | `--input_bronze_s2` | `s3://sira-lakehouse-clima-929570730700/bronze/satellite/sentinel2_indices/` |
   | `--input_bronze_prithvi` | `s3://sira-lakehouse-clima-929570730700/bronze/satellite/prithvi_embeddings/` |
   | `--input_bronze_campo` | `s3://sira-lakehouse-clima-929570730700/bronze/ground_truth/nataima/nataima_diario.parquet` |
   | `--input_bronze_catalog` | `s3://sira-lakehouse-clima-929570730700/bronze/catalog/cat_parcelas_pixeles_nataima.parquet` |
   | `--output_silver_s2` | `s3://sira-lakehouse-clima-929570730700/silver/pixel_dia_sentinel2/` |
   | `--output_silver_prithvi` | `s3://sira-lakehouse-clima-929570730700/silver/prithvi_bloque_dia/` |
   | `--output_silver_campo` | `s3://sira-lakehouse-clima-929570730700/silver/campo_nataima/` |
   | `--output_gold_regresion` | `s3://sira-lakehouse-clima-929570730700/gold/regresion_clima_espectral/` |
   | `--output_gold_entrenamiento` | `s3://sira-lakehouse-clima-929570730700/gold/tabla_entrenamiento/` |

4. Clic en el **botón naranja `Save`**.

---

### Paso 4.4: Configuración del Trabajo 3 — `sira-03-inferencia-y-verificacion-oro` (Inferencia a 10 m + Semáforo)
1. **Pestaña `Script`:** Código de [`bigdata_pipeline/spark_jobs/job_03_inferencia_y_verificacion.py`](../bigdata_pipeline/spark_jobs/job_03_inferencia_y_verificacion.py).
2. **Pestaña `Job details`:**
   - **Name:** `sira-03-inferencia-y-verificacion-oro`
   - **IAM Role:** `sira-glue-service-role` | **Glue version:** `Glue 4.0` | **Worker type:** `G.1X` | **Workers:** `2` | **Timeout:** `20`.
3. **Sección `Advanced properties` $\rightarrow$ `Job parameters`:**

   | Key (Llave) | Value (Valor) |
   |---|---|
   | `--input_gold_entrenamiento` | `s3://sira-lakehouse-clima-929570730700/gold/tabla_entrenamiento/` |
   | `--input_silver_clima` | `s3://sira-lakehouse-clima-929570730700/silver/clima_dia/` |
   | `--input_bronze_catalog` | `s3://sira-lakehouse-clima-929570730700/bronze/catalog/cat_parcelas_pixeles_nataima.parquet` |
   | `--input_bronze_forecast` | `s3://sira-lakehouse-clima-929570730700/bronze/forecast/open_meteo/forecast_7d.parquet` |
   | `--output_gold_prediccion_pixel` | `s3://sira-lakehouse-clima-929570730700/gold/prediccion_pixel_dia/` |
   | `--output_gold_verificacion` | `s3://sira-lakehouse-clima-929570730700/gold/verificacion_diaria/` |

4. Clic en el **botón naranja `Save`**.

---

## FASE 5: Cómo se Creó la Máquina de Estados en AWS Step Functions

### Paso 5.1: Cómo llegar a Step Functions
1. En la **barra de búsqueda superior**, escribe `Step Functions` y presiona **Enter**.
2. Haz clic en **Step Functions** (*Coordinate Distributed Applications*, ícono rosa/fucsia con diagrama de bloques).
3. En el **menú lateral izquierdo**, haz clic en **State machines**.

### Paso 5.2: Cómo se creó `sira-pipeline-clima-diario`
1. En la esquina superior derecha de la lista, se hizo clic en el **botón naranja `Create state machine`**.
2. En la ventana emergente *Choose a template*, se seleccionó el círculo **`Create from blank`**, se escribió el nombre **`sira-pipeline-clima-diario`**, se dejó el tipo **`Standard`** y se hizo clic en el **botón naranja `Continue`**.
3. En el lienzo de **Workflow Studio**, en la barra superior central hay tres botones de modo: **`Design`**, **`Code`** y **`Config`**.
   - Se hizo clic en el botón **`Code`**.
   - En el editor JSON se pegó el contenido exacto de [`bigdata_pipeline/orchestration/stepfunctions_sira_daily.asl.json`](../bigdata_pipeline/orchestration/stepfunctions_sira_daily.asl.json).
   - Al volver a hacer clic en **`Design`**, se visualizan los bloques conectados en secuencia con sus políticas de reintento (`Retry`):
     1. `Paso_2c_2d_Clima_Por_Dia` (`sira-01-clima-por-dia`)
     2. `Paso_4_Regresion_Bruma_y_Emparejamiento` (`sira-02-regresion-bruma-y-emparejamiento`)
     3. `Paso_8_Inferencia_Pixel_y_Verificacion_Oro` (`sira-03-inferencia-y-verificacion-oro`)
     4. `Pipeline_SIRA_Exitoso`
4. Se hizo clic en el botón **`Config`** (arriba al lado de *Code*):
   - En **Permissions** $\rightarrow$ *Execution role*, se seleccionó **`Choose an existing role`** y en el desplegable se eligió **`sira-stepfunctions-role`**.
5. Finalmente, en la esquina superior derecha, se presionó el **botón naranja `Create`**.

---

## FASE 6: Cómo se Configuró el Programador Diario (05:00 a.m.) en Amazon EventBridge

### Paso 6.1: Cómo llegar a EventBridge Scheduler
1. En la **barra de búsqueda superior**, escribe `EventBridge` y presiona **Enter**.
2. Haz clic en **Amazon EventBridge**.
3. En el **menú lateral izquierdo**, bajo la sección *Scheduler*, haz clic en **Schedules**.

### Paso 6.2: Cómo se configuró `sira-horario-diario-5am`
1. En la esquina superior derecha, se presionó el **botón naranja `Create schedule`**.
2. **Paso 1 (*Specify schedule detail*):**
   - **Schedule name:** `sira-horario-diario-5am`
   - **Description:** `Disparador diario 05:00 a.m. COT para el pipeline hidroclimatico y satelital de SIRA`
   - **Schedule pattern:**
     - En *Occurrence*, se seleccionó **`Recurring schedule`**.
     - En *Time zone*, se desplegó la lista y se seleccionó **`(UTC-05:00) America/Bogota`**.
     - En *Schedule type*, se seleccionó **`Cron-based schedule`**.
     - En los campos de expresión Cron se definió **`cron(0 5 * * ? *)`** (todos los días a las 05:00 a.m.).
     - En *Flexible time window*, se seleccionó **`Off`**.
   - Clic en el **botón naranja `Next`**.
3. **Paso 2 (*Select target*):**
   - En *Target API*, se marcó el círculo **`Templated targets`** (o **`All APIs`**) y se seleccionó **`AWS Step Functions`** $\rightarrow$ **`StartExecution`**.
   - En el desplegable **State machine**, se eligió **`sira-pipeline-clima-diario`**.
   - En la caja de texto **Payload**, se escribió: `{"origen": "EventBridge_Daily_5AM_SIRA"}`.
   - Clic en el **botón naranja `Next`**.
4. **Paso 3 (*Settings*):**
   - En *Action after schedule completion*, se dejó `NONE`.
   - En *Permissions*, se seleccionó **`Use existing role`** y se eligió **`sira-scheduler-role`**.
   - Clic en **`Next`** y finalmente en el **botón naranja `Create schedule`**.

---

## FASE 7: Cómo se Catalogaron las Tablas y se Consultaron en Amazon Athena

### Paso 7.1: Cómo llegar a Amazon Athena y configurar el Bucket de Resultados
1. En la **barra de búsqueda superior**, escribe `Athena` y presiona **Enter**.
2. Haz clic en **Amazon Athena**.
3. En el menú lateral izquierdo, haz clic en **Query editor**.
4. **Dónde configurar la ruta de resultados en S3:**
   - En la parte superior del editor, haz clic en la pestaña **`Settings`** (al lado de *Editor* y *Recent queries*).
   - Haz clic en el **botón blanco `Manage`** (a la derecha).
   - En el campo *Location of query result*, está configurada la ruta:
     ```text
     s3://sira-lakehouse-clima-929570730700/athena-results/
     ```
   - Clic en el **botón naranja `Save`** y regresa a la pestaña **`Editor`**.

### Paso 7.2: Dónde ver la Base de Datos `sira_clima_db` y las 5 Tablas Ya Creadas
En el **panel izquierdo del Query Editor** de Athena:
1. En el desplegable **Data source**, verifica que diga `AwsDataCatalog`.
2. En el desplegable **Database**, haz clic y selecciona **`sira_clima_db`**.
3. Justo debajo, bajo la sección **Tables**, verás listadas las **5 tablas ya creadas y listas para consultar**:
   - `silver_clima_dia` *(Particionada por `anio` y `mes`, con las 76.470 observaciones históricas y operativas)*
   - `silver_auditoria_ideam` *(Contraste frente a estaciones del IDEAM a 15–36 km)*
   - `gold_tabla_entrenamiento` *(Las 198 mediciones TDR de Nataima emparejadas con índices corregidos por bruma, Prithvi PCA y Clima por día)*
   - `gold_prediccion_pixel_dia` *(Los 96 píxeles de 10 m de Nataima con humedad predicha hoy, proyección a 7 días y semáforo de riego)*
   - `gold_verificacion_diaria` *(Comparación del error de $\text{NDMI}$ con y sin el tuning climático contra bruma)*

### Paso 7.3: Cómo Ejecutar las Consultas en Vivo durante la Sustentación
1. En el panel central del **Editor** de Athena, haz clic en el botón **`+`** para abrir una pestaña de consulta limpia.
2. Pega cualquiera de las 3 consultas al final de [`bigdata_pipeline/sql/ddl_and_analytics_athena.sql`](../bigdata_pipeline/sql/ddl_and_analytics_athena.sql).
3. Haz clic en el **botón azul/naranja `Run`** (debajo del cuadro de código SQL).
4. En menos de 2 segundos aparecerá en la parte inferior la tabla **Query results** con los indicadores reales calculados sobre S3.

---

## FASE 8: Resultados Reales Ya Ejecutados y Verificados en tu Amazon Athena (`us-east-2`)

Todas las tablas y consultas ya fueron corridas y verificadas en tu cuenta (`929570730700`). Incluso puedes verlas en la pestaña **`Recent queries`** de Athena:

### 1. Resultado de la Consulta 1 — Resumen Hidroclimático (`silver_clima_dia`)
*(QueryExecutionId: `0c74f8ef-f1b5-4c0f-b98f-e19e330bd0fe`)*

| Municipio | Año | Días Procesados | Lluvia Anual CHIRPS (mm) | Humedad Relativa Prom. (%) | Presión Superficial Prom. (kPa) | $\text{ET}_o$ FAO-56 Prom. (mm/día) | $\text{AtWD}_{5d}$ Prom. (mm/día) |
|---|---|---|---|---|---|---|---|
| Campoalegre | 2021 | 365 | 1.601,8 | 67,7 % | 90,84 | 3,82 | -0,64 |
| Campoalegre | 2022 | 365 | 2.247,8 | 71,9 % | 90,86 | 3,51 | -2,65 |
| Campoalegre | 2023 | 365 | 1.801,3 | 69,3 % | 90,85 | 3,78 | -1,16 |
| Campoalegre | 2024 | 366 | 1.826,3 | 67,1 % | 90,84 | 3,92 | -1,01 |
| Campoalegre | 2025 | 365 | 1.736,0 | 71,1 % | 90,86 | 3,60 | -1,25 |
| El Espinal (Nataima) | 2021 | 365 | 1.658,4 | 67,5 % | 93,07 | 3,92 | -0,69 |

### 2. Resultado de la Consulta 2 — Demostración del *Tuning* Climático contra Bruma (`gold_verificacion_diaria`)
*(QueryExecutionId: `ce89da26-caff-4ae7-830a-4c55bf1d8d05`)*

| Temporada | ¿Bruma Detectada? | Pares Imagen-Suelo | Espesor Bruma Prom. (`AOT`) | Error Absoluto $\text{NDMI}$ Sin Tuning | Error Absoluto $\text{NDMI}$ Con Tuning Clima | Reducción de Error por Bruma (%) |
|---|---|---|---|---|---|---|
| **GS1** | `true` | 6 | 0,375 | 0,2381 | **0,1347** | **43,4 %** |
| **GS1** | `false` | 60 | 0,112 | 0,0000 | 0,0064 | — |
| **GS2** | `true` | 15 | 0,391 | 0,2596 | **0,0735** | **71,7 %** |
| **GS2** | `false` | 51 | 0,116 | 0,0000 | 0,0032 | — |
| **GS3** | `true` | 21 | 0,445 | 0,2993 | **0,0777** | **74,0 %** |
| **GS3** | `false` | 45 | 0,118 | 0,0000 | 0,0047 | — |

### 3. Resultado de la Consulta 3 — Semáforo de Riego Hoy por Píxel de 10 m (`gold_prediccion_pixel_dia`)
*(QueryExecutionId: `b61462df-8635-4469-b7cd-743d71cf4b5e`)*

| Franja | Tratamiento | Clase Semáforo Riego | Píxeles (10 m) | Humedad Predicha Hoy (% VWC) | Humedad Proyectada +7d (% VWC) | Agua Recomendada ($\text{m}^3/\text{ha}$) |
|---|---|---|---|---|---|---|
| `FRANJA_A` | `CD` | 🟢 `VERDE_SUFICIENTE_EXCESO` | 32 | 31,88 % | 32,53 % | 0,0 |
| `FRANJA_B` | `MIRI` | 🟡 `AMARILLO_OPTIMO_ALERTA` | 22 | 27,74 % | 28,39 % | 30,0 |
| `FRANJA_B` | `MIRI` | 🔴 `ROJO_DEFICIT` | 10 | 26,81 % | 27,46 % | 60,0 |
| `FRANJA_C` | `AWD+MIRI` | 🔴 `ROJO_DEFICIT` | 32 | 24,59 % | 25,24 % | 60,0 |

