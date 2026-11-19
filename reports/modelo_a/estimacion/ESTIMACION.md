# Modelo predictor (A): complejidad, tiempo y costo antes de entrenar

**Código:** `scripts/modelo_a/` (`stac_conteo.py`, `bench_lectura.py`, `bench_computo.py`, `estimar_costo.py`).
**Datos:** las tablas de esta carpeta.
**Mediciones:** del 18-nov-2026 en el PC local (16 hilos, 16 GB). Precios on-demand de us-west-2 tomados de la API de precios de AWS.

## 1. Cuántas imágenes hay sobre Nataima (Planetary Computer)

- **Sentinel-2 L2A:** **846 fechas** (2015–2026), teselas 18NWK y 18NVK.
- **Landsat C2 L2:** **1062 escenas** (1984–2026), casi todas de la órbita 008/057.
  - Landsat 4/5: 1984–2011.
  - Landsat 7: 1999–2024.
  - Landsat 8: desde 2013.
  - Landsat 9: desde 2021.

**Ejemplos de entrenamiento por pixel:** un ejemplo es un par (fecha despejada, otra fecha despejada a ≤ 30 días).
- Con el 28 % de fechas despejadas en Sentinel-2 y el 30 % en Landsat (informe §4), salen **≈ 1 300 pares por pixel**.
- **Solo 60 de ellos (4,6 %) son de antes de 2013.** Antes de Landsat 8 hay pocas fechas al año y casi todas nubladas (`pares_por_anio.csv`).

## 2. Operaciones y costo por paso

### 2.1 Leer imágenes y preprocesar (nubes, bruma, NDVI/NDMI, relleno, pares)

**Mediciones:**
- **Cómputo:** 160 ns por pixel·fecha en numpy y 180 ns en pandas. Pandas no es mucho más lento; su problema es la **memoria**, unos 50 bytes por fila en formato largo.
- **Lectura:** se lee por bloques del COG (512 px en Sentinel-2, 256 px en Landsat). Leer 1 ha o 5 × 5 km cuesta lo mismo: hasta 4 bloques por banda.
- **Internet local:** 2,5 MB/s medidos.

| Área | Pixeles 10 m | Pixel·fecha | GB a leer | Leer en local | Leer en EC2 (50 MB/s, supuesto) | Preprocesar (núcleo-hora) | Tabla larga en pandas |
|---|---|---|---|---|---|---|---|
| Lote (~1 ha) | 125 | 2,4 × 10⁵ | 8 | ~1 h | 3 min | 0 | 0,01 GB |
| 1 km² | 10 000 | 1,9 × 10⁷ | 8 | ~1 h | 3 min | 0 | 1 GB |
| Ventana 5 × 5 km | 250 000 | 4,8 × 10⁸ | 8 | ~1 h | 3 min | 0,02 | 24 GB |
| **Imagen completa** (tesela S2 de 110 × 110 km) | 1,2 × 10⁸ | 2,3 × 10¹¹ | **826** | **92 h** | **4,6 h** | **10** | **11 500 GB** |

### 2.2 Entrenar y elegir el modelo

**El costo depende de cuántos ejemplos N se usan, no del área.**

**Plan de cómputo:**
- 5 particiones de la ventana deslizante;
- 8 configuraciones de XGBoost, 6 de Random Forest, 6 de Gradient Boosting y 4 de la GRU;
- ARIMAX por pixel sobre 5 000 pixeles;
- prueba de años con XGBoost: 7 historias acumuladas + ranking de 40 años, por 5 particiones;
- +10 % para reentrenar el ganador, la prueba y el bootstrap.

**Tiempo por ajuste, medido:**

| Modelo | N medido | Segundos por ajuste |
|---|---|---|
| XGBoost | 10⁶ | 9,5 (16 hilos) |
| Random Forest | 10⁵ | 25 (16 hilos) |
| Gradient Boosting de sklearn | 10⁵ | 184 (1 hilo) |
| GRU | 10⁵ | 16 |
| ARIMAX | 1 serie | 0,44 |

**Total estimado:**

| N ejemplos | Núcleo-hora | Horas con 16 núcleos | US$ EC2 c7i.4xlarge | US$ EMR Serverless |
|---|---|---|---|---|
| 10⁵ | 15 | 0,9 | 0,7 | 1,1 |
| **10⁶** | **100** | **6,3** | **4,5** | 7,6 |
| 10⁷ | 1 110 | 69 | 50 | 84 |

**De dónde sale el costo:** Random Forest y Gradient Boosting de sklearn pesan el 60 % a 10⁶. XGBoost es el más barato.

**Ejemplos disponibles:**
- con todos los pixeles de la ventana: 3,3 × 10⁸;
- con todos los pixeles de la imagen completa: 1,6 × 10¹¹;
- en ambos casos, solo una parte es arroz.

Usar todos no aporta información en proporción: los pixeles vecinos de un mismo lote son casi copias. **N se elige con una curva de aprendizaje** (10⁵ → 10⁶ → 10⁷): se agregan ejemplos solo mientras la mejora en validación tenga un IC 95 % que excluya el 0. Es la misma regla del paso 6.

## 3. Spark: qué cambia y qué no

- **Spark no reduce las operaciones.** Reparte las mismas entre núcleos o máquinas.
- **En una sola máquina** (local[*]) hace el mismo cómputo que numpy o pandas. Además suma el arranque de Java y el paso de datos entre Python y Java. **No abarata.**
- **EMR Serverless cuesta 1,7 veces más por vCPU-hora** que EC2 c7i: US$ 0,076 frente a US$ 0,045.
- **El entrenamiento de los candidatos** (XGBoost, Random Forest, GRU, ARIMAX) corre en una máquina sobre 10⁵–10⁷ filas. Spark no lo acelera.
- **Dónde sí sirve:** cuando los datos **no caben en memoria**. Por ejemplo, la imagen completa en formato largo, de 11,5 TB, o el escenario de 100 predios. Ahí reparte el preprocesado por bloques de la imagen. También se puede hacer con numpy por bloques en una sola máquina; la comparación del §4 del handover dirá cuál sale más barato.

## 4. Conclusión

| Opción | Leer y preprocesar | Entrenar con N = 10⁶ | Total |
|---|---|---|---|
| **Ventana 5 × 5 km, todo en local** | ~1 h, US$ 0 | ~6–8 h, US$ 0 | **US$ 0** |
| **Imagen completa en EC2 + entrenar en local** | ~5–6 h en c7i.4xlarge ≈ **US$ 4** | ~6–8 h, US$ 0 | **≈ US$ 4** |
| Imagen completa y entrenamiento en EC2 | ≈ US$ 4 | ≈ US$ 4,5 | **≈ US$ 9** (+ S3, centavos) |
| Kaggle (4 núcleos, sesiones de 12 h) | No conviene para la imagen completa (tope de unos 20 GB guardados) | ~25 h, unas 3 sesiones, US$ 0 | US$ 0 |

**Lectura:**
- **No es enorme.** La parte cara es **leer la imagen completa** (826 GB): en local tomaría unas 92 h y en EC2 unas 5 h.
- El entrenamiento cabe en este PC si N se queda cerca de 10⁶.
- **Riesgo no medido:** la velocidad de lectura desde EC2 hacia Planetary Computer, que está en Azure, Europa occidental. Se supuso 50 MB/s. La QA de la máquina la mide y se apaga sola (`ESTIMADO_EXCEDE`) si la proyección pasa el tope.
