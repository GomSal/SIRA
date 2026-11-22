# Esquema del semáforo de humedad (producto)

Semáforo de humedad del suelo por pixel de 10 m, de hoy a 30 días, para arroz. Escenario: **sin riego desde hoy**. El agricultor no tiene que ingresar nada.

| Color | Humedad (TDR) | Déficit bajo capacidad de campo | Mensaje |
|---|---|---|---|
| Rojo (baja) | < 27,1 % | > 4,34 mm | Riego inmediato |
| Amarillo (media) | 27,1–30 % | 0–4,34 mm | Riego en un intervalo |
| Verde (suficiente) | ≥ 30 % | ≤ 0 | No requiere riego, alcanza con la lluvia pronosticada |

Los umbrales son de Martínez-Vega et al. 2025: capacidad de campo 30 % y déficit crítico 4,34 mm en 150 mm de raíz. Un punto de humedad equivale a 1,5 mm (1,5 L/m²).

## Las piezas: dos modelos y dos ecuaciones

| Pieza | Qué hace | Se entrena | Archivo |
|---|---|---|---|
| **A** | Random Forest de NDVI y NDMI por pixel. Detecta la siembra con la curva del NDVI y pronostica NDVI y NDMI de hoy a +30 días. | Ya entrenado (2016→, Sentinel-2 + Landsat calibrado) | `data/interim/modelo_a/kaggle_final/resultados_a/modelos/` |
| **Ecuaciones de B** | S_τ(t) = e^(−1/τ)·S_τ(t−1) + lluvia + riego − Kc·ETo, con τ = 20 y 5, y el déficit FAO-56 Dr con k = 0,5. Son las entradas del ARIMAX, desde la siembra hasta hoy. | No | `scripts/operacion/b_entradas.py` (`propagar`) |
| **B** | ARIMAX(1) entrenado con las 198 mediciones TDR de Nataima. Corrige el estado de **hoy**: da μ, la humedad esperada en unidades del TDR, y σ = 3,12 %, su error típico sin TDR. | Ya entrenado. Aquí solo se guarda el modelo de uso | `models/semaforo_b/arimax.json` |
| **Dr_modificada** | Déficit FAO-56 que propaga de hoy a +30 días: Dr(t) = Dr(t−1) − lluvia + Ks·Kc·ETo. TAW 6,7 mm, p 0,65, drenaje k = 0,5 sobre capacidad de campo, límites −26,5 a 6,7 mm. | No | `scripts/operacion/b_entradas.py` (`propagar_dr`) |

Kc = 1,0·NDVI + 0,45, recortado a 0,6–1,3 (§11.4 del informe).

## Orden de ejecución por pixel

1. **A detecta la siembra** y la edad del cultivo hoy.
2. **De la siembra a hoy: entradas de B.** Se calculan S₂₀, S₅, Dr, el riego de 5 días, los días desde el riego, la ETo de 5 días y el NDVI, con:
   - lluvia CHIRPS y ETo de NASA POWER observadas;
   - el NDVI observado del pixel, llevado a diario;
   - **riego supuesto CD** (inundación continua).

   El riego supuesto son los 3 calendarios medidos en Nataima (GS1, GS2 y GS3, por días desde siembra). Se corre con cada uno y se promedia μ.
3. **Hoy: B corrige y se aplica el margen, una sola vez.** El déficit de arranque es Dr₀ = 1,5·(30 − μ₀) + 1,5·σ, donde el margen 1,5·σ vale 4,7 mm. El color de hoy sale de Dr₀.
4. **De hoy a +30 días: Dr_modificada propaga** desde Dr₀ con:
   - lluvia y ETo de GEOS-S2S, del último inicio ≤ hoy, con 5 miembros, corregidas con `paso8_geos_correccion.csv`;
   - el NDVI pronosticado por A;
   - riego cero.

   Se promedia sobre los miembros. Cada día se pinta con los límites del artículo tal cual, sin volver a sumar margen.
5. **Alarma tardía del NDMI**, que no cambia el color. Se activa en un pixel verde cuyo NDMI pronosticado por A queda en el 10 % más seco de la curva normal del arroz, aun sumándole el error de A. El pixel lleva entonces la marca *"posible enfermedad o deficiencia nutricional"*.

## Cómo se corre

```
python scripts/operacion/guardar_modelo_b.py          # una vez: guarda el ARIMAX de uso y los calendarios; controla las ecuaciones
python scripts/operacion/semaforo_c.py --hoy 2026-11-15 [--riego CD|MIRI|AWD+MIRI] [--lat 4.1895 --lon -74.9621 --lado_km 2]
```

Usar el `.venv` del proyecto: tiene pyproj.

**Controles**:
- `guardar_modelo_b.py` exige que las ecuaciones de operación reproduzcan exactamente las 7 entradas de la tabla de entrenamiento; la diferencia máxima obtenida fue 0.
- Partir la propagación de las entradas en dos tramos da lo mismo que una sola corrida (diferencia 0).
- `semaforo_c.py --qa` reproduce las predicciones de prueba de A.

**Salida** (`reports/semaforo_c/`):
- `mapa.html`: visor Leaflet sobre imagen satelital Esri, con un deslizador hoy / 7 / 15 / 30 días;
- `semaforo_hXX.png`;
- `resumen.json`, que incluye los días hasta "baja" sin riego;
- `pixeles.csv.gz`, con humedad, déficit pintado, déficit de B sin margen, S₂₀, Dr de la ecuación y color por pixel y horizonte.

Los datos que se usan son los que ya existían: el cubo de A, `clima.csv` y GEOS-S2S. No cambió el almacenamiento.

## Resultado del 15-nov-2026 (zona de 400 ha alrededor de Nataima, 69 ha en cultivo)

| Día | Verde | Amarillo | Rojo | Lluvia / ETo acumuladas (GEOS-S2S) |
|---|---|---|---|---|
| Hoy | 10 % | 84 % | 6 % | — |
| +7 | 0 % | 0 % | 100 % | 18 / 26 mm |
| +15 | 0 % | 9 % | 91 % | 35 / 54 mm |
| +30 | 0 % | 100 % | 0 % | 92 / 113 mm |

Días hasta rojo sin riego:
- 6 % ya hoy;
- 90 % en 3–5 días;
- 4 % en 6–30 días.

**Por qué todo pasa a rojo en una semana.** La reserva útil del artículo (TAW 6,7 mm) alcanza para menos de dos días de consumo (Kc·ETo ≈ 4,3 mm/día). Sin riego, el balance la agota en días, sin importar el estado de partida. Por eso la información que distingue un pixel de otro es en cuántos días llega a rojo.

## De dónde sale cada decisión

| Decisión | Fecha | Evidencia |
|---|---|---|
| ARIMAX(1) con las 7 entradas, sin Prithvi | 18/21-nov | Prueba sellada por bloques de 10 días: AUC macro 0,75. Prithvi no mejora (`reports/semaforo_clasificador_ndvi_prithvi/RESULTADOS.md`). |
| B solo hasta hoy; desde hoy propaga Dr_modificada | 22-nov | Pasarle al ARIMAX las entradas propagadas no seca el suelo: 30 días sin riego bajan μ solo 0,3 puntos, porque β de Dr y de "días desde riego" tienen el signo físico al revés (colinealidad). Reentrenar con solo S τ45 tampoco sigue los secados observados (`reports/b_reentreno/dinamica/`: ρ −0,08 [−0,29; 0,10]). Los datos de Nataima no pueden validar un secado aprendido: hay 2 días entre mediciones y el ruido del TDR es de ±4 puntos. La dinámica la pone la física (FAO-56). |
| Margen solo en el arranque | 22-nov | Sin margen, B detecta 0 de 17 rojos, porque μ es una media encogida. Con margen detecta 9 de 17, con exactitud balanceada 0,53. Dr_modificada no está encogida, así que se pinta con los límites del artículo tal cual. |
| Riego supuesto CD | 22-nov | Es el riego más común y accesible. El producto busca ahorrar agua frente a lo que hace la mayoría, así que se supone el riego más arcaico. Con AWD+MIRI el día 0 salía 100 % rojo. |
| Escenario sin riego desde hoy | 21-nov | Es el más universal: no depende de lo que haga cada agricultor. |

Comparación de reglas de color para B en la prueba sellada, con las 198 mediciones:

| Regla | Rojos | Amarillos | Verdes | Exactitud balanceada |
|---|---|---|---|---|
| Déficit de B sin margen | 0 de 17 | 13 de 42 | 119 de 139 | 0,39 |
| **Déficit de B + margen (la elegida, solo para hoy)** | **9 de 17** | **24 de 42** | 70 de 139 | **0,53** |
| Probabilidad corregida por frecuencia (descartada: pinta rojo con déficit ≈ 0) | 12 de 17 | 11 de 42 | 90 de 139 | 0,54 |

## Límites que hay que decir

- **El día 0 tiene un error grande.** Por pixel, B se equivoca en unos 4 L/m², y solo 1 de cada 5 rojos de B era rojo de verdad.
- **La propagación de 30 días no está validada con TDR.** Es FAO-56 con los parámetros del artículo. No hay mediciones sin riego de 30 días para comprobarla.
- **El margen no crece con el horizonte.** No incluye la dispersión de GEOS-S2S ni el error de A.
- **TAW 6,7 mm limita el déficit.** No puede pasar de 6,7 mm, o sea, la humedad equivalente no baja de 25,5 %. En Nataima el TDR sí bajó de 25,5 % algunas veces.
- **Un solo sitio.** B solo conoce el suelo de Nataima.
