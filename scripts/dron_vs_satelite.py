"""Dron (Fig. 7) frente a Sentinel-2 por franja: ¿qué tratamiento estaba en qué franja, y ve el satélite lo que ve el dron?

Sentinel-2 (reports/correlacion_ndvi_ndre_datos.csv): NDVI y NDRE por franja física y fecha despejada. Las franjas
físicas se nombran por el tratamiento que tenían en GS1 (Fig. 1): oeste = MIRI, centro = AWD+MIRI, este = CD.
Se promedia por etapa (vegetativa ≤ 42 días, reproductiva 43–82, maduración > 82).

Comparación dentro de cada etapa: a cada valor se le resta la media de las 3 franjas (o de los 3 tratamientos)
en esa etapa. Así se quita el crecimiento del cultivo, que domina los índices, y queda solo la diferencia entre
tratamientos, que es lo que distingue al riego. Para cada temporada se prueban las 6 asignaciones posibles
tratamiento → franja y se mide el acuerdo (Pearson de las 9 diferencias y error cuadrático) con el dron.
Salida: reports/dron/rotacion_por_dron.csv y dron_vs_s2.csv
"""
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports" / "dron"
OUT.mkdir(parents=True, exist_ok=True)
TRAT = ("MIRI", "AWD+MIRI", "CD")
FIS = {"MIRI": "oeste", "AWD+MIRI": "centro", "CD": "este"}

dron = pd.read_csv(RAIZ / "data/processed/dron_indices.csv")
s2 = pd.read_csv(RAIZ / "reports/correlacion_ndvi_ndre_datos.csv")
s2["fisica"] = s2.franja.map(FIS)
s2["etapa"] = np.select([s2.das <= 42, s2.das <= 82], ["vegetativa", "reproductiva"], "maduracion")
sat = s2.groupby(["temporada", "etapa", "fisica"], as_index=False)[["ndvi", "ndre"]].mean()
nfechas = s2.groupby(["temporada", "etapa"]).fecha.nunique()


def centrar(df, col, grupo):
    return df[col] - df.groupby(grupo)[col].transform("mean")


filas, pares = [], []
for t in (1, 2, 3):
    for perm in itertools.permutations(TRAT):  # tratamiento en oeste, centro, este
        asign = dict(zip(("oeste", "centro", "este"), perm))
        for ind in ("NDVI", "NDRE"):
            s = sat[sat.temporada == t].copy()
            s["tratamiento"] = s.fisica.map(asign)
            dr = dron[(dron.temporada == t) & (dron.indice == ind)]
            m = s.merge(dr, on=["etapa", "tratamiento"])
            if len(m) < 6:
                continue
            m["sat_c"] = centrar(m, ind.lower(), "etapa")
            m["dron_c"] = centrar(m, "valor", "etapa")
            r = float(np.corrcoef(m.sat_c, m.dron_c)[0, 1])
            filas.append({"temporada": t, "oeste_centro_este": "/".join(perm), "indice": ind, "n": len(m),
                          "pearson_diferencias": round(r, 2),
                          "es_rot_este": perm == {1: TRAT, 2: ("CD", "MIRI", "AWD+MIRI"), 3: ("AWD+MIRI", "CD", "MIRI")}[t],
                          "es_rot_oeste": perm == {1: TRAT, 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}[t]})
            if perm == {1: TRAT, 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}[t] or \
               perm == {1: TRAT, 2: ("CD", "MIRI", "AWD+MIRI"), 3: ("AWD+MIRI", "CD", "MIRI")}[t]:
                pares.append(m.assign(temporada=t, indice=ind, asignacion="/".join(perm)))
R = pd.DataFrame(filas)
R.to_csv(OUT / "rotacion_por_dron.csv", index=False)
pd.concat(pares).to_csv(OUT / "dron_vs_s2.csv", index=False)

print("Fechas S2 despejadas por temporada y etapa:", nfechas.to_dict())
for t, g in R.groupby("temporada"):
    piv = g.pivot_table(index="oeste_centro_este", columns="indice", values="pearson_diferencias")
    piv["promedio"] = piv.mean(1)
    piv["rotacion"] = [("este " if g[(g.oeste_centro_este == p)].es_rot_este.any() else "") +
                       ("oeste" if g[(g.oeste_centro_este == p)].es_rot_oeste.any() else "") for p in piv.index]
    print(f"\nGS{t}: acuerdo dron–satélite en las diferencias entre tratamientos (oeste/centro/este)")
    print(piv.sort_values("promedio", ascending=False).to_string())
