# 04_B — Manual de Uso y Guía de Lectura Detallada (`B_SIRA_ARCH` · HEAD `8fde03d`)

**Proyecto:** SIRA — Sistema Inteligente de Riego para Arroz en el Alto Magdalena (Equipo 10, EAFIT)  
**Repositorio GitHub Local:** [`la raíz del repositorio (`./`)`](../) (`GomSal/SIRA`, rama `modelo-humedad`, commit `8fde03d`)  
**Lakehouse AWS:** `s3://sira-b-lakehouse-929570730700/` · **Base de Datos Athena:** `sira_b_lakehouse_db` (**13 tablas**)

> **Propósito de este documento:** Explicar en lenguaje claro **cómo saber leer cada pieza del sistema** (qué dice cada carpeta en S3, qué significa cada tabla y cada columna en Athena, cómo leer los mapas y colores del Semáforo C, cómo interpretar las respuestas JSON de la API) y entregar el **manual de uso completo** para actualizar, ejecutar y demostrar la plataforma.

---

## PARTE 1 · Cómo Saber Leer la Estructura (Qué Dice Cada Parte)

### 1.1 Mapa de Carpetas Locales: ¿Dónde está cada cosa?
```text
<ruta-local-SIRA>\
├── github\           ← TODO lo del repositorio GitHub (clon oficial rama modelo-humedad).
│                        No edites datos a mano aquí; se actualiza solo con `git pull`.
├── A_SIRA_ARCH\      ← Iteración A archivada (arquitectura inicial, solo histórico).
└── B_SIRA_ARCH\      ← Iteración B vigente (código Big Data, scripts AWS, API y manuales).
```

### 1.2 Cómo Leer las 3 Capas en Amazon S3 (`s3://sira-b-lakehouse-929570730700/`)
Piensa en S3 como una **línea de preparación en 3 pisos** (Arquitectura Lakehouse Medallion):

1. **`bronce/` — Ingredientes crudos (tal como llegaron del repositorio y satélites):**
   - `sentinel2/` y `landsat/`: reflectancias e índices crudos por píxel (10 m y 30 m) antes de limpiar bruma ni calibrar sensores.
   - `hls/`: manifiesto de escenas HLS2 ($224\times 224$ px, 30 m) reservadas **solo** para que `Prithvi-EO-2.0` rellene nubes si hoy está nublado.
   - `chirps/` y `power/`: lluvia y meteorología diaria histórica observada.
   - `geos_s2s/`: pronóstico climático de la NASA (`geos_s2s_nrt.parquet`, 41.805 filas) y su climatología histórica de sesgo (`geos_s2s_drift.parquet`, 8.008 filas).
   - `campo_nataima/` e `ideam_auditoria/`: las 198 mediciones reales TDR del artículo de Martínez-Vega et al. (2025) y la auditoría que documenta por qué se descartó IDEAM.
   - `semaforo_c_repo/`: espejo crudo de los 20.286 píxeles producidos por `github/scripts/operacion/semaforo_c.py`.

2. **`plata/` — Datos limpios, calibrados y listos para modelar (4 tablas en Athena):**
   - **`plata_indices_pixel`** (`predio_id=.../fecha=...`): es el insumo exacto del **Modelo A**.
     - `ndvi0`, `ndmi0`: vegetación y humedad foliar actuales del píxel en escala Sentinel-2 (Landsat ya fue calibrado con $S2 = a + b\cdot L$).
     - `sensor0`: `0` = Sentinel-2, `1` = Landsat 7, `2` = Landsat 8/9.
     - `flag_bruma` y `origen_valor`: `False` / `observado` indica que pasó el filtro de bruma (sin caídas abruptas $>0{,}10$ ni banda azul `azul_b02 ≥ 0,075` sobre vegetación).
     - `ndvi_l1`, `edad_l1`, `ndmi_l1`, `ndvi_l2`, `edad_l2`: los 2 últimos valores despejados del mismo píxel y cuántos días atrás ocurrieron (topes 60 d y 90 d).
     - `pend_ndvi`, `pend_ndmi`: velocidad de cambio diario del cultivo ($\Delta\text{índice}/\Delta\text{días}$); positiva = cultivo creciendo, negativa = senescencia o secado.
     - `fecha_siembra_est`, `das0`, `etapa0`: fecha de siembra detectada automáticamente por la curva de NDVI, días desde siembra y etapa (`0` fuera de ciclo, `1` vegetativa $\le 45\text{ d}$, `2` reproductiva $\le 82\text{ d}$, `3` maduración $\le 130\text{ d}$).
     - `doy_sin`, `doy_cos`: posición cíclica del día en el año (captura estacionalidad bimodal del Tolima).
   - **`plata_clima_observado`** (`anio=...`): lluvia diaria `lluvia_mm` (CHIRPS con respaldo POWER) y demanda evaporativa `eto_mm` (FAO-56 Penman-Monteith), más acumulados `5d` y `10d`.
   - **`plata_geos_s2s_crudo`** (`emision=...`): pronóstico crudo de los 5 miembros de GEOS-S2S-3 antes de corregir sesgo (`pr_raw_mm`, `tx_raw_c`, `tn_raw_c`) por `plazo` (días desde la emisión).
   - **`plata_estado_hidrico_pixel`**: **la memoria diaria del suelo** al corte de hoy. Guarda los valores arrastrados de cada píxel para no recomputar toda la campaña desde el día 0 cada mañana:
     - `s_tau20_init`, `s_tau5_init`, `dr_k05_init`: memoria para el **Modelo B oficial (`ARIMAX(1)`)**.
     - `s_tau45_a05_init`, `s_tau60_a05_init`: memoria de largo plazo ($\alpha = 0{,}5$, $\tau = 45$ y $60\text{ d}$) ganadoras del **Reentreno B Etapa 1 (commit `8fde03d`)**.
     - `dias_desde_riego_init`, `riego_4d_prev_sum`, `eto_4d_prev_sum`: contadores y buffers de los 4 días previos.

3. **`oro/` — Productos finales de negocio, semáforos, mapas y auditoría (9 tablas + mapas):**
   - **`oro_correccion_coeficientes`** (`version=v2026_11`): factores multiplicativos (`variable='pr'`) y aditivos en °C (`variable='t'`) por mes (`1..12`) y bloque de plazo (`0`: 0–10 d, `1`: 11–20 d, `2`: 21–60 d). Si un factor de `pr` es `0,62`, significa que GEOS-S2S sobreestima la lluvia en ese mes y se multiplica por `0,62` para llevarlo a la escala de CHIRPS.
   - **`oro_geos_s2s_corregido`** (`emision=...`): lluvia (`lluvia_corr_mm`) y ETo (`eto_corr_mm`, escalada por temperatura media sobre la climatología FAO-56) ya corregidas para los 5 miembros de GEOS-S2S.
   - **`oro_curva_normal_10anios`**: la «curva sana de referencia» del arroz en el Alto Magdalena construida con 10 años de imágenes (16 tramos cada 10 días desde la siembra: `das_b = 0..150`). `ndmi0_mean` y `ndmi0_std` dicen cuál es el NDMI normal del cultivo a esa edad.
   - **`oro_pronostico_predictor`** (`emision=...`): salida del **Modelo A (`Random Forest`)**. Para cada `pixel_id` y `horizonte` (`1..30` días), entrega `ndvi_pred` y `ndmi_pred` en escala Sentinel-2 (sin depender del clima).
   - **`oro_semaforo`** (`emision=...`): salida del **Modelo B oficial (`ARIMAX(1)` para fincas CON riego registrado)**.
     - Cada fila es un `pixel_id × miembro (1..5) × escenario_riego ('sin_riego' o 'riego_30mm_hoy') × horizonte (1..30)`.
     - Cómo leer las métricas: `vwc_esperada_pct` es la humedad volumétrica esperada del suelo (%); `p_verde`, `p_amarillo`, `p_rojo` suman `1,0` y representan la probabilidad de cada clase (`VERDE` $\ge 30\%$, `AMARILLO` $27{,}1\text{–}30\%$, `ROJO` $< 27{,}1\%$); `s_tau45_a05` y `s_tau60_a05` muestran la reserva hídrica de memoria larga (mayor valor = más agua almacenada).
   - **`oro_semaforo_c_pixeles`** (`emision=2026-11-12`): salida del **Modelo C operativo (para operación SIN riego registrado — 20.286 píxeles, 400 ha)**.
     - `dr0_mm`: déficit inicial hoy (mm) estimado comparando el NDMI de hoy contra `oro_curva_normal_10anios`. `0,0 mm` = suelo lleno; `4,34 mm` = ya está en umbral crítico de déficit.
     - `dias_hasta_baja`: **cuántos días faltan para que el píxel entre en humedad baja (rojo) si no se riega**. `0` = regar ya hoy; `4` = entra a rojo en 4 días; `-1` = no llega a rojo en los próximos 30 días.
     - `clase_h0`, `clase_h7`, `clase_h15`, `clase_h30`: clase del semáforo hoy, a 7, 15 y 30 días:
       - `0` = 🟢 **Suficiente** ($Dr \le 0\text{ mm}$, humedad $\ge 30\%$)
       - `1` = 🟡 **Media** ($0 < Dr \le 4{,}34\text{ mm}$, humedad $27{,}1\text{–}30\%$)
       - `2` = 🔴 **Baja** ($Dr > 4{,}34\text{ mm}$, humedad $< 27{,}1\%$ — **regar ya**)
       - `-1` = Fuera de cultivo (barbecho o cosechado).
     - `dr_mm_h*`: mediana del déficit radicular (mm) entre los 5 miembros de GEOS-S2S. Equivale directamente a **litros por metro cuadrado ($\text{L/m}^2$)** faltantes para volver a capacidad de campo.
     - `p_baja_h*`: fracción de los 5 miembros de GEOS-S2S que predicen humedad baja (`0,0` = 0/5 miembros; `0,6` = 3/5; `1,0` = los 5 miembros coinciden en déficit). Mide la **certeza climática de la alerta**.
     - `marca_h*` (`true`/`false`): **alerta morada fitosanitaria**. Cuando es `true`, el suelo tiene agua suficiente (`clase = 0`), pero el NDMI de la hoja está en el 10 % más seco incluso descontando el error del Modelo A $\Rightarrow$ **revisar en campo por posible enfermedad o deficiencia nutricional**.
   - **`oro_semaforo_c_resumen`** (`emision=2026-11-12`): tabla ejecutiva de 4 filas (`horizonte = 0, 7, 15, 30`) que resume las 400 ha: `ha_en_cultivo` (68,68 ha hoy), `%` y `ha` en cada clase, `deficit_baja_L_m2`, volumen total de agua requerido en la zona (`agua_baja_m3_zona` en $\text{m}^3$), `dias_hasta_baja_mediana` y `pct_marca_fitosanitaria`.
   - **`oro_semaforo_c_mapas/` (en S3):** contiene los 4 mapas RGBA (`semaforo_h00.png`, `h07`, `h15`, `h30`), `resumen.json` y **`mapa.html`**, un visor web interactivo (Leaflet sobre imagen satelital Esri) con deslizador de horizonte (`hoy`, `7 d`, `15 d`, `30 d`).
     - **Cómo leer los colores en `mapa.html`:** 🟢 Verde = humedad suficiente (no regar) · 🟡 Amarillo = humedad media (programar riego en los días indicados) · 🔴 Rojo = humedad baja (regar ya, muestra $\text{L/m}^2$ y $\text{m}^3$) · 🟣 Morado = hoja seca con suelo húmedo (revisar enfermedad/nutrientes).
   - **`oro_verificacion_clima_cultivo`**: tablero de 33 filas con los **5 bloques oficiales de validación del proyecto** (`1_CLIMA_GEOS_S2S`, `2_MODELO_A_NDVI/NDMI_ABSOLUTO`, `3_SEMAFORO_B_ADELANTE`, `4_MODELO_B_FAMILIAS_ROC` y `5_REENTRENO_B_ETAPA1`). Compara siempre `valor_crudo_o_base` contra `valor_corregido_o_modelo`.
   - **`oro_predios`**: ficha maestra de cada finca (coordenadas, área, celda GEOS-S2S asignada y parámetros hidráulicos del suelo `FC`, `PWP`, `TAW`, `RAW`, `DR_ROJO`, `DSAT`, `k_drenaje`). Al escalar de 1 a 100 predios solo se agregan filas aquí.

---

## PARTE 2 · Manual de Uso Operativo (Paso a Paso)

### 2.1 Sincronizar las últimas actualizaciones desde GitHub (`SIRA\github`)
Cada vez que el equipo de ML suba nuevos commits a la rama `modelo-humedad`:
```powershell
cd <ruta-local-SIRA>
git -C github pull origin modelo-humedad
```

### 2.2 Ejecutar el Pipeline Completo Local (Sin tocar AWS)
```powershell
# Paso 1: Ingestar datos reales desde SIRA\github hacia Bronce (Parquet)
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\ingestion\fetch_b_bronze_data.py

# Paso 2: Procesar localmente los 4 Lotes (construye las 13 tablas Plata y Oro + parámetros ARIMAX(1))
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\deploy_and_run_b_pipeline.py --local-only
```

### 2.3 Reorganizar / Desplegar en AWS (`us-east-2`) y Validar en Athena
```powershell
# Sube Bronce, Plata, Oro, Modelos, 4 Scripts PySpark y Mapas PNG/HTML a S3,
# actualiza 4 Glue Jobs, 3 Step Functions, 3 EventBridge Schedules y valida las 13 tablas + 6 queries en Athena:
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\deploy_and_run_b_pipeline.py --aws-only
```
*(También puedes correr local + AWS en un solo comando omitiendo las banderas).*

### 2.4 Probar el Microservicio API Dual (`Modelo B /regar_hoy` + `Modelo C /semaforo_c`)
```powershell
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\api\app_b_inference_api.py
```

**Cómo leer las 2 respuestas JSON de la API:**
1. **`PRUEBA 1: MODELO B (POST /regar_hoy)`** *(finca con riego registrado)*:
   - Confirma `"re_ejecuto_modelo_a": false` (demuestra que cambiar el riego recalcula en $<50\text{ ms}$ sin re-ejecutar el satélite ni el Random Forest A).
   - Compara `escenario_sin_riego` vs. `escenario_con_riego` (`lamina_riego_mm = 30.0`): observa cómo sube `vwc_esperada_pct`, baja `p_rojo` (con banda `p_rojo_rango_5m` de los 5 miembros GEOS-S2S) y aumenta la reserva `s_tau45_a05_medio` (de `~20,6` a `~50,6 mm`).
2. **`PRUEBA 2: MODELO C (GET /semaforo_c)`** *(operación sin riego registrado)*:
   - Muestra el estado real de un píxel entre los **20.286 píxeles** de las 400 ha (`pixel = 75149`: hoy `h0` está `suficiente` con `dr0_mm = 0,0`, entra en `baja` en **`4 días` sin riego**, y a `h7` llega a `dr_mm = 6,7` con `p_baja = 1,0` en 5/5 miembros).
   - Incluye el resumen zonal `dias_hasta_baja_sin_riego_pct_zona`: `35,7 %` ya hoy, `7,5 %` en 1–2 d, `54,5 %` en 3–5 d, `0,7 %` en 6–30 d, `1,6 %` no llega en 30 d, más la ruta S3 del visor `mapa.html`.

### 2.5 Abrir el Visor Interactivo de Mapas (Modelo C) en el Navegador
Puedes abrir directamente la copia local del visor interactivo generado desde el repositorio:
- [`B_SIRA_ARCH/bigdata_pipeline/data/oro/semaforo_c_mapas/mapa.html`](bigdata_pipeline/data/oro/semaforo_c_mapas/mapa.html)  
Mueve el deslizador superior entre **`hoy`**, **`7 d`**, **`15 d`** y **`30 d`** para ver cómo evolucionan sobre la imagen satelital de Nataima las 400 ha y las tarjetas de déficit en $\text{L/m}^2$ y $\text{m}^3$.

---

## PARTE 3 · Guion de Demostración en 5 Minutos (Sustentación `SI7006`)

1. **Minuto 1 — Orden del Repositorio y Evolución (`SIRA\github` → `A_SIRA_ARCH` → `B_SIRA_ARCH`):** Explica la separación del repo GitHub en `github/` y por qué pasamos de 1 modelo a **3 modelos desacoplados**: A (NDVI/NDMI 1–30 d sin clima/Prithvi), B (`ARIMAX(1)` oficial con riego + `S_tau45_a05` del reentreno) y C (ecuación física FAO-56 para operar sin riego registrado sobre 400 ha).
2. **Minuto 2 — Lakehouse Medallion en S3 (`sira-b-lakehouse-929570730700`):** Muestra `bronce/`, `plata/indices_pixel/predio_id=NATAIMA_01/fecha=...`, `plata/estado_hidrico_pixel/`, `oro/semaforo/`, `oro/semaforo_c_pixeles/` y `oro/semaforo_c_mapas/mapa.html`.
3. **Minuto 3 — 4 Glue Jobs + 3 Step Functions (`Parallel`) + 3 EventBridge Schedules:** Abre `sira-b-flujo-geos-5dias` en Step Functions para mostrar el bloque **`Parallel`** actualizando en simultáneo Semáforo B (`sira-b-03`) y Semáforo C (`sira-b-04`), justificando con `SPARK_ECUACION.md` cuándo entra Spark (`1 vs. 100 predios`). Confirma `0 stacks` en CloudFormation.
4. **Minuto 4 — Amazon Athena (`sira_b_lakehouse_db`):** Corre las Consultas 4, 5 y 6 de [`ddl_and_analytics_b_athena.sql`](bigdata_pipeline/sql/ddl_and_analytics_b_athena.sql) mostrando en vivo las 400 ha de Modelo C (20.286 píxeles) y el tablero de los 5 bloques de verificación.
5. **Minuto 5 — API Dual + Mapa Interactivo:** Ejecuta `app_b_inference_api.py` (Modo B `/regar_hoy` + Modo C `/semaforo_c`) y abre `mapa.html` deslizando de hoy a 30 días.
