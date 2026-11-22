# ¿Cuántos años de historia? (estudio previo al entrenamiento del modelo predictor)

**Código:** `scripts/modelo_a/estudio_anios.py`.
**Datos:**
- ventana de 3 × 3 km alrededor de Nataima, 1 500 pixeles agrícolas de 30 m;
- Sentinel-2 + Landsat 4/5/7/8/9, 1984–2026;
- clima CHIRPS + NASA POWER.

**Validación:** ventana deslizante, 3 años (nov-2022 → nov-2025). La prueba sellada (desde 2025-11-18) no se tocó.
**Modelo de la prueba:** XGBoost.
**IC 95 %:** bootstrap pareado de bloques de 10 días (1 000 repeticiones; el valor p, con 10 000).

## Historia acumulada (`historia_acumulada.csv`, `valor_p_vs_mejor.csv`)

| Años hacia atrás | NDVI: error frente al mejor | p | NDMI: error frente al mejor | p |
|---|---|---|---|---|
| 5 | +4,0 % | < 0,001 | +2,7 % | < 0,001 |
| 6 | +1,1 % | 0,056 | +1,0 % | 0,094 |
| 8 | +1,0 % | 0,148 | +1,6 % | 0,001 |
| **10** | +0,6 % | **0,163** | +0,7 % | **0,161** |

- El mejor L es 13 años en NDVI y 16 en NDMI.
- **Decisión del usuario (19-nov): 10 años.** Es el L más corto en el que los dos índices empatan con el mejor con holgura. 6 años pasa solo en el borde (p = 0,056).
- Antes de 2013 hay pocas fechas y casi todas nubladas: solo el 6 % de los ejemplos.

## Ranking por año (`ranking_por_anio.csv`)

Se quitó cada año de la historia completa y se midió cuánto empeora la validación.
- **Ningún año aporta por sí solo de forma significativa:** los IC 95 % incluyen el 0.
- Los años que más aportan en NDVI son recientes: 2021, 2018 y 2022.
- Unos pocos años antiguos **estorban** de forma significativa: 1996 y 2007 en NDVI; 2009 y 2013 en NDMI.

Esto es coherente con quedarse con los años recientes.
