# SIRA: fase 0, verdad de campo de Nataima

Reconstrucción, a partir del artículo abierto (CC BY 4.0) de Martínez-Vega et al. (2025, *Irrigation Science* 43:1615–1633), de los datos diarios del ensayo de riego de arroz en el C.I. Nataima (Espinal, Tolima). Son 3 temporadas (GS1–GS3) y 3 tratamientos (CD, MIRI, AWD+MIRI). Los datos originales son privados, así que se digitalizan las figuras rasterizadas del PDF.

## Cómo correrlo

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install --only-binary=:all: numpy pandas scipy matplotlib pytest pymupdf pyproj shapely
cd src && ../.venv/Scripts/python.exe -m sira.digitalizar.ensamblar && cd ..
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/graficas_control.py
```

## Qué sale

| Archivo | Contenido |
|---|---|
| `data/processed/nataima_diario.csv` | Una fila por temporada × día después de siembra × tratamiento: `etapa`, `lluvia_mm`, `atwd_mm`, `eto5_mm`, `riego_mm`, `vwc_pct` y `vwc_ee_pct` (vacíos los días sin medición) |
| `data/interim/fig2_lluvia_atwd.csv` | Fig. 2 tal como se digitalizó |
| `data/interim/fig4_riego.csv` | Fig. 4: riego diario y acumulado por tratamiento, y lluvia acumulada |
| `data/interim/fig3_vwc_detecciones.csv` | Fig. 3: todas las detecciones, con `fuente` = `barra` (se usa) o `rombo` (descartada) |
| `reports/validacion_digitalizacion.md` | Comparación con todo número publicado en el artículo |
| `reports/control/*.png` | Lo digitalizado dibujado sobre la figura original |

## Método, en corto

- **Fig. 2 (lluvia y AtWD):** los ejes se calibran con las etiquetas de días, la serie en 0 mm y la línea de 100 mm. Cada barra se sigue desde la base aunque la cruce la línea roja. El AtWD se lee por continuidad, para no confundirlo con la leyenda.
- **Fig. 4 (riego acumulado):** cada línea es el camino más probable (Viterbi) a través de la imagen. El camino es continuo, no baja y está anclado en 0 y en el total de la Tabla 2. Un riego es una rampa de un día entre dos marcas. Cada serie se reescala ≤1,5 % para que cuadre exacto con la Tabla 2.
- **Fig. 3 (humedad TDR):** solo se usan los marcadores con barra de error simétrica (media = centro, EE = media barra). La curva suavizada de Excel no es dato y no se usa.

## Hallazgos y límites

- Lluvia: GS1 cuadra exacto (521,5 frente a 521,4 mm), GS2 queda +1,5 % y GS3 +1,0 %. En GS2 el texto dice 87 mm de lluvia máxima diaria, pero la figura muestra 77 mm.
- Las sumas por etapa de la Fig. 4 solo cuadran (≈2 mm de error medio) con cortes de etapa distintos a los del sombreado. Se usan esos cortes.
- La línea de AtWD de GS1 está dibujada un día antes que las barras de lluvia.
- Humedad: 198 mediciones de las 9 series. Los mínimos y máximos coinciden con el texto (±1 punto). Faltan marcadores tapados por otros, sobre todo de MIRI. Se prefirió precisión a cantidad.
- El día de cada medición tiene una incertidumbre de ±1 día. El error de lectura de la humedad es ≈0,1 puntos, menor que el EE publicado (0,2–2,5 puntos).
- Ojo con la escala, en `scripts/escala_lote.py`: el lote mide 136 × 70 m, con franjas de 23,5 m. En HLS a 30 m ninguna franja tiene un píxel puro. En Sentinel-2 a 10 m hay 10–15 por franja.
