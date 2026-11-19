# SIRA: modelo predictor (A) en producción y almacenamiento de datos

**Qué es:** el modelo predictor pronostica el **NDVI y el NDMI de cada pixel de 1 a 30 días adelante**. Con eso, el semáforo (B) dice "si no riegas, en N días pasas a amarillo o rojo".

**Cómo funciona:** todo corre **por lotes** (batch), sin tiempo real ni flujo continuo. No se usan Kafka ni Kinesis. Cada lote se dispara cuando llega un dato nuevo: una imagen de satélite, un pronóstico de GEOS-S2S o el clima observado.

---

## 1. Flujo de producción

Se lee de arriba hacia abajo. **Cilindros** = datos que entran. **Rectángulos** = procesos. **Paralelogramos** = lo que se guarda.

```mermaid
flowchart TB
  %% ---------- entradas ----------
  SAT[("Imágenes nuevas<br/>Sentinel-2 L2A (10–20 m) y Landsat 8/9 C2 L2 (30 m)<br/>Planetary Computer, sin cuenta")]
  GEOS[("GEOS-S2S-3 (NASA GMAO)<br/>pronóstico nuevo cada 5 días, 5 miembros, 81 días<br/>lluvia, T máx, T mín · celda de 0,5°")]
  OBS[("Clima observado<br/>CHIRPS (lluvia) + NASA POWER (ETo FAO-56)")]
  RIEGO[("Riego del agricultor<br/>escenario: 'si no riego' / 'si riego X mm'")]

  %% ---------- lote 1: imágenes ----------
  SAT --> L1["Lote 1 · diario: buscar escenas nuevas sobre la ventana<br/>máscara de nubes por pixel (SCL / QA) + control de bruma<br/>NDVI y NDMI · Landsat calibrado a la escala de Sentinel-2"]
  L1 --> GAP["Relleno de huecos<br/>(el método elegido en validación: Prithvi o interpolación)<br/>cada valor marcado: observado o estimado"]
  GAP --> SER[/"Serie de NDVI y NDMI por pixel<br/>+ fecha de siembra detectada por la curva"/]

  %% ---------- lote 2: clima pronosticado ----------
  GEOS --> L2["Lote 2 · cada 5 días: leer la celda del lote (5 miembros)<br/>aplicar la corrección guardada → escala CHIRPS / POWER<br/>ETo con Hargreaves calibrada contra FAO-56"]
  COEF[/"Coeficientes de corrección<br/>(con versión)"/] --> L2
  L2 --> CLIF[/"Clima pronosticado y corregido<br/>por fecha de emisión, miembro y día"/]

  %% ---------- lote 3: recalibrar ----------
  OBS --> L3["Lote 3 · mensual: comparar lo pronosticado con lo observado<br/>recalcular el sesgo por horizonte<br/>medir el error de lluvia y ETo"]
  CLIF --> L3
  L3 --> COEF

  %% ---------- unir y pronosticar ----------
  OBS --> CLIO[/"Clima observado hasta hoy"/]
  SER --> UNIR
  CLIO --> UNIR
  CLIF --> UNIR
  UNIR["Unir por (fecha de emisión, fecha objetivo)<br/>el mismo clima para todos los pixeles de la ventana<br/>días ya pasados = observado · días futuros = GEOS-S2S corregido"]
  UNIR --> A["Modelo predictor (A)<br/>entradas: historia del índice + días desde siembra<br/>+ lluvia y ETo acumuladas de t a t+h<br/>corre una vez por miembro → rango de incertidumbre"]
  A --> PA[/"NDVI y NDMI por pixel<br/>h = 1 … 30 días, con rango"/]

  %% ---------- semáforo ----------
  PA --> B["Semáforo (B)<br/>balance de agua con Kc(NDVI), lluvia y ETo pronosticadas<br/>+ riego como escenario"]
  CLIF --> B
  RIEGO --> B
  B --> MAPA[/"Semáforo futuro por pixel<br/>verde ≥ 30 % · amarillo 27,1–30 % · rojo < 27,1 %<br/>'si no riegas, en N días pasas a rojo'"/]
```

---

## 2. Los tres lotes y su ritmo

| Lote | Cada cuánto | Qué dispara | Qué hace | Qué guarda |
|---|---|---|---|---|
| **1. Imágenes** | Diario | Una escena nueva de Sentinel-2 o Landsat en el catálogo | Nubes y bruma, índices, calibración de Landsat, relleno de huecos | Serie por pixel |
| **2. Clima pronosticado** | Cada 5 días | Un pronóstico nuevo de GEOS-S2S (la NASA lo publica unos 4 días después de su fecha de inicio) | Leer la celda del lote, corregir y calcular la ETo | Clima corregido |
| **3. Recalibración** | Mensual | Ya existe lo observado de los días pronosticados | Comparar pronóstico contra observado y actualizar los coeficientes | Coeficientes con versión + error por horizonte |
| **Predictor + semáforo** | Cuando cambia una entrada (lote 1 o 2) | Serie nueva o clima nuevo | Pronosticar el NDVI y NDMI, y luego el semáforo | Pronóstico por pixel y semáforo |

**Por qué la corrección va aparte del predictor:**
- El predictor se entrena con **clima observado**, que tiene décadas de historia (CHIRPS desde 1981; POWER desde 1981 y con radiación desde 1984).
- En producción recibe GEOS-S2S **corregido a esa misma escala**.
- Si la NASA cambia la versión de GEOS-S2S, solo se recalibra la corrección; el predictor no se toca.
- El error de cada pieza se mide por separado:
  1. el error del clima;
  2. el error del predictor con clima observado (techo);
  3. lo que se pierde al usar el pronóstico.

---

## 3. Almacenamiento: S3 en capas bronce, plata y oro

- **Bucket:** `sira-809411919665-usw2` (us-west-2).
- **Formato:** Parquet particionado por fecha.

| Capa | Ruta | Contenido | Partición |
|---|---|---|---|
| **Bronce** (crudo) | `bronce/sentinel2/`, `bronce/landsat/` | Bandas por pixel recortadas a la ventana + máscara de calidad | `fecha=` |
| | `bronce/geos_s2s/` | Recorte de la celda del lote: lluvia, T máx y T mín por miembro y día de plazo | `emision=` |
| | `bronce/chirps/`, `bronce/power/` | Respuestas originales de cada fuente | `anio=` / `mes=` |
| **Plata** (limpio) | `plata/indices_pixel/` | NDVI y NDMI por pixel y fecha, sensor, marca observado / estimado, nube | `fecha=` |
| | `plata/clima_observado/` | Lluvia y ETo FAO-56 diarias | `anio=` |
| | `plata/geos_s2s_crudo/` | Lluvia, T máx y T mín por emisión, miembro y fecha objetivo | `emision=` |
| **Oro** (listo para usar) | `oro/geos_s2s_corregido/` | Lluvia y ETo corregidas por emisión, miembro y fecha objetivo | `emision=` |
| | `oro/correccion_coeficientes/` | Sesgo por horizonte y variable, con versión y periodo de ajuste | `version=` |
| | `oro/pronostico_predictor/` | NDVI y NDMI por pixel, horizonte y miembro (+ rango) | `emision=` |
| | `oro/semaforo/` | Clase y probabilidades por pixel, horizonte y escenario de riego | `emision=` |

**Llaves de unión:**
- `pixel_id`;
- `fecha_emision` (el día t en que se pronostica);
- `fecha_objetivo` (t + h);
- `miembro` (1–5).

El clima es uno solo para toda la ventana, porque una celda de GEOS-S2S mide unos 55 km. Por eso se une a todos los pixeles copiando la misma tabla pequeña.

**Evidencia en el repositorio:** el código, los informes y una muestra de cada capa. Los datos completos viven en S3.

---

## 4. Entrenamiento frente a producción

| | Entrenamiento | Producción |
|---|---|---|
| Lluvia y ETo de t a t+h | Observadas (CHIRPS + POWER FAO-56) | GEOS-S2S corregido (ETo por Hargreaves calibrada) |
| Respuesta | El NDVI / NDMI que el satélite midió en t+h | — (es lo que se pronostica) |
| Medición del clima pronosticado | Prueba sellada con los pronósticos reales de GEOS-S2S de 2026; corrección ajustada con la climatología de los hindcasts 2001–2020 y los pronósticos de 2025 | Recalibración mensual |

**Límite conocido:** la NASA no publica los pronósticos pasados de GEOS-S2S de 1991–2024 (están en sus máquinas internas). Por eso su error se mide solo con los pronósticos públicos:
- mayo–agosto de 2025;
- marzo–noviembre de 2026, que pasa de La Niña débil a El Niño fuerte (ONI de NOAA CPC).

Fuentes: `https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3/` y su `GEOS-S2S-3-primer.pdf`.

---

## 5. Spark: por definir

**Aún no está decidido dónde se usa Spark.** Se decide con la medición del handover del modelo A, §4:
- el mismo pipeline en pandas en una sola máquina y en PySpark;
- se mide tiempo, costo y memoria pico;
- a 3 tamaños de área: un lote, 1 km² y la ventana completa.

**Candidatos naturales:**
- el lote 1 (miles a millones de pixel·fecha);
- la construcción de ejemplos por horizonte para entrenar;
- la unión del clima con todos los pixeles, que en Spark es una unión con *broadcast*.

Los lotes 2 y 3 son pequeños: una celda y 5 miembros. No necesitan Spark.
