# Modelo B (Corrector satélite → dron): resultados, corrida 2

Corrida en EC2 `c7a.16xlarge` (us-west-2), 18-nov-2026: 29 min, estado **OK**, ≈ US$1,60. Código: commit `077a92a`. AWS quedó en **0 instancias y 0 volúmenes**. Detalle en `cloud/modelo_b/ultima_corrida.json` y `run.log`.

La corrida 1 sigue completa en `reports/modelo_b_corrida1/` (`cloud/modelo_b/corrida1.json`).

**En una frase:**
- **NDVI** quedó bien: la selección deja solo el NDVI del satélite y supera a la línea base en las 3 temporadas.
- **NDRE** sigue fallando en 2 de 3 temporadas: con 14 fechas, la selección elige variables (o Prithvi) que ganan en validación y pierden en la prueba. El modelo fijado de antemano, solo el NDRE del satélite, es mejor.
- **OSAVI** no sirve, por el valor incoherente del artículo.
- Prithvi, clima y riego no aportan de forma consistente.

## Por qué hubo una corrida 2

En la revisión de la corrida 1, el modelo elegido resultó **no mejor que una recta con solo el índice del satélite**: RMSE medio de prueba 0,051 frente a 0,050 en NDVI, y 0,075 frente a 0,061 en NDRE.

**Causa:** la selección hacia adelante aceptaba una variable si bajaba el RMSE de validación más de 1 %. Con unas 25 candidatas y una sola temporada de validación, siempre alguna lo logra por azar.

**Cambios del código:**
- Una variable entra solo si el **IC 95 % de su mejora excluye el 0**. Es la misma regla de empate que ya se aplicaba a los modelos (paso 7).
- Se reporta siempre la recta con **solo el índice** (§6.11 del informe), en las mismas filas de prueba.
- r con el mismo peso para cada fecha.
- El "error esperado" se describe como error del procedimiento.

**Transparencia:** la regla se cambió **después de ver la prueba de la corrida 1**. Por eso se reportan las dos corridas. Las temporadas de prueba ya no son "nunca vistas" respecto a esta decisión. Con 3 temporadas no hay datos nuevos para confirmar, y eso se dice como limitación.

## Resultado en la temporada de prueba

RMSE con peso igual por fecha; IC 95 % por bootstrap de bloques móviles + ruido del dron (1000 repeticiones).

| Índice | Prueba | Elegido | Entradas | RMSE [IC 95 %] | Solo el índice | Línea base | Corrida 1 |
|---|---|---|---|---|---|---|---|
| NDVI | GS1 | B1 Huber | ndvi | 0,036 [0,021–0,060] | 0,036 | 0,042 | 0,033 |
| NDVI | GS2 | B1 Huber | ndvi | 0,056 [0,027–0,089] | 0,057 | 0,060 | 0,069 |
| NDVI | GS3 | B1 lineal | ndvi | 0,054 [0,016–0,101] | 0,058 | 0,073 | 0,050 |
| NDRE | GS1 | B1 lineal | ndre, ndwi, ndwi_rel | 0,091 [0,059–0,105] | **0,047** | 0,049 | 0,089 |
| NDRE | GS2 | B2 Huber | ndre, str_rel, das + Prithvi (3) | 0,128 [0,022–0,255] | **0,073** | 0,078 | 0,079 |
| NDRE | GS3 | B1 lineal | ndre, str_rel | 0,066 [0,029–0,133] | 0,065 | 0,112 | 0,057 |
| OSAVI | GS1 | B2 lineal | osavi, eto_10d, riego_10d + Prithvi (3) | 0,195 | 0,151 | 0,077 | 0,195 |
| OSAVI | GS2 | B1 lineal | osavi, lluvia_3d | 0,129 | 0,063 | 0,057 | 0,206 |
| OSAVI | GS3 | B2 Ridge | osavi + Prithvi (5) | 0,225 | 0,242 | 0,216 | 0,224 |

"Solo el índice" se mide en todas las filas de la temporada. En `prueba.csv` también está medido en las mismas filas del elegido.

**Promedio de las 3 temporadas de prueba (RMSE):**

| Índice | Corrida 1 | Corrida 2 | Solo el índice | Línea base |
|---|---|---|---|---|
| NDVI | 0,051 | **0,049** | 0,050 | 0,058 |
| NDRE | 0,075 | 0,095 | **0,061** | 0,080 |
| OSAVI | 0,208 | 0,183 | 0,152 | 0,117 |

**Elegido frente a solo el índice**, misma fila, IC 95 % de la diferencia (`comparacion_variantes.csv`):
- NDVI: 0,000 [−0,003 a 0,003] en GS1, −0,001 [−0,001 a 0,000] en GS2 y −0,004 [−0,011 a −0,002] en GS3. Son **equivalentes**.
- NDRE: **+0,044 [0,017 a 0,058]** en GS1, **+0,075 [0,028 a 0,103]** en GS2 y +0,001 en GS3. El elegido es **peor**.

**Verificación cronológica** (entrena GS1 → valida GS2 → prueba GS3):
- NDVI: 0,055, frente a 0,058 con solo el índice y 0,073 de la base.
- NDRE: 0,057, frente a 0,065 y 0,112.
- OSAVI: falla.

## Qué pasó con NDRE

- **GS1 de prueba:** la validación (GS2 ↔ GS3) aceptó `ndwi` y `ndwi_rel` con el IC excluyendo el 0. En la prueba, GS1 tiene 3 fechas útiles de NDRE y el modelo falla.
- **GS2 de prueba:** dentro de B1 la selección dejó **solo `ndre`**, que era lo correcto. Pero en la comparación entre variantes, en las filas con HLS, ganó B2 (Prithvi + `str_rel` + `das`) por menor RMSE de validación, y en la prueba duplicó el error.
- **El problema de fondo no es la regla, sino el tamaño:** NDRE tiene 14 fechas en total (3, 6 y 5 por temporada). Cada validación interna son 3 a 6 fechas, y ningún intervalo calculado sobre tan pocas fechas protege de elegir mal.
- **Para NDVI**, con 23 fechas y Landsat, la regla nueva sí funciona.

## Prithvi, clima y riego

- **Prithvi (B2 − B1, en las mismas filas):**
  - NDVI: +0,032 [0,003 a 0,062] en GS1 y +0,035 [−0,010 a 0,057] en GS2. En GS3 no es concluyente (2 fechas HLS).
  - NDRE: mejora en GS1 (−0,048 [−0,057 a −0,029], 3 fechas) y empeora en GS2 (+0,075 [0,028 a 0,103]).
  - **No hay aporte consistente.**
  - B3 (Prithvi solo) es peor que B1 en NDVI en las 3 temporadas.
- **Clima:** con la regla nueva no entra en ningún corrector de NDVI ni de NDRE. Su mejora en validación no excluye el 0.
- **Riego:** no entra en NDVI ni en NDRE.
- **Estabilidad (200 remuestras):** B1 gana el 88–94 % de las veces en NDVI y el 72–94 % en NDRE. La familia más elegida es la lineal.

## Modelos de uso (`models/corrector_b/`)

| Índice | Archivo | Configuración | Error esperado (media de las 3 pruebas) |
|---|---|---|---|
| NDVI | `ndvi.joblib` | B1 lineal [ndvi] | 0,049 (error del procedimiento) |
| NDVI | `ndvi_solo_indice.joblib` | lineal [ndvi, sensor_L], fijada de antemano | 0,050 (de esta misma configuración) |
| NDRE | `ndre.joblib` | B1 lineal [ndre, str_rel] | 0,095 (procedimiento) → **no usar** |
| NDRE | `ndre_solo_indice.joblib` | lineal [ndre], fijada de antemano | **0,061** (GS1 0,047; GS2 0,073; GS3 0,065) |
| OSAVI | `osavi*.joblib` | — | **no usar** |

**Recomendación:**
- **NDVI:** cualquiera de los dos; son equivalentes, y el más simple es `ndvi_solo_indice`.
- **NDRE:** `ndre_solo_indice`.
- **Argumento:** la recta con solo el índice se fijó **antes** de entrenar (§6.11) y no pasa por la selección. Su error de una temporada afuera es el de esa misma configuración. La selección, con 3 a 6 fechas de validación, no es confiable para NDRE.
- **Limitación a reportar:** esta preferencia se decide comparando las pruebas de dos procedimientos.

## Qué no salió bien

1. **NDRE:** la selección de variables y de variantes no generaliza con 14 fechas (arriba).
2. **OSAVI:** la Tabla 4 del artículo da 0,21 en GS3 maduración con NDVI 0,87. Los demás valores dan OSAVI/NDVI de 0,57 a 0,72. Nuestra digitalización coincide con la Tabla 4, así que es el dato del artículo. Falta decidir si se excluye esa casilla.
3. **Pocas fechas:** los IC son anchos y casi todos los r incluyen 0. GS3 tiene solo 2 fechas HLS.
4. **Orden de franjas** (qué parte del lote está peor): NDVI acierta la franja más baja en 0 %, 67 % y 100 % de las fechas S2 de GS1, GS2 y GS3 (3, 6 y 5 fechas). No es confiable.
