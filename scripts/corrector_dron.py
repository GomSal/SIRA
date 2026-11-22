"""Modelo B (corrector): índice de Sentinel-2 → índice equivalente al dron, con control de bruma y bootstrap.

1. Control de calidad (igual para las 3 temporadas; reports/dron/revision_gs*.csv): se descarta una fecha si
     a) el NDVI del lote cae > 0,10 frente a la fecha anterior y a la siguiente (caída que se recupera), o
     b) el azul del lote es ≥ 0,075 entre los días 25 y 115 (bruma o nube fina que la máscara SCL dejó pasar).
2. Dron como serie de tiempo: los vuelos fueron a los 30, 50, 70, 90 y 110 días. La media por etapa se ubica en el
   día medio de sus vuelos (vegetativa 30; reproductiva 60; maduración 100) y se interpola con PCHIP por tratamiento
   y temporada. Cada fecha de Sentinel-2 se empareja con el valor del dron ese día (marcado "interpolado").
3. Corrector: recta dron = a + b·satélite por índice, entrenada con 2 temporadas, probada en la 3ª.
4. Bootstrap (1000 repeticiones):
     - por bloques: se remuestrean las fechas de Sentinel-2 dentro de cada temporada (cada fecha arrastra sus 3 franjas);
     - del dron: cada media se perturba con su error estándar (EE por etapa de la Tabla 4 × √3, aproximación por celda).
   Se reportan intervalos del 95 % para r y RMSE en la temporada de prueba.
Salida: reports/dron/corrector_dron.csv, corrector_dron_pares.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports" / "dron"
rng = np.random.default_rng(0)
FIS = {"MIRI": "oeste", "AWD+MIRI": "centro", "CD": "este"}  # nombre en el CSV de S2 = tratamiento de GS1
ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}  # "oeste"
DIA_ETAPA = {"vegetativa": 30, "reproductiva": 60, "maduracion": 100}
EE_T4 = {  # EE por etapa de la Tabla 4 (NDVI, NDRE)
    (1, "vegetativa"): (.008, .008), (1, "reproductiva"): (.001, .004), (1, "maduracion"): (.002, .004),
    (2, "vegetativa"): (.007, .009), (2, "reproductiva"): (.001, .004), (2, "maduracion"): (.004, .006),
    (3, "vegetativa"): (.014, .006), (3, "reproductiva"): (.005, .011), (3, "maduracion"): (.005, .006)}

# ---- 1. control de calidad
rev = pd.concat([pd.read_csv(OUT / f"revision_gs{t}.csv") for t in (1, 2, 3)]).sort_values(["temporada", "das"])
rev["prev"] = rev.groupby("temporada").ndvi_lote.shift(1)
rev["sig"] = rev.groupby("temporada").ndvi_lote.shift(-1)
rev["caida"] = (rev.prev - rev.ndvi_lote > 0.10) & (rev.sig - rev.ndvi_lote > 0.10)
rev["azul"] = (rev.azul_lote >= 0.075) & rev.das.between(25, 115)
rev["descartada"] = rev.caida | rev.azul
malas = set(zip(rev[rev.descartada].temporada.astype(int), rev[rev.descartada].fecha.astype(str)))
print("Fechas descartadas:", sorted(malas))

s2 = pd.read_csv(RAIZ / "reports/correlacion_ndvi_ndre_datos.csv")
s2["fisica"] = s2.franja.map(FIS)
s2["tratamiento"] = [dict(zip(("oeste", "centro", "este"), ROT[t]))[f] for t, f in zip(s2.temporada, s2.fisica)]
s2["calidad_ok"] = [(t, f) not in malas for t, f in zip(s2.temporada, s2.fecha.astype(str))]

# ---- 2. dron como serie diaria
dron = pd.read_csv(RAIZ / "data/processed/dron_indices.csv")


def dron_en(t, trat, ind, das, ruido=False):
    g = dron[(dron.temporada == t) & (dron.tratamiento == trat) & (dron.indice == ind)].copy()
    g["dia"] = g.etapa.map(DIA_ETAPA)
    g = g.sort_values("dia")
    v = g.valor.to_numpy()
    if ruido:
        k = 0 if ind == "NDVI" else 1
        v = v + rng.normal(0, [EE_T4[(t, e)][k] * np.sqrt(3) for e in g.etapa])
    return PchipInterpolator(g.dia, v, extrapolate=True)(np.clip(das, 25, 115))


def pares(datos, ruido=False):
    out = datos.copy()
    for ind in ("NDVI", "NDRE"):
        out[f"dron_{ind}"] = np.nan
        for (t, trat), g in out.groupby(["temporada", "tratamiento"]):
            out.loc[g.index, f"dron_{ind}"] = dron_en(t, trat, ind, g.das.to_numpy(), ruido)
    return out


def evaluar(P, ind):
    filas = []
    for prueba in (1, 2, 3):
        tr, te = P[P.temporada != prueba], P[P.temporada == prueba]
        b, a = np.polyfit(tr[ind.lower()], tr[f"dron_{ind}"], 1)
        pred = a + b * te[ind.lower()]
        y = te[f"dron_{ind}"]
        filas.append({"temporada_prueba": prueba, "a": a, "b": b, "n": len(te),
                      "r": float(np.corrcoef(pred, y)[0, 1]) if len(te) > 2 else np.nan,
                      "rmse": float(np.sqrt(np.mean((pred - y) ** 2))),
                      "rmse_base": float(np.sqrt(np.mean((tr[f"dron_{ind}"].mean() - y) ** 2)))})
    return pd.DataFrame(filas)


res = []
for filtro, D in (("sin control de calidad", s2), ("con control de calidad", s2[s2.calidad_ok])):
    D = D[D.das.between(25, 115)]
    P = pares(D)
    P.assign(filtro=filtro).to_csv(OUT / f"corrector_dron_pares_{'qc' if 'con' in filtro else 'crudo'}.csv", index=False)
    for ind in ("NDVI", "NDRE"):
        base = evaluar(P, ind)
        # ---- 4. bootstrap por bloques de fechas + ruido del dron
        boots = []
        for _ in range(1000):
            partes = []
            for t, g in D.groupby("temporada"):
                fechas = g.fecha.unique()
                elegidas = rng.choice(fechas, size=len(fechas), replace=True)
                partes.append(pd.concat([g[g.fecha == f] for f in elegidas]))
            boots.append(evaluar(pares(pd.concat(partes, ignore_index=True), ruido=True), ind))
        B = pd.concat(boots)
        for _, fila in base.iterrows():
            bb = B[B.temporada_prueba == fila.temporada_prueba]
            res.append({"filtro": filtro, "indice": ind, "temporada_prueba": int(fila.temporada_prueba), "n_pares": int(fila.n),
                        "fechas": D[D.temporada == fila.temporada_prueba].fecha.nunique(),
                        "r": round(fila.r, 2), "r_IC95": f"{bb.r.quantile(.025):.2f} a {bb.r.quantile(.975):.2f}",
                        "rmse": round(fila.rmse, 3), "rmse_IC95": f"{bb.rmse.quantile(.025):.3f} a {bb.rmse.quantile(.975):.3f}",
                        "rmse_base": round(fila.rmse_base, 3), "recta": f"dron = {fila.a:.2f} + {fila.b:.2f}·S2"})
R = pd.DataFrame(res)
R.to_csv(OUT / "corrector_dron.csv", index=False)
pd.set_option("display.width", 220)
print(R.to_string(index=False))
