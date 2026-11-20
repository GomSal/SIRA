"""¿Distingue el satélite el régimen de riego de cada franja, fecha por fecha?

Etiqueta: régimen de la franja ese día, según la rotación "oeste" (resuelta con el dron, sección 8.6):
  GS1  oeste MIRI · centro AWD+MIRI · este CD
  GS2  oeste AWD+MIRI · centro CD · este MIRI
  GS3  oeste CD · centro MIRI · este AWD+MIRI      (variante "dron": oeste CD · centro AWD+MIRI · este MIRI)
Muestras: píxeles Sentinel-2 de 10–20 m con ≥ 90 % de su área en una franja, en fechas despejadas de cada siembra.
Rasgos:
  absolutos: NDVI, NDWI, NDMI (LSWI), MNDWI, STR (SWIR transformado), días desde siembra
  relativos: cada índice menos la media de todo el lote ese día (como compararse con el arroz vecino)
Clasificador: Random Forest, entrenado con 2 temporadas y probado en la 3ª (las posiciones cambian por la rotación,
así que el modelo no puede aprender "la franja oeste es CD").
Métricas en la temporada de prueba:
  - por píxel: exactitud y macro-F1 (azar = 0,33)
  - por franja-fecha: voto de mayoría de sus píxeles
  - por fecha con asignación: cada fecha tiene un régimen por franja; se elige la asignación de las 3 franjas a los
    3 regímenes con mayor probabilidad conjunta → % de franjas bien asignadas (azar = 0,33)
Uso: python scripts/clasificar_regimen.py [oeste|dron]
"""
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score

RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports" / "regimen"
OUT.mkdir(parents=True, exist_ok=True)
VAR = sys.argv[1] if len(sys.argv) > 1 else "oeste"
SIEMBRAS = {1: pd.Timestamp("2021-05-27"), 2: pd.Timestamp("2021-12-04"), 3: pd.Timestamp("2022-07-09")}
ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"),
       3: ("CD", "MIRI", "AWD+MIRI") if VAR == "oeste" else ("CD", "AWD+MIRI", "MIRI")}
REG = ("CD", "MIRI", "AWD+MIRI")
FRAC = {"oeste": "frac_MIRI", "centro": "frac_AWD+MIRI", "este": "frac_CD"}
IND = ["ndvi", "ndwi", "ndmi", "mndwi", "str"]

e = pd.read_csv(RAIZ / "data/s3/ronda1_b/out/e1_pixeles_lote.csv", parse_dates=["fecha"])
e = e[e.sensor == "S2"].copy()
e["fisica"] = e[list(FRAC.values())].idxmax(axis=1).map({v: k for k, v in FRAC.items()})
lote_media = e.groupby("fecha")[IND].transform("mean")  # media del lote (todas las franjas) ese día
for c in IND:
    e[c + "_rel"] = e[c] - lote_media[c]
e = e[e[list(FRAC.values())].max(axis=1) >= 0.9]
filas = []
for t, d0 in SIEMBRAS.items():
    x = e[(e.fecha >= d0) & (e.fecha <= d0 + pd.Timedelta(days=125))].copy()
    x["temporada"], x["das"] = t, (x.fecha - d0).dt.days + 1
    x["regimen"] = x.fisica.map(dict(zip(("oeste", "centro", "este"), ROT[t])))
    filas.append(x)
D = pd.concat(filas, ignore_index=True)
D["etapa"] = np.select([D.das <= 42, D.das <= 82], ["vegetativa", "reproductiva"], "maduracion")

CONJ = {"absolutos": IND + ["das"], "relativos": [c + "_rel" for c in IND] + ["das"],
        "ambos": IND + [c + "_rel" for c in IND] + ["das"]}
res, detalle = [], []
for nombre, cols in CONJ.items():
    for prueba in (1, 2, 3):
        tr, te = D[D.temporada != prueba], D[D.temporada == prueba].copy()
        rf = RandomForestClassifier(n_estimators=400, min_samples_leaf=5, class_weight="balanced", random_state=0, n_jobs=-1)
        rf.fit(tr[cols], tr.regimen)
        te["pred"] = rf.predict(te[cols])
        P = pd.DataFrame(rf.predict_proba(te[cols]), columns=rf.classes_, index=te.index)
        te = te.join(P.add_prefix("p_"))
        # franja-fecha: voto de mayoría y probabilidad media
        g = te.groupby(["fecha", "fisica"]).agg(regimen=("regimen", "first"), etapa=("etapa", "first"),
                                                **{f"p_{r}": (f"p_{r}", "mean") for r in REG})
        g["pred_franja"] = g[[f"p_{r}" for r in REG]].idxmax(axis=1).str[2:]
        # asignación por fecha: permutación de regímenes que maximiza la probabilidad conjunta
        g["asignado"] = None
        for f, gf in g.groupby(level=0):
            if len(gf) < 3:
                continue
            fis = list(gf.index.get_level_values(1))
            mejor = max(itertools.permutations(REG), key=lambda p: sum(np.log(gf.loc[(f, s), f"p_{r}"] + 1e-6)
                                                                       for s, r in zip(fis, p)))
            for s, r in zip(fis, mejor):
                g.loc[(f, s), "asignado"] = r
        ga = g.dropna(subset=["asignado"])
        res.append({"variante_rotacion": VAR, "rasgos": nombre, "temporada_prueba": prueba,
                    "n_pixeles": len(te), "n_fechas": te.fecha.nunique(),
                    "exactitud_pixel": round(accuracy_score(te.regimen, te.pred), 2),
                    "macroF1_pixel": round(f1_score(te.regimen, te.pred, average="macro"), 2),
                    "exactitud_franja_fecha": round(float((g.pred_franja == g.regimen).mean()), 2),
                    "exactitud_asignacion": round(float((ga.asignado == ga.regimen).mean()), 2) if len(ga) else np.nan,
                    "fechas_asignacion_perfecta": f"{int((ga.groupby(level=0).apply(lambda z: (z.asignado == z.regimen).all())).sum())}/{ga.index.get_level_values(0).nunique()}"})
        if nombre == "ambos":
            detalle.append(g.reset_index().assign(temporada=prueba))
R = pd.DataFrame(res)
R.to_csv(OUT / f"clasificacion_regimen_{VAR}.csv", index=False)
G = pd.concat(detalle)
G.to_csv(OUT / f"clasificacion_regimen_franja_fecha_{VAR}.csv", index=False)
print(R.to_string(index=False))
print("\nExactitud por etapa (rasgos ambos, franja-fecha con asignación):")
print(G.dropna(subset=["asignado"]).assign(ok=lambda z: z.asignado == z.regimen).groupby(["temporada", "etapa"]).ok
      .agg(["mean", "size"]).round(2).unstack(0).to_string())
print("\nMatriz de confusión franja-fecha (filas = real, columnas = asignado):")
Ga = G.dropna(subset=["asignado"])
print(pd.DataFrame(confusion_matrix(Ga.regimen, Ga.asignado, labels=list(REG)), index=REG, columns=REG).to_string())
