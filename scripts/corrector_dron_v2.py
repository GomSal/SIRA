"""Corrector B v2: Sentinel-2 + Landsat → índice equivalente al dron, con bootstrap por bloques móviles.

Datos satelitales (fechas despejadas de cada siembra, días 25–115):
  - Sentinel-2: NDVI y NDRE por franja (píxeles ≥ 90 % dentro), reports/correlacion_ndvi_ndre_datos.csv
  - Landsat 7/8/9: NDVI por píxel de 30 m con ≥ 50 % de su área en el lote (ningún píxel es puro). Su valor de
    referencia del dron es la mezcla de los tratamientos que cubre, ponderada por área.
Control de calidad (igual para todos): se descarta una fecha si el NDVI medio del lote cae > 0,10 frente a la fecha
anterior y a la siguiente de la serie conjunta S2 + Landsat; y, en Sentinel-2, si el azul del lote es ≥ 0,075
(reports/dron/revision_gs*.csv).
Dron: medias por etapa ubicadas en el día medio de sus vuelos (30, 60, 100) e interpoladas con PCHIP por tratamiento,
solo para emparejar fechas (no agrega información).
Modelos (entrenar con 2 temporadas, probar en la 3ª):
  NDVI:  dron = a + b·NDVI + c·[Landsat]     (S2 + Landsat)
  NDRE:  dron = a + b·NDRE                   (solo S2: Landsat no tiene borde rojo)
Incertidumbre: bootstrap por bloques móviles de fechas (bloques de 2 fechas consecutivas, dentro de cada temporada;
respeta la autocorrelación de la serie), más ruido del dron con su error estándar. 1000 repeticiones.
Salida: reports/dron/corrector_dron_v2.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports" / "dron"
rng = np.random.default_rng(0)
SIEMBRAS = {1: pd.Timestamp("2021-05-27"), 2: pd.Timestamp("2021-12-04"), 3: pd.Timestamp("2022-07-09")}
FIS = {"MIRI": "oeste", "AWD+MIRI": "centro", "CD": "este"}
FRAC = {"oeste": "frac_MIRI", "centro": "frac_AWD+MIRI", "este": "frac_CD"}
ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}
TRAT = ("CD", "MIRI", "AWD+MIRI")
DIA_ETAPA = {"vegetativa": 30, "reproductiva": 60, "maduracion": 100}
EE = {(1, "vegetativa"): (.008, .008), (1, "reproductiva"): (.001, .004), (1, "maduracion"): (.002, .004),
      (2, "vegetativa"): (.007, .009), (2, "reproductiva"): (.001, .004), (2, "maduracion"): (.004, .006),
      (3, "vegetativa"): (.014, .006), (3, "reproductiva"): (.005, .011), (3, "maduracion"): (.005, .006)}
dron = pd.read_csv(RAIZ / "data/processed/dron_indices.csv")


def temporada(f):
    for t, d0 in SIEMBRAS.items():
        das = (f - d0).days + 1
        if 25 <= das <= 115:
            return t, das
    return None, None


# ---- Sentinel-2 por franja
s2 = pd.read_csv(RAIZ / "reports/correlacion_ndvi_ndre_datos.csv", parse_dates=["fecha"])
s2 = s2[s2.das.between(25, 115)]
s2["fisica"] = s2.franja.map(FIS)
s2["sensor"] = "S2"
for t in TRAT:
    s2[f"w_{t}"] = 0.0
for i, r in s2.iterrows():
    s2.loc[i, f"w_{dict(zip(('oeste', 'centro', 'este'), ROT[r.temporada]))[r.fisica]}"] = 1.0
s2["unidad"] = s2.fisica

# ---- Landsat por píxel (mezcla ponderada por área)
e = pd.read_csv(RAIZ / "data/s3/ronda1_b/out/e1_pixeles_lote.csv", parse_dates=["fecha"])
L = e[(e.sensor == "L") & (e.frac_lote >= 0.5)].copy()
L[["temporada", "das"]] = L.fecha.apply(lambda f: pd.Series(temporada(f)))
L = L.dropna(subset=["temporada"])
L["temporada"] = L.temporada.astype(int)
for t in TRAT:
    L[f"w_{t}"] = 0.0
for i, r in L.iterrows():
    for pos, trat in zip(("oeste", "centro", "este"), ROT[r.temporada]):
        L.loc[i, f"w_{trat}"] = r[FRAC[pos]] / r.frac_lote
L["sensor"], L["ndre"], L["unidad"] = "L", np.nan, L.pixel_id
cols = ["temporada", "fecha", "das", "sensor", "unidad", "ndvi", "ndre"] + [f"w_{t}" for t in TRAT]
D = pd.concat([s2[cols], L[cols]], ignore_index=True)

# ---- control de calidad: caída que se recupera en la serie conjunta + azul alto en S2
lote = D.groupby(["temporada", "fecha", "sensor"], as_index=False).ndvi.mean().sort_values(["temporada", "fecha"])
lote["prev"] = lote.groupby("temporada").ndvi.shift(1)
lote["sig"] = lote.groupby("temporada").ndvi.shift(-1)
lote["caida"] = (lote.prev - lote.ndvi > 0.10) & (lote.sig - lote.ndvi > 0.10)
rev = pd.concat([pd.read_csv(OUT / f"revision_gs{t}.csv") for t in (1, 2, 3)])
azul = set(pd.to_datetime(rev[(rev.azul_lote >= 0.075) & rev.das.between(25, 115)].fecha))
malas = set(lote[lote.caida].apply(lambda r: (r.fecha, r.sensor), axis=1)) | {(f, "S2") for f in azul}
D["ok"] = [(f, s) not in malas for f, s in zip(D.fecha, D.sensor)]
print("Descartadas:", sorted((str(f.date()), s) for f, s in malas))
D = D[D.ok].reset_index(drop=True)
print(D.groupby(["temporada", "sensor"]).fecha.nunique().unstack().to_string())


def dron_curvas(ruido=False):
    curvas = {}
    for (t, trat, ind), g in dron.groupby(["temporada", "tratamiento", "indice"]):
        g = g.assign(dia=g.etapa.map(DIA_ETAPA)).sort_values("dia")
        v = g.valor.to_numpy()
        if ruido:
            k = 0 if ind == "NDVI" else 1
            v = v + rng.normal(0, [EE[(t, e_)][k] * np.sqrt(3) for e_ in g.etapa]) if ind in ("NDVI", "NDRE") else v
        curvas[(t, trat, ind)] = PchipInterpolator(g.dia, v, extrapolate=True)
    return curvas


def objetivo(df, ind, curvas):
    return sum(df[f"w_{t}"] * np.array([curvas[(tt, t, ind)](d) for tt, d in zip(df.temporada, df.das)]) for t in TRAT)


def evaluar(df, ind, curvas):
    df = df.dropna(subset=[ind.lower()]).copy()
    df["y"] = objetivo(df, ind, curvas)
    filas = []
    for prueba in (1, 2, 3):
        tr, te = df[df.temporada != prueba], df[df.temporada == prueba]
        X = lambda z: np.c_[np.ones(len(z)), z[ind.lower()], (z.sensor == "L").astype(float)] if ind == "NDVI" \
            else np.c_[np.ones(len(z)), z[ind.lower()]]  # noqa: E731
        beta = np.linalg.lstsq(X(tr), tr.y, rcond=None)[0]
        pred = X(te) @ beta
        filas.append({"temporada_prueba": prueba, "n": len(te), "fechas": te.fecha.nunique(),
                      "fechas_landsat": te[te.sensor == "L"].fecha.nunique(),
                      "r": float(np.corrcoef(pred, te.y)[0, 1]), "rmse": float(np.sqrt(np.mean((pred - te.y) ** 2))),
                      "rmse_base": float(np.sqrt(np.mean((tr.y.mean() - te.y) ** 2))), "beta": beta})
    return pd.DataFrame(filas)


def bloques_moviles(df, largo=2):
    partes = []
    for t, g in df.groupby("temporada"):
        fechas = np.sort(g.fecha.unique())
        n = len(fechas)
        inicios = np.arange(max(n - largo + 1, 1))
        elegidas = []
        while len(elegidas) < n:
            i = rng.choice(inicios)
            elegidas += list(fechas[i:i + largo])
        partes += [g[g.fecha == f] for f in elegidas[:n]]
    return pd.concat(partes, ignore_index=True)


curvas0 = dron_curvas()
res = []
for ind in ("NDVI", "NDRE"):
    base = evaluar(D, ind, curvas0)
    B = pd.concat([evaluar(bloques_moviles(D), ind, dron_curvas(ruido=True)) for _ in range(1000)])
    for _, f in base.iterrows():
        bb = B[B.temporada_prueba == f.temporada_prueba]
        res.append({"indice": ind, "sensores": "S2 + Landsat" if ind == "NDVI" else "S2",
                    "temporada_prueba": int(f.temporada_prueba), "fechas": int(f.fechas), "fechas_landsat": int(f.fechas_landsat),
                    "pares": int(f.n), "r": round(f.r, 2), "r_IC95": f"{bb.r.quantile(.025):.2f} a {bb.r.quantile(.975):.2f}",
                    "rmse": round(f.rmse, 3), "rmse_IC95": f"{bb.rmse.quantile(.025):.3f} a {bb.rmse.quantile(.975):.3f}",
                    "rmse_base": round(f.rmse_base, 3),
                    "mejora_vs_base_en_bootstrap": f"{100 * np.mean(bb.rmse < bb.rmse_base):.0f} %"})
R = pd.DataFrame(res)
R.to_csv(OUT / "corrector_dron_v2.csv", index=False)
pd.set_option("display.width", 230)
print(R.to_string(index=False))
