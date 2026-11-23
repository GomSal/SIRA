# 04_B — Manual de Uso y Guía de Lectura Detallada (`B_SIRA_ARCH` Limpio · HEAD `876be25`)

**Proyecto:** SIRA — Sistema Inteligente de Riego para Arroz en el Alto Magdalena (Equipo 10, EAFIT)  
**Repositorio ML:** [`GomSal/SIRA@modelo-humedad`](https://github.com/GomSal/SIRA/tree/modelo-humedad) (commit **`876be25`**, emisión **`2026-11-19`**)  
**Lakehouse AWS:** `s3://sira-b-lakehouse-929570730700/` · **Base de Datos Athena:** `sira_b_lakehouse_db` (**13 tablas**)  
**API Live en Producción (Lovable):** `https://s435rizz22wj6qd3mv37doq76m0widfd.lambda-url.us-east-2.on.aws/`

---

## PARTE 1 · Cómo Leer la Estructura Conectada (ML ↔ Big Data ↔ App)

### 1.1 Prefijos Oficiales en Amazon S3 (`s3://sira-b-lakehouse-929570730700/`)

1. **`models/` — Modelos reales versionados:**
   - `models/pronosticador_a/rf/`: **`predictor_ndvi.joblib`** (92,2 MB) y **`predictor_ndmi.joblib`** (95,3 MB) reales (`sklearn 1.6.1`), más calibración Landsat→S2, curva normal 10 años y factores GEOS-S2S.
   - `models/semaforo_b/operacion/`: **`arimax.json`** ($\sigma = 3{,}12\,\%$, margen $4{,}68\text{ mm}$) y **`calendarios_riego.csv`** (CD, MIRI, AWD+MIRI de Nataima).
2. **`bronce/` y `plata/` — Datos crudos y limpios:**
   - `plata/cubo_a/`: cubo satelital real de 400 ha (`cubo.npz` 297,6 MB, `cubo_fechas.csv` hasta `2026-11-18`) y serie climática diaria `clima.csv` (`2001–2026`, lluvia POWER hasta `2026-11-19`).
   - `plata/clima_observado/` (`anio=2001..2026`): tabla Parquet generada por `sira-b-02` con lluvia CHIRPS + relleno NASA POWER (`PRECTOTCORR`) y ETo FAO-56.
   - `plata/indices_pixel/` y `plata/geos_s2s_crudo/`: series limpias particionadas para Athena.
3. **`oro/` — Salidas de negocio y consumo:**
   - `oro/geos_s2s_corregido/` (`emision=`): 5 miembros GEOS-S2S corregidos por `sira-b-02` con climatología sellada de 10 años (`2015-11-18..2025-11-17`), consumidos directamente por `sira-b-05`.
   - `oro/semaforo_c_pixeles/` (`emision=2026-11-19`, histórico `2026-11-15`): **20.286 píxeles agrícolas** en **40 columnas** (`predio_id`, `pixel`, `das_hoy`, `dias_hasta_baja`, y para `h = 0, 7, 15, 30`: `clase_h*`, `humedad_pct_h*`, `deficit_B_sin_margen_mm_h*`, `deficit_mm_h*`, `S_tau20_h*`, `dr_ecuacion_mm_h*`, `ndvi_h*`, `ndmi_h*`, `marca_h*`).
   - `oro/semaforo_c_resumen/` (`emision=2026-11-19`, histórico `2026-11-15`): **4 horizontes (`0, 7, 15, 30`)** en **25 columnas** incluyendo `modelo_b_sigma_pct` (`3,12`), `modelo_b_margen_mm` (`4,68`), `riego_supuesto`, `lluvia_hueco_dias_sin_chirps` (`50`) y `lluvia_hueco_relleno` (`power`).
   - `oro/semaforo_app/`: **fuente directa de la App Lovable**, actualizada automáticamente por `sira-b-05` con guardia monotónica:
     - `latest.json`: puntero atómico a la última emisión (`emision=2026-11-19`, `generado_por: "aws-glue:sira-b-05-inferencia-unificada-ml"`).
     - `zonas.json`: contorno del Tolima + 47 municipios + polígono de la zona piloto Nataima.
     - `emision=2026-11-19/semaforo.json`: **`formato: 2`**, **6.761 píxeles en cultivo** con `lat`/`lon` WGS84 y los **31 días completos (`h = 0..30`)**.
     - `emision=2026-11-19/simulador_riego.json`: payload compacto que permite a la Lambda `POST /regar_hoy` simular riegos con física exacta FAO-56 en `< 5 ms`.
     - `emision=2026-11-19/mapa.html` y `semaforo_h{00,07,15,30}.png`: visor interactivo Leaflet y mapas RGBA (también espejados en `oro/semaforo_c_mapas/`).
   - `oro/verificacion_clima_cultivo/`: **37 filas en 6 bloques** (incluyendo el Bloque 6 `6_PRUEBA_DINAMICA_B_VS_FAO56`).

### 1.2 Cómo Leer los Resultados Oficiales Vigentes (`2026-11-19` · Paridad 1:1 ML = Athena = Lambda)

| Horizonte | Fecha | Área en cultivo | 🟢 Suficiente | 🟡 Media | 🔴 Baja | 🟣 Alarma NDMI | Déficit / Volumen |
|---|---|---|---|---|---|---|---|
| **Hoy (`h=0`)** | 2026-11-19 | `67,61 ha` (`6.761 px`) | **`99,6 %`** | `0,4 %` | **`0,0 %`** | `11,4 %` | `0 L/m²` (`0 m³` en baja) |
| **`+7 d`** | 2026-11-26 | `67,38 ha` | `0,0 %` | `0,0 %` | **`100,0 %`** | `0,0 %` | `6,61 L/m²` (`4.457 m³` en baja) |
| **`+15 d`** | 2026-12-04 | `66,37 ha` | `0,0 %` | **`100,0 %`** | `0,0 %` | `0,0 %` | `1,92 L/m²` (`1.276 m³` en media) |
| **`+30 d`** | 2026-12-19 | `65,98 ha` | `0,0 %` | `0,0 %` | **`100,0 %`** | `0,0 %` | `6,63 L/m²` (`4.377 m³` en baja) |

- **Urgencia sin riego desde hoy (`2026-11-19`):** `0,0 %` ya hoy · **`1,6 %` en 1–2 días** (`111 px`, `1,11 ha`) · **`98,4 %` en 3–5 días** (`6.650 px`, `66,50 ha`) · `0,0 %` en 6–30 d / no llega.
- **Lluvia/ETo GEOS-S2S acumuladas:** `+7 d`: `3,9 / 25,3 mm` · `+15 d`: `39,9 / 55,2 mm` · `+30 d`: `79,0 / 113,3 mm`.
- **Histórico preservado en Athena:** la emisión previa `2026-11-15` (`68,68 ha`, `98,6 %` suf hoy, `87,8 %` en 3–5 d) permanece particionada en `oro_semaforo_c_resumen` y `oro_semaforo_c_pixeles`.

---

## PARTE 2 · Manual de Uso Operativo y Conexión con Lovable

### 2.1 Endpoints HTTPS Activos en Producción (CORS nativo único)

Base URL: **`https://s435rizz22wj6qd3mv37doq76m0widfd.lambda-url.us-east-2.on.aws`**

| Método y Ruta | Uso en la App Lovable | Respuesta / Latencia típica |
|---|---|---|
| `GET /health` | Monitoreo y verificación de despliegue | `200 OK` · estado + última emisión + lista de rutas |
| `GET /latest` | Detectar si hay una nueva emisión del pipeline AWS | `200 OK` · `317 B` · `emision`, `generado_por`, `subido_utc` |
| `GET /zonas` | Cargar polígono del Tolima, 47 municipios y zona piloto | `200 OK` · `28 KB` |
| `GET /resumen` | Tarjetas ejecutivas de 400 ha (`h=0, 7, 15, 30` + 31 días) | `200 OK` · `6,1 KB` |
| `GET /semaforo` | Pintar los **6.761 píxeles** del mapa en los **31 días** (`formato: 2`) | `200 OK` · **`232 KB` (gzip)** |
| `GET /semaforo_c?lat=4.1895&lon=-74.9621` | Inspeccionar un píxel puntual al hacer clic en el mapa | `200 OK` · `3,1 KB` |
| `POST /regar_hoy` | Simulador interactivo *«¿Qué pasa si riego X mm el día D?»* | `200 OK` · **`< 5 ms`** en caliente (`re_ejecuto_modelo_a: false`) |

Ejemplo `fetch()` desde Lovable para el botón **"Simular riego"**:
```javascript
const API = "https://s435rizz22wj6qd3mv37doq76m0widfd.lambda-url.us-east-2.on.aws";
const resp = await fetch(`${API}/regar_hoy`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ lat: 4.1895, lon: -74.9621, lamina_riego_mm: 30, dia_aplicacion: 2 }),
});
const sim = await resp.json();
// Emisión 2026-11-19: sim.escenario_sin_riego.dias_hasta_rojo === 3 -> sim.escenario_con_riego.dias_hasta_rojo === 6 (+3 días)
```

### 2.2 Operación Diaria del Pipeline Local y en AWS

```powershell
# 1. Traer últimos cambios de ML (solo lectura de modelo-humedad si trabajas con clon separado)
git -C github pull origin modelo-humedad

# 2. Ingesta local + corrida local de validación (13 tablas Parquet)
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\ingestion\fetch_b_bronze_data.py
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\deploy_and_run_b_pipeline.py --local-only

# 3. Sincronizar scripts, 2 Glue Jobs, 3 Step Functions, 3 Schedules, Athena y Lambda API en AWS (0 CloudFormation)
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\deploy_and_run_b_pipeline.py --aws-only

# 4. Probar localmente el cliente contra la Capa Oro real en S3
.\.venv\Scripts\python.exe B_SIRA_ARCH\bigdata_pipeline\api\app_b_inference_api.py
```

---

## PARTE 3 · Guion de Demostración en 5 Minutos (Sustentación Integrada ML + Big Data + App)

1. **Minuto 1 — Conexión Real ML → S3 (`sira-b-lakehouse-929570730700`):** Muestra `models/pronosticador_a/rf/*.joblib`, `models/semaforo_b/operacion/arimax.json`, `plata/cubo_a/cubo.npz` y `oro/semaforo_app/latest.json` apuntando a **`2026-11-19`** (generado automáticamente por `aws-glue:sira-b-05-inferencia-unificada-ml`).
2. **Minuto 2 — Arquitectura Depurada (2 Glue Jobs + Step Functions):** Abre AWS Glue Studio (solo 2 jobs limpios: `sira-b-02` y `sira-b-05` con `--fecha_hoy=auto`) y Step Functions `sira-b-flujo-geos-5dias` (`sira-b-02 → sira-b-05`). Confirma **`0 stacks`** en CloudFormation.
3. **Minuto 3 — Amazon Athena (`sira_b_lakehouse_db`, 13 tablas):** Corre `Q4` y `Q5` demostrando paridad 1:1 exacta con el equipo de ML y la App (`2026-11-19`: `99,6 %` verde hoy, `98,4 %` rojo en 3–5 d, `1,6 %` en 1–2 d, `20.286` píxeles totales, `6.761` en cultivo).
4. **Minuto 4 — API Serverless AWS Lambda (`sira-b-api-serving`):** Abre `/health`, `/latest`, `/zonas` y prueba `POST /regar_hoy` (`dia_aplicacion=2`, `30 mm`) mostrando la respuesta instantánea ganando `+3 días` antes de rojo sin re-ejecutar el Random Forest.
5. **Minuto 5 — App Lovable en Vivo:** Muestra la aplicación en producción consumiendo la Function URL con los 47 municipios del Tolima, el piloto de 400 ha en Nataima a 10 m y el deslizador de 31 días.
