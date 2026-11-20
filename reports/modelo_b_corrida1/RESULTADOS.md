# Modelo B (Corrector satélite → dron): resultados del entrenamiento

> **Corrida 1, reemplazada por la corrida 2** (`reports/modelo_b/RESULTADOS.md`). Su regla de selección de variables (baja > 1 %) sobreajustaba la validación. Se conserva como registro.

Corrida en EC2 `c7a.16xlarge` (us-west-2), 18-nov-2026: 30 min, estado **OK**, ≈ US$1,65. Commit del código: `9dbdae1`. Se deja **0 instancias y 0 volúmenes**. Detalle en `cloud/modelo_b/corrida1.json` y `run.log`.
Una corrida anterior (6 min, ≈ US$0,35) se paró sola con `ESTIMADO_EXCEDE`. La QA subestimaba los núcleos disponibles; se corrigió.

**En una frase:** el corrector de **NDVI** y el de **NDRE** superan a la línea base en 2 de 3 temporadas. Clima, riego y Prithvi **no mejoran de forma consistente** al índice del satélite solo. El de **OSAVI no sirve**, porque la referencia del artículo tiene un valor incoherente.

## Paso por paso

| Paso | Qué salió | Archivo |
|---|---|---|
| 1 · Exploración | Fechas útiles: S2 3/6/5 y Landsat 6/2/2 en GS1/GS2/GS3 (NDVI); HLS 7/6/3. Figuras de series, dispersión, agua y correlación | `eda/` |
| 2 · Calidad y datos | Descartadas 4 fechas S2 y 1 Landsat (bruma), y 1 HLS (7-jul-2021, la misma toma de S2 con bruma). Contrato de datos cumplido: NDVI 23 fechas/141 pares, NDRE 14/42, OSAVI 23/141. Prithvi: 16 vectores de 16 fechas HLS. **No regresión OK** (r a ≤ 0,005 de `corrector_dron_v2.csv`). OSAVI: el rojo despejado del NDVI coincide con la imagen original (6 píxeles, diferencia 0) | `calidad_fechas.csv`, `contrato_datos.csv`, `no_regresion.csv`, `prithvi_embeddings.csv`, `tabla_limpia_*.csv` |
| 3 · Partición | Anidada por temporada (prueba sellada; entrenar con una y validar con la otra, en los dos sentidos) + cronológica GS1 → GS2 → GS3. NDRE con GS1 de entrenamiento tiene 3 fechas (marcado) | `particiones.csv` |
| 4 · Métricas | Principal: RMSE con peso igual por fecha. Además: RMSE sin ponderar, MAE, r, mejora frente a la media del entrenamiento y orden de franjas | `metricas_definicion.json` |
| 5 · Estandarización | StandardScaler y PCA de Prithvi dentro del Pipeline, ajustados solo con el entrenamiento | `estandarizacion.txt` |
| 6 · Variables | Selección hacia adelante (máx. 4 entradas). Control con Lasso (todas las candidatas): casi siempre deja solo el índice del satélite o nada | `seleccion_variables.csv`, `importancia.csv` |
| 7 · Modelos | 7 familias × rejillas × 4 variantes. Gana **B1 lineal** o con penalización suave en 7 de 9 casos. Random Forest y XGBoost nunca ganan | `validacion_interna.csv`, `seleccion.csv` |
| 8 · Evaluación | Prueba medida **una vez**, después de la selección (orden de eventos verificado en `seleccion.csv`). Bootstrap de 1000, estabilidad de 200 | `prueba.csv`, `bootstrap_IC.csv`, `comparacion_variantes.csv`, `estabilidad_seleccion.csv`, `cronologica.csv`, `errores_por_etapa_sensor.csv`, figuras `fig_*.png` |

## Resultado en la temporada de prueba

RMSE con peso igual por fecha; IC 95 % por bootstrap de bloques móviles + ruido del dron.

| Índice | Prueba | Elegido | Entradas | RMSE [IC95] | Línea base | r [IC95] | Supera a la base | §6.11 (r) |
|---|---|---|---|---|---|---|---|---|
| NDVI | GS1 | B1 lineal | ndvi, str, T2M_MAX, ndmi_rel | 0,033 [0,018–0,072] | 0,042 | 0,85 [−0,17–0,94] | 71 % | 0,58 |
| NDVI | GS2 | B1 Huber | ndvi, osavi, eto_10d, str | 0,069 [0,032–0,097] | 0,060 | 0,52 [−0,40–0,82] | 28 % | 0,52 |
| NDVI | GS3 | B1 lineal | ndvi, T2M_MAX, lluvia_1d, eto_5d | 0,050 [0,014–0,109] | 0,073 | 0,75 [−0,60–0,86] | 74 % | 0,79 |
| NDRE | GS1 | B1 lineal | ndre, ndwi, ndwi_rel, ndvi | 0,089 [0,053–0,098] | 0,049 | −0,03 [−0,09–0,76] | 10 % | 0,20 |
| NDRE | GS2 | B1 Ridge | ndre, ndmi | 0,079 [0,047–0,097] | 0,083 | 0,39 [−0,16–0,97] | 71 % | 0,72 |
| NDRE | GS3 | B1 lineal | ndre, str_rel, riego_5d | 0,057 [0,026–0,119] | 0,112 | 0,95 [0,55–0,97] | 92 % | 0,95 |
| OSAVI | GS1 | B2 lineal | osavi, eto_10d, riego_10d + Prithvi (3) | 0,195 | 0,077 | −0,84 | 4 % | — |
| OSAVI | GS2 | B1 Huber | osavi, lluvia_3d, lluvia_1d, riego_10d | 0,206 | 0,057 | −0,36 | 8 % | — |
| OSAVI | GS3 | B2 lineal | osavi, ndwi_rel, riego_5d + Prithvi (5) | 0,224 | 0,216 | −0,86 | 56 % | — |

**Verificación cronológica** (entrena GS1 → valida GS2 → prueba GS3):
- NDVI: RMSE 0,057 frente a 0,073 de la base, r 0,64.
- NDRE: RMSE 0,055 frente a 0,112, r 0,93.
- OSAVI: falla.

**Frente a §6.11** (regresión lineal con solo el índice):
- NDVI mejora en GS1 (r 0,58 → 0,85) e iguala en GS2 y GS3.
- NDRE empeora en GS1 y GS2. El modelo agrega variables que no generalizan a otra temporada; con 3 y 6 fechas de prueba, el IC es muy ancho.

## Cuánto aportan Prithvi, el clima y el riego

En la prueba y en las mismas filas, la diferencia de RMSE con IC 95 % está en `comparacion_variantes.csv`. Un valor positivo = peor que B1.

- **Prithvi (B2 − B1):**
  - NDVI: +0,032 en GS1 [−0,002 a 0,065], +0,025 en GS2 [−0,023 a 0,043] y −0,005 en GS3 (2 fechas).
  - NDRE: −0,051 en GS1 (3 fechas), +0,051 en GS2 [0,010 a 0,072] y +0,015 en GS3.
  - **No hay aporte consistente.** B3 (Prithvi solo) no supera a B1 en ninguna temporada de NDVI.
  - En la selección, Prithvi gana solo en OSAVI (que no sirve). En la estabilidad, B2 gana el 1–12 % de las veces en NDVI.
- **Clima:** entra en los 3 correctores de NDVI elegidos (T2M_MAX, ETo de 5–10 días, lluvia). Pero Lasso lo anula casi siempre y su importancia por permutación es baja (≤ 0,009). Es un aporte pequeño y poco robusto.
- **Riego (B1 − B1 sin riego):**
  - Sin efecto en NDVI (la selección no lo elige).
  - En NDRE GS3 mejora 0,009 [−0,016 a 0,000].
  - En OSAVI GS2 empeora 0,022.
  - El modelo de uso de NDRE lo incluye (`riego_5d`), así que en la app requiere que el agricultor reporte riegos. La versión sin riego está reportada al lado.

## Estabilidad de la selección (200 remuestras)

- B1 gana el 88–99 % de las veces en NDVI, el 95–98 % en NDRE GS1 y GS2, y el 55 % en NDRE GS3 (ahí B2, 45 %).
- La familia más elegida es la **regresión lineal** (37–86 %).
- RF y XGBoost ganan menos del 7 % de las veces.

## Modelos de uso (`models/corrector_b/`)

En las 3 temporadas de prueba se eligió una configuración distinta. Como empatan 1 a 1, se tomó la más estable y después la más simple. Todas se reentrenaron con GS1 + GS2 + GS3.

| Índice | Modelo | Entradas | Error esperado (RMSE de prueba GS1/GS2/GS3; media) |
|---|---|---|---|
| NDVI | B1 lineal | ndvi, str, T2M_MAX, ndmi_rel | 0,033 / 0,069 / 0,050; 0,051 |
| NDRE | B1 lineal | ndre, str_rel, riego_5d | 0,089 / 0,079 / 0,057; 0,075 |
| OSAVI | B2 lineal | osavi, eto_10d, riego_10d + Prithvi | 0,195 / 0,206 / 0,224 → **no usar** |

## Qué no salió bien

1. **OSAVI.** La Fig. 7 y la Tabla 4 del artículo dan OSAVI 0,21 en GS3, maduración, con un NDVI de 0,87. En el resto de casillas, OSAVI/NDVI vale 0,57–0,72; ahí vale 0,23. Es físicamente incoherente (probable error del artículo) y rompe el corrector: r negativo en las 3 pruebas. **No se cambió el dato:** decidir si se excluye esa casilla queda para el chat original.
2. **Pocas fechas.** NDRE GS1 tiene 3 fechas. GS3 tiene solo 2 fechas con HLS, así que la comparación con Prithvi en GS3 no es concluyente. Los IC son anchos y casi todos los r incluyen el 0.
3. **NDVI GS2** no supera a la línea base (RMSE 0,069 frente a 0,060).
4. **Orden de franjas** (lo que vería el agricultor): en NDVI y NDRE se acierta la franja más baja en 0–100 % de las fechas, según temporada. No es confiable.
5. **Por etapa:** el error mayor está en la vegetativa (NDVI 0,065–0,070) y en la maduración de NDRE (0,082, peor que la base).

## Decisiones de implementación (no cambian el diseño)

- **Selección hacia adelante (paso 6):** se hace con el modelo lineal. Las familias del paso 7 se ajustan después sobre esas variables.
- **B2:** lleva el bloque de Prithvi desde el inicio (B2 = B1 + Prithvi), para que siempre mida el aporte de Prithvi.
- **Selección en dos niveles:** dentro de cada variante, con sus filas; entre B1, B2 y B3, en las mismas filas de validación (las que tienen HLS). Con la regla de empate y la de simplicidad del handover.
- **B1 sin riego:** no compite en la selección; se reporta. Por eso se omite en las repeticiones de estabilidad, donde no cambia al ganador.
- **PCA de Prithvi:** se ajusta sobre los vectores distintos (una fecha = un vector). k se recorta a fechas HLS − 1, porque no hay más componentes posibles.
- **Prithvi-EO-2.0-300M-TL congelado:** commit fijo `63adbd39` de Hugging Face, normalización oficial, hasta 4 fechas. El lote cae en el bloque (7, 7) del recorte de 224 px. Coordenadas (lat, lon) según el código del codificador.
- **Control de calidad de HLS:** además de Fmask y la regla de bruma, se descarta una HLS si es la misma toma que una imagen S2 o Landsat ya descartada.
- **Otros:**
  - el ruido del dron en OSAVI usa el EE de NDVI;
  - la MLP de B3 usa el solver `lbfgs` (datos pocos);
  - el entrenamiento pesa por fila; las métricas, por fecha.
- **Alerta:** sin correo SNS ni regla de EventBridge, a pedido del usuario. Quedan los topes de tiempo y las paradas por error de la máquina.
