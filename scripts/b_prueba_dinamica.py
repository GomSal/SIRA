"""¿B propaga bien el secado? Prueba de la dinámica, además de la clasificación. Solo evaluación.

Compara dos ARIMAX(1), con el mismo protocolo, las mismas 4 particiones selladas por bloques de 10 días y la misma
regla de color (déficit + 1,5·σ):
  B7  el actual: S_tau20, S_tau5, Dr_k0.5, riego_5d, dias_desde_riego, eto_5d, ndvi
  BS  una sola entrada: S con τ = 45 y α = 0,5 (lluvia × 0,5), la elegida en la etapa 1 (reports/b_reentreno/etapa1/)

Criterios fijados antes de ver resultados:
  1. Dinámica: en los intervalos entre dos mediciones TDR seguidas de la misma franja SIN riego en medio (secados
     observados), el cambio de μ debe seguir al cambio del TDR: correlación de Spearman > 0 con IC 95 % sin el 0, y
     el cambio medio de μ del mismo signo que el del TDR.
  2. Clasificación: con la regla de color aprobada, la exactitud balanceada no debe empeorar (regla de empate:
     IC 95 % pareado de la diferencia que incluye el 0 = empate).
  3. Signo físico: en BS, β de S > 0 en las 4 particiones.
Salida: reports/b_reentreno/dinamica/
"""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.model_selection import StratifiedGroupKFold
from statsmodels.tsa.statespace.sarimax import SARIMAX

warnings.filterwarnings("ignore")
RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports/b_reentreno/dinamica"
OUT.mkdir(parents=True, exist_ok=True)
SEM = 0
SIEMBRAS = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}
ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}
FRAC = {"oeste": "frac_MIRI", "centro": "frac_AWD+MIRI", "este": "frac_CD"}
MALAS = {"2021-07-07", "2021-08-11", "2021-09-15", "2022-01-03"}
B7 = ["S_tau20", "S_tau5", "Dr_k0.5", "riego_5d", "dias_desde_riego", "eto_5d", "ndvi"]
BS = ["S_tau45_a0.5"]

# ---------------------------------------------------------------- datos: tabla de entrenamiento + S τ45 α0,5
M = pd.read_csv(RAIZ / "reports/humedad_unificada/tabla_entrenamiento.csv").sort_values(["temporada", "tratamiento", "das"]).reset_index(drop=True)
d_all = pd.read_csv(RAIZ / "data/processed/nataima_diario.csv")
clima = pd.read_csv(RAIZ / "data/processed/modelo_b/clima_dia.csv", parse_dates=["fecha"]).set_index("fecha")
pix = pd.read_csv(RAIZ / "data/processed/modelo_b/pixeles_lote_s2_landsat.csv", parse_dates=["fecha"])
s2 = pix[(pix.sensor == "S2") & (pix[list(FRAC.values())].max(axis=1) >= 0.9) & ~pix.fecha.dt.strftime("%Y-%m-%d").isin(MALAS)].copy()
s2["fis"] = s2[list(FRAC.values())].idxmax(axis=1).map({v: k for k, v in FRAC.items()})
s2 = s2.groupby(["fecha", "fis"], as_index=False).ndvi.mean()
riego_d, s45 = {}, {}
for (t, trat), g in d_all.groupby(["temporada", "tratamiento"]):
    g = g.sort_values("das")
    c = clima.reindex(pd.Timestamp(SIEMBRAS[t]) + pd.to_timedelta(g.das - 1, unit="D"))
    s = s2[s2.fis == dict(zip(ROT[t], ("oeste", "centro", "este")))[trat]].copy()
    s["das"] = (s.fecha - pd.Timestamp(SIEMBRAS[t])).dt.days + 1
    s = s[s.das.between(-15, 140)].sort_values("das")
    kc = np.clip(1.0 * np.interp(g.das, s.das, s.ndvi) + 0.45, 0.6, 1.3)
    L, R, E = c.lluvia.fillna(0).to_numpy(), g.riego_mm.fillna(0).to_numpy(), c.eto.ffill().bfill().to_numpy()
    v, out = 0.0, []
    for i in range(len(g)):
        v = np.exp(-1 / 45) * v + 0.5 * L[i] + R[i] - kc[i] * E[i]
        out.append(v)
    s45[(t, trat)] = dict(zip(g.das, out))
    riego_d[(t, trat)] = dict(zip(g.das, R))
M["S_tau45_a0.5"] = [s45[(t, tr)][d] for t, tr, d in zip(M.temporada, M.tratamiento, M.das)]
hum = M.vwc_pct.to_numpy()
y = np.select([hum < 27.1, hum < 30], [2, 1], 0)
G = M.grupo.to_numpy()


def ajustar(cols, idx):
    X = M.loc[idx, cols].to_numpy(float)
    mu_x, sd_x = X.mean(0), X.std(0) + 1e-9
    df = pd.DataFrame((X - mu_x) / sd_x, columns=cols).assign(hum=hum[idx], t=M.temporada.to_numpy()[idx],
                                                             tr=M.tratamiento.to_numpy()[idx], das=M.das.to_numpy()[idx])
    en, ex = [], []
    for _, g in df.groupby(["t", "tr"]):
        full = pd.DataFrame({"das": np.arange(g.das.min(), g.das.max() + 1)}).merge(g, on="das", how="left")
        full[cols] = full[cols].interpolate(limit_direction="both")
        en += list(full.hum) + [np.nan] * 30
        ex += full[cols].values.tolist() + [[0.0] * len(cols)] * 30
    r = SARIMAX(np.array(en, float), exog=np.array(ex, float), order=(1, 0, 0), trend="c").fit(disp=False, maxiter=200)
    p = dict(zip(r.model.param_names, np.asarray(r.params)))
    beta = np.array([p[f"x{i + 1}"] for i in range(len(cols))])
    return {"mu_x": mu_x, "sd_x": sd_x, "beta": beta, "cte": p["intercept"] / (1 - p["ar.L1"]),
            "sigma": float(np.sqrt(p["sigma2"] / (1 - p["ar.L1"] ** 2)))}


def mu_de(m, cols, idx):
    return ((M.loc[idx, cols].to_numpy(float) - m["mu_x"]) / m["sd_x"]) @ m["beta"] + m["cte"]


# ---------------------------------------------------------------- predicciones fuera de muestra (prueba sellada)
outer = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=SEM)
MU = {k: np.full(len(M), np.nan) for k in ("B7", "BS")}
SD = {k: np.full(len(M), np.nan) for k in ("B7", "BS")}
betas_bs = []
for f, (dev, te) in enumerate(outer.split(M, y, G)):
    for k, cols in (("B7", B7), ("BS", BS)):
        m = ajustar(cols, dev)
        MU[k][te], SD[k][te] = mu_de(m, cols, te), m["sigma"]
        if k == "BS":
            betas_bs.append(float(m["beta"][0]))


def color(mu, sd):
    dp = 1.5 * (30 - mu) + 1.5 * sd
    return np.select([dp > 4.34, dp > 0], [2, 1], 0)


def bal(pred, ii):
    return np.mean([np.mean(pred[ii][y[ii] == k] == k) for k in range(3) if (y[ii] == k).any()])


filas = []
for k in ("B7", "BS"):
    p = color(MU[k], SD[k])
    filas.append({"modelo": k, **{f"recall_{c}": f"{(p[y == j] == j).sum()}/{(y == j).sum()}" for j, c in enumerate(("verde", "amarillo", "rojo"))},
                  "exactitud_balanceada": round(bal(p, np.arange(len(M))), 3),
                  "spearman_mu_tdr": round(spearmanr(MU[k], hum)[0], 3), "sigma_medio": round(SD[k].mean(), 2)})
CL = pd.DataFrame(filas)

# ---------------------------------------------------------------- dinámica: secados observados
pares = []
for (t, tr), g in M.groupby(["temporada", "tratamiento"]):
    ii = g.sort_values("das").index.to_numpy()
    for a, b in zip(ii[:-1], ii[1:]):
        d0, d1 = M.das[a], M.das[b]
        sin_riego = all(riego_d[(t, tr)].get(dd, 0) == 0 for dd in range(d0 + 1, d1 + 1))
        pares.append({"temporada": t, "tratamiento": tr, "das0": d0, "das1": d1, "sin_riego": sin_riego,
                      "d_tdr": hum[b] - hum[a], **{f"d_mu_{k}": MU[k][b] - MU[k][a] for k in ("B7", "BS")},
                      "grupo": G[a]})
P = pd.DataFrame(pares)
rng = np.random.default_rng(SEM)
din = []
for nombre, Q in (("sin riego en medio (secados)", P[P.sin_riego]), ("todos los intervalos", P)):
    u = Q.grupo.unique()
    for k in ("B7", "BS"):
        rho = spearmanr(Q[f"d_mu_{k}"], Q.d_tdr)[0]
        bs = []
        for _ in range(1000):
            q = pd.concat([Q[Q.grupo == g_] for g_ in rng.choice(u, len(u))])
            bs.append(spearmanr(q[f"d_mu_{k}"], q.d_tdr)[0])
        din.append({"intervalos": nombre, "n": len(Q), "modelo": k, "spearman_dmu_dtdr": round(rho, 3),
                    "ic95": [round(x, 3) for x in np.nanpercentile(bs, [2.5, 97.5])],
                    "d_tdr_medio": round(Q.d_tdr.mean(), 2), "d_mu_medio": round(Q[f"d_mu_{k}"].mean(), 2)})
DIN = pd.DataFrame(din)

# diferencia pareada de exactitud balanceada (BS − B7), bootstrap de bloques
u = np.unique(G)
idx = {g_: np.where(G == g_)[0] for g_ in u}
p7, ps = color(MU["B7"], SD["B7"]), color(MU["BS"], SD["BS"])
dif = []
for _ in range(1000):
    ii = np.concatenate([idx[g_] for g_ in rng.choice(u, len(u))])
    if len(np.unique(y[ii])) == 3:
        dif.append(bal(ps, ii) - bal(p7, ii))
res = {"clasificacion": CL.to_dict("records"), "dinamica": DIN.to_dict("records"),
       "dif_exactitud_balanceada_BS_menos_B7_ic95": [round(x, 3) for x in np.percentile(dif, [2.5, 97.5])],
       "beta_S_BS_por_particion": [round(b, 3) for b in betas_bs]}
json.dump(res, open(OUT / "resultado.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
P.to_csv(OUT / "intervalos.csv", index=False)
print(CL.to_string(index=False))
print(DIN.to_string(index=False))
print("exactitud balanceada BS − B7, IC 95 %:", res["dif_exactitud_balanceada_BS_menos_B7_ic95"])
print("β de S en BS por partición:", res["beta_S_BS_por_particion"])
M[["temporada", "tratamiento", "das", "vwc_pct"]].assign(clase=y, mu_B7=MU["B7"], sd_B7=SD["B7"], color_B7=p7,
                                                         mu_BS=MU["BS"], sd_BS=SD["BS"], color_BS=ps).to_csv(OUT / "predicciones.csv", index=False)
