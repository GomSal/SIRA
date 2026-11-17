# Comparación de fuentes de lluvia contra la lluvia medida en Nataima (Fig. 2)

Referencia: lluvia diaria digitalizada del artículo (estación en el propio lote). Fechas de siembra estimadas por alineación (el artículo solo da meses):

- GS1: siembra ≈ **2021-05-27** (correlación media de sumas de 5 días con las fuentes en grilla: 0.59)
- GS2: siembra ≈ **2021-12-04** (correlación media de sumas de 5 días con las fuentes en grilla: 0.42)
- GS3: siembra ≈ **2022-07-09** (correlación media de sumas de 5 días con las fuentes en grilla: 0.29)

## Resumen (las 3 temporadas, solo días con dato de cada fuente)

| fuente                             |   dias |   r_diaria |   r_5dias |   sesgo_pct |   error_medio_mm_dia |
|:-----------------------------------|-------:|-----------:|----------:|------------:|---------------------:|
| IDEAM San Miguel (Ricaurte)        |     82 |       0.56 |      0.5  |       -63.2 |                 3.27 |
| NASA POWER                         |    385 |       0.19 |      0.47 |        26.7 |                 6.34 |
| ERA5                               |    385 |       0.25 |      0.44 |        50.7 |                 6.84 |
| CHIRPS                             |    385 |       0.16 |      0.42 |        13.7 |                 6.52 |
| IDEAM Cucunubá (Valle de San Juan) |    136 |       0.14 |      0.39 |       -36.2 |                 5.3  |

r_diaria: correlación día a día. r_5dias: correlación de sumas de 5 días (lo que importa para el balance de agua). sesgo: cuánto más (+) o menos (−) llueve la fuente que lo medido en el lote.

## Por temporada

|   temporada | fuente                             |   dias_con_dato |   desfase_dias |   r_diaria |   r_5dias |   total_paper_mm |   total_fuente_mm |   sesgo_pct |   error_medio_mm_dia |   acierta_dias_lluvia_pct |   falsas_alarmas_pct |   max_paper |   max_fuente |
|------------:|:-----------------------------------|----------------:|---------------:|-----------:|----------:|-----------------:|------------------:|------------:|---------------------:|--------------------------:|---------------------:|------------:|-------------:|
|           1 | NASA POWER                         |             128 |              1 |       0.1  |      0.6  |            521.5 |             498.8 |        -4.3 |                 6.31 |                        28 |                   85 |       113.4 |         25.7 |
|           1 | ERA5                               |             128 |              0 |       0.24 |      0.59 |            521.5 |             419.9 |       -19.5 |                 5.63 |                        28 |                   83 |       113.4 |         44.2 |
|           1 | CHIRPS                             |             128 |             -1 |       0.08 |      0.62 |            521.5 |             527.5 |         1.1 |                 7.11 |                        28 |                   84 |       113.4 |         47.8 |
|           1 | IDEAM Cucunubá (Valle de San Juan) |             119 |             -1 |       0.14 |      0.39 |            494.6 |             345.6 |       -30.1 |                 5.67 |                        24 |                   75 |       113.4 |         78.2 |
|           1 | IDEAM San Miguel (Ricaurte)        |               0 |            nan |     nan    |    nan    |            nan   |             nan   |       nan   |               nan    |                       nan |                  nan |       nan   |        nan   |
|           1 | IDEAM Base Aérea Melgar            |               0 |            nan |     nan    |    nan    |            nan   |             nan   |       nan   |               nan    |                       nan |                  nan |       nan   |        nan   |
|           2 | NASA POWER                         |             129 |              1 |       0.22 |      0.51 |            557   |             717.8 |        28.9 |                 6.35 |                        80 |                   63 |        76.8 |         32.7 |
|           2 | ERA5                               |             129 |              0 |       0.27 |      0.48 |            557   |            1347.6 |       141.9 |                 9.99 |                        68 |                   77 |        76.8 |         65.5 |
|           2 | CHIRPS                             |             129 |             -1 |       0    |      0.32 |            557   |             626.3 |        12.4 |                 7.19 |                        32 |                   80 |        76.8 |         55.5 |
|           2 | IDEAM Cucunubá (Valle de San Juan) |               0 |            nan |     nan    |    nan    |            nan   |             nan   |       nan   |               nan    |                       nan |                  nan |       nan   |        nan   |
|           2 | IDEAM San Miguel (Ricaurte)        |               0 |            nan |     nan    |    nan    |            nan   |             nan   |       nan   |               nan    |                       nan |                  nan |       nan   |        nan   |
|           2 | IDEAM Base Aérea Melgar            |               0 |            nan |     nan    |    nan    |            nan   |             nan   |       nan   |               nan    |                       nan |                  nan |       nan   |        nan   |
|           3 | NASA POWER                         |             128 |             -1 |       0.27 |      0.28 |            476.8 |             754.1 |        58.2 |                 6.37 |                        75 |                   73 |        67.8 |         34.4 |
|           3 | ERA5                               |             128 |              0 |       0.33 |      0.26 |            476.8 |             576.6 |        20.9 |                 4.88 |                        80 |                   60 |        67.8 |         29.5 |
|           3 | CHIRPS                             |             128 |              0 |       0.42 |      0.32 |            476.8 |             613.9 |        28.8 |                 5.24 |                        70 |                   60 |        67.8 |         43.3 |
|           3 | IDEAM Cucunubá (Valle de San Juan) |              17 |            nan |     nan    |    nan    |            nan   |             nan   |       nan   |               nan    |                       nan |                  nan |       nan   |        nan   |
|           3 | IDEAM San Miguel (Ricaurte)        |              82 |              0 |       0.56 |      0.5  |            318.2 |             117.2 |       -63.2 |                 3.27 |                        67 |                    0 |        67.8 |         32.8 |
|           3 | IDEAM Base Aérea Melgar            |               0 |            nan |     nan    |    nan    |            nan   |             nan   |       nan   |               nan    |                       nan |                  nan |       nan   |        nan   |

## Decisión

- **Ninguna fuente externa reproduce bien la lluvia diaria del lote** (correlación día a día ≤ 0,42; el aguacero de 113 mm de GS1 aparece como 26–48 mm en las fuentes satelitales). En el trópico la lluvia es muy local: el modelo no debe tomar la lluvia diaria externa como dato exacto.
- **Lluvia: CHIRPS** como fuente principal: menor sesgo en volumen (+1 %, +12 %, +29 % por temporada), ~5 km y serie completa. ERA5 se descarta para lluvia (+142 % en GS2). NASA POWER queda de respaldo.
- **IDEAM no es mejor aquí:** las estaciones automáticas públicas más cercanas están a 15–36 km, tienen grandes huecos (ninguna tiene datos de GS2) y subestiman 36–63 %. San Miguel correlaciona algo mejor en sumas de 5 días (0,50), pero con solo 82 días.
- **Temperatura, radiación, viento y humedad del aire (para la ETo):** NASA POWER (no evaluado aquí porque el artículo no publica esas variables; varían poco en el espacio).
- Las fechas de siembra salen de esta misma alineación; la de GS3 es la menos segura (0,29). Esto favorece un poco a las fuentes en grilla frente al IDEAM.

## Datos descargados (data/raw/clima/)

- NASA POWER: power.larc.nasa.gov/api/temporal/daily/point (PRECTOTCORR, punto del lote).
- ERA5: archive-api.open-meteo.com/v1/archive (precipitation_sum).
- CHIRPS v2: climateserv.servirglobal.net/api (datatype=0, promedio sobre 0,05° alrededor del lote).
- IDEAM: datos.gov.co, dataset s54a-sgyg (estaciones automáticas; día completo = ≥130 de 144 registros).
