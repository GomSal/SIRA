"""Prueba: ¿un balance hídrico FAO-56 reproduce el faltante de agua (Dr) medido con el TDR en Nataima?

Dr observado = 1000·Zr·(θcc − θ_TDR) = 150·(0,30 − θ) mm (θ en fracción). Negativo = exceso sobre capacidad de campo.

Balance diario por tratamiento (FAO-56 ec. 85, con exceso y drenaje para suelo franco-arenoso):
    Dr ← Dr − lluvia − riego
    Dr ← Dr + Ks·Kc·ETo                         (Ks = 1 si Dr ≤ RAW; si no, (TAW − Dr)/((1 − p)·TAW))
    si Dr < 0: Dr ← Dr·(1 − k)                   (el agua sobre capacidad de campo drena; k se calibra)
    límites: −Dsat ≤ Dr ≤ TAW                    (Dsat = 150·(0,4765 − 0,30) = 26,5 mm; TAW = 6,7 mm)
TAW = 6,7 mm, p = 0,65 → RAW = 4,34 mm (del artículo). Arranca con Dr = 0 el día 1 (lote recién inundado).

Versiones:
  A  techo:      lluvia del lote (artículo) + riego real (Fig. 4) + Kc FAO-56 del arroz por etapa.
  B  satélite:   lluvia CHIRPS + riego real + Kc = a·NDVI + b (NDVI Sentinel-2 de la franja, interpolado).
  C  producción: lluvia CHIRPS + SIN riego; el lote se da por mojado (Dr → mín(Dr, 0)) cuando el satélite lo ve
                 inundado: LSWI + 0,05 ≥ NDVI (Xiao et al., 2005) o MNDWI > 0. Kc desde NDVI.
Parámetros (k; y a, b en B y C) calibrados con 2 temporadas y probados en la tercera (las tres combinaciones).
Métricas en la temporada de prueba, solo días con medición real: RMSE (mm), Spearman, acierto de la clase
(exceso Dr < 0 · óptimo 0 ≤ Dr ≤ 4,35 · déficit Dr > 4,35) y línea base = Dr medio de entrenamiento.
B y C dependen de qué tratamiento estaba en qué franja: se corren con las dos rotaciones posibles.
"""
import itertools
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports" / "balance_hidrico"
OUT.mkdir(parents=True, exist_ok=True)
SIEMBRAS = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}
TRAT = ("MIRI", "AWD+MIRI", "CD")
TAW, P_AG, ZR = 6.7, 0.65, 150.0
RAW = P_AG * TAW
DSAT = ZR * (0.4765 - 0.30)
ROT = {"este": {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("CD", "MIRI", "AWD+MIRI"), 3: ("AWD+MIRI", "CD", "MIRI")},
       "oeste": {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}}
FIS = {"O": "frac_MIRI", "C": "frac_AWD+MIRI", "E": "frac_CD"}

diario = pd.read_csv(RAIZ / "data/processed/nataima_diario.csv")
clima = pd.read_csv(RAIZ / "reports/ronda1_b_corregida/este/e2_clima_dia.csv", parse_dates=["fecha"]).set_index("fecha")
e1 = pd.read_csv(RAIZ / "data/s3/ronda1_b/out/e1_pixeles_lote.csv")
e1 = e1[e1.sensor == "S2"].copy()
e1["fis"] = e1[list(FIS.values())].idxmax(axis=1).map({v: k for k, v in FIS.items()})
e1 = e1[e1[list(FIS.values())].max(axis=1) >= 0.9]
sat = e1.groupby(["fecha", "fis"], as_index=False)[["ndvi", "ndmi", "mndwi"]].mean()  # ndmi = LSWI (NIR, SWIR1)
sat["fecha"] = pd.to_datetime(sat.fecha)


def kc_fao(das: np.ndarray) -> np.ndarray:
    """Kc del arroz FAO-56 (Tabla 12): 1,05 inicial, 1,20 medio, 0,75 final; etapas escaladas a ~125 días."""
    return np.interp(das, [1, 25, 50, 95, 125], [1.05, 1.05, 1.20, 1.20, 0.75])


def series(t: int, trat: str, rot: str | None) -> pd.DataFrame:
    d = diario[(diario.temporada == t) & (diario.tratamiento == trat)].sort_values("das").copy()
    d["fecha"] = pd.Timestamp(SIEMBRAS[t]) + pd.to_timedelta(d.das - 1, unit="D")
    d["eto"] = clima.eto.reindex(d.fecha).to_numpy()
    d["chirps"] = clima.lluvia.reindex(d.fecha).to_numpy()
    d["dr_obs"] = ZR * (0.30 - d.vwc_pct / 100)
    if rot is not None:
        fis = dict(zip(ROT[rot][t], ("O", "C", "E")))[trat]
        s = sat[(sat.fis == fis)].copy()
        s["das"] = (s.fecha - pd.Timestamp(SIEMBRAS[t])).dt.days + 1
        s = s[s.das.between(-10, 140)].sort_values("das")
        d["ndvi"] = np.interp(d.das, s.das, s.ndvi) if len(s) >= 2 else np.nan
        moj = s[(s.ndmi + 0.05 >= s.ndvi) | (s.mndwi > 0)]
        d["mojado_sat"] = d.das.isin(moj.das.round().astype(int))
        d["obs_sat"] = d.das.isin(s.das.round().astype(int))
    return d


def balance(d: pd.DataFrame, version: str, k: float, a: float = 1.0, b: float = 0.0) -> np.ndarray:
    kc = kc_fao(d.das.to_numpy()) if version == "A" else np.clip(a * d.ndvi.to_numpy() + b, 0.2, 1.5)
    lluvia = (d.lluvia_mm if version == "A" else d.chirps).fillna(0).to_numpy()
    riego = d.riego_mm.fillna(0).to_numpy() if version in ("A", "B") else np.zeros(len(d))
    eto = d.eto.fillna(d.eto.mean()).to_numpy()
    moj = d.mojado_sat.to_numpy() if version == "C" else np.zeros(len(d), bool)
    dr, out = 0.0, []
    for i in range(len(d)):
        dr -= lluvia[i] + riego[i]
        if moj[i]:
            dr = min(dr, 0.0)
        ks = 1.0 if dr <= RAW else max(0.0, (TAW - dr) / ((1 - P_AG) * TAW))
        dr += ks * kc[i] * eto[i]
        if dr < 0:
            dr *= (1 - k)
        dr = float(np.clip(dr, -DSAT, TAW))
        out.append(dr)
    return np.array(out)


def clase(dr):
    return np.select([dr < 0, dr > 4.35], ["exceso", "deficit"], "optimo")


def evaluar(version: str, rot: str | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    S = {(t, tr): series(t, tr, rot) for t in (1, 2, 3) for tr in TRAT}
    rejilla_k = np.round(np.arange(0.05, 0.96, 0.05), 2)
    rejilla = [(k, 1.0, 0.0) for k in rejilla_k] if version == "A" else \
        [(k, a, b) for k in rejilla_k[::2] for a in np.arange(0.6, 1.81, 0.2) for b in np.arange(-0.3, 0.51, 0.2)]

    def err(params, temporadas):
        e = []
        for (t, tr), d in S.items():
            if t in temporadas:
                m = d.dr_obs.notna().to_numpy()
                e.append(balance(d, version, *params)[m] - d.dr_obs.to_numpy()[m])
        e = np.concatenate(e)
        return float(np.sqrt(np.nanmean(e ** 2)))

    filas, preds = [], []
    for prueba in (1, 2, 3):
        entren = [t for t in (1, 2, 3) if t != prueba]
        mejor = min(rejilla, key=lambda p: err(p, entren))
        base = np.nanmean(np.concatenate([S[(t, tr)].dr_obs.to_numpy() for t in entren for tr in TRAT]))
        for tr in TRAT:
            d = S[(prueba, tr)]
            m = d.dr_obs.notna().to_numpy()
            p = balance(d, version, *mejor)
            preds.append(pd.DataFrame({"version": version, "rotacion": rot or "-", "temporada": prueba, "tratamiento": tr,
                                       "das": d.das[m], "dr_obs": d.dr_obs[m], "dr_modelo": p[m], "dr_base": base}))
        filas.append({"version": version, "rotacion": rot or "-", "temporada_prueba": prueba, "k": mejor[0],
                      "a": mejor[1], "b": mejor[2]})
    P = pd.concat(preds, ignore_index=True)
    return P, pd.DataFrame(filas)


todo, params = [], []
for version, rot in [("A", None), ("B", "este"), ("B", "oeste"), ("C", "este"), ("C", "oeste")]:
    P, pa = evaluar(version, rot)
    todo.append(P); params.append(pa)
P = pd.concat(todo, ignore_index=True)
P.to_csv(OUT / "predicciones.csv", index=False)
pd.concat(params).to_csv(OUT / "parametros.csv", index=False)

res = []
for (v, r), g in P.groupby(["version", "rotacion"]):
    rmse = np.sqrt(np.mean((g.dr_modelo - g.dr_obs) ** 2))
    rmse_b = np.sqrt(np.mean((g.dr_base - g.dr_obs) ** 2))
    rho = spearmanr(g.dr_modelo, g.dr_obs)[0]
    acc = np.mean(clase(g.dr_modelo.to_numpy()) == clase(g.dr_obs.to_numpy()))
    acc_b = np.mean(clase(g.dr_base.to_numpy()) == clase(g.dr_obs.to_numpy()))
    res.append({"version": v, "rotacion": r, "n_mediciones": len(g), "rmse_mm": round(rmse, 2),
                "rmse_linea_base_mm": round(rmse_b, 2), "spearman": round(rho, 2),
                "acierto_clase": round(acc, 2), "acierto_clase_linea_base": round(acc_b, 2)})
R = pd.DataFrame(res)
R.to_csv(OUT / "resultados.csv", index=False)
print(R.to_string(index=False))
print("\nDistribución real de clases (días medidos):", pd.Series(clase(P[P.version == "A"].dr_obs.to_numpy())).value_counts().to_dict())
print(pd.concat(params).to_string(index=False))
