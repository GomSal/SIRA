"""Valida la digitalización de la Fig. 7 contra las medias marginales impresas en la Tabla 4 del artículo.
Media digitalizada por etapa = promedio de los 3 tratamientos; por tratamiento = promedio de las 3 etapas.
(La Tabla 4 sale de un modelo mixto con distinto número de vuelos por etapa, así que no es un promedio simple:
se espera una diferencia pequeña, sobre todo en las medias por tratamiento.)"""
from pathlib import Path
import pandas as pd
RAIZ = Path(__file__).resolve().parents[1]
d = pd.read_csv(RAIZ / "data/processed/dron_indices.csv")
T4 = {  # (temporada, grupo): (NDVI, NDRE, OSAVI)
    (1, "vegetativa"): (.815, .383, .558), (1, "reproductiva"): (.917, .519, .637), (1, "maduracion"): (.889, .511, .639),
    (2, "vegetativa"): (.796, .344, .548), (2, "reproductiva"): (.927, .563, .534), (2, "maduracion"): (.863, .471, .613),
    (3, "vegetativa"): (.696, .239, .463), (3, "reproductiva"): (.881, .492, .643), (3, "maduracion"): (.875, .490, .210),
    (1, "CD"): (.884, .486, .619), (1, "MIRI"): (.880, .485, .615), (1, "AWD+MIRI"): (.879, .474, .620),
    (2, "CD"): (.853, .445, .546), (2, "MIRI"): (.890, .503, .586), (2, "AWD+MIRI"): (.868, .474, .569),
    (3, "CD"): (.807, .404, .397), (3, "MIRI"): (.861, .460, .455), (3, "AWD+MIRI"): (.857, .457, .449)}
filas = []
for (t, grupo), ref in T4.items():
    col = "etapa" if grupo in ("vegetativa", "reproductiva", "maduracion") else "tratamiento"
    for k, ind in enumerate(("NDVI", "NDRE", "OSAVI")):
        v = d[(d.temporada == t) & (d[col] == grupo) & (d.indice == ind)].valor.mean()
        filas.append({"temporada": t, "tipo": "etapa" if col == "etapa" else "tratamiento", "grupo": grupo,
                      "indice": ind, "digitalizado": round(v, 3), "tabla4": ref[k], "dif": round(v - ref[k], 3)})
V = pd.DataFrame(filas)
V.to_csv(RAIZ / "reports/dron/validacion_dron_tabla4.csv", index=False)
print(V.groupby(["tipo", "indice"]).dif.agg(["mean", lambda s: s.abs().mean(), lambda s: s.abs().max()]).round(3)
      .rename(columns={"mean": "sesgo", "<lambda_0>": "error_medio_abs", "<lambda_1>": "error_max_abs"}).to_string())
