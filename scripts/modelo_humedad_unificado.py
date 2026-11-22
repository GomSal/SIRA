"""Modelo único de humedad del suelo: agua + clima entran → planta → agua sale, con GS1, GS2 y GS3 como un solo cultivo.

Idea: las tres temporadas son el mismo cultivo de arroz en el mismo lote. Se alinean por días desde siembra y etapa
(vegetativa, reproductiva, maduración) y se entrena un solo modelo con todas las mediciones reales de humedad (TDR,
198 mediciones).

Entradas por tratamiento y día:
  agua que entra  riego del día y acumulado 3/5/10 días (Fig. 4), lluvia CHIRPS 1/3/5/10 días, días desde el último
                  riego y desde la última lluvia ≥ 5 mm
  clima           ETo FAO-56 (NASA POWER) del día y 3/5/10 días, temperatura máxima, radiación, humedad del aire
  planta          NDVI de Sentinel-2 de la franja (rotación "oeste", con el control de bruma), interpolado por día;
                  Kc = 1,0·NDVI + 0,45 (calibrado en §11.4)
  agua que sale   balance diario S(t) = S(t−1)·e^(−1/τ) + lluvia + riego − Kc·ETo con τ = 2, 5, 10 y 20 días, y el
                  déficit FAO-56 Dr (versión B de scripts/prueba_balance.py, k = 0,2/0,5/0,8)
  tiempo          días desde siembra y etapa
Respuesta: humedad volumétrica medida con el TDR (%), solo días con medición real (sin interpolar).

Validación: validación cruzada por bloques de 10 días (todas las franjas de ese bloque salen juntas, para que el clima
compartido no se filtre), 10 particiones con los bloques de las 3 temporadas mezclados. Hiperparámetros elegidos con
una validación interna igual, dentro del entrenamiento. Además se informa dejando una temporada afuera.
Modelos: media (línea base), solo el balance físico (recta sobre S y Dr), Ridge, Random Forest y XGBoost.
Semáforo: déficit < 27,1 % · óptimo 27,1–30 % · exceso > 30 % (capacidad de campo 30 %, RAW del artículo).
Comparaciones: sin riego, sin satélite (Kc de la tabla FAO en vez del NDVI) y sin clima.
Salida: reports/humedad_unificada/
"""
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression, RidgeCV
from sklearn.model_selection import GridSearchCV, GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports" / "humedad_unificada"
OUT.mkdir(parents=True, exist_ok=True)
SIEMBRAS = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}
ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}  # oeste/centro/este
FRAC = {"oeste": "frac_MIRI", "centro": "frac_AWD+MIRI", "este": "frac_CD"}
MALAS_S2 = {"2021-07-07", "2021-08-11", "2021-09-15", "2022-01-03"}  # control de bruma (§4)
TAW, P_AG, ZR = 6.7, 0.65, 150.0
RAW, DSAT = P_AG * TAW, ZR * (0.4765 - 0.30)
SEM = 0
rng = np.random.default_rng(SEM)

# ---------------------------------------------------------------- datos
diario = pd.read_csv(RAIZ / "data/processed/nataima_diario.csv")
clima = pd.read_csv(RAIZ / "data/processed/modelo_b/clima_dia.csv", parse_dates=["fecha"]).set_index("fecha")
pix = pd.read_csv(RAIZ / "data/processed/modelo_b/pixeles_lote_s2_landsat.csv", parse_dates=["fecha"])
s2 = pix[(pix.sensor == "S2") & (pix[list(FRAC.values())].max(axis=1) >= 0.9)
         & ~pix.fecha.dt.strftime("%Y-%m-%d").isin(MALAS_S2)].copy()
s2["fis"] = s2[list(FRAC.values())].idxmax(axis=1).map({v: k for k, v in FRAC.items()})
s2 = s2.groupby(["fecha", "fis"], as_index=False).ndvi.mean()


def ndvi_franja(t: int, trat: str, das: np.ndarray) -> np.ndarray:
    fis = dict(zip(ROT[t], ("oeste", "centro", "este")))[trat]
    s = s2[s2.fis == fis].copy()
    s["das"] = (s.fecha - pd.Timestamp(SIEMBRAS[t])).dt.days + 1
    s = s[s.das.between(-15, 140)].sort_values("das")
    return np.interp(das, s.das, s.ndvi)


def balance_fao(lluvia, riego, kc, eto, k):
    dr, out = 0.0, []
    for i in range(len(lluvia)):
        dr -= lluvia[i] + riego[i]
        ks = 1.0 if dr <= RAW else max(0.0, (TAW - dr) / ((1 - P_AG) * TAW))
        dr += ks * kc[i] * eto[i]
        if dr < 0:
            dr *= (1 - k)
        dr = float(np.clip(dr, -DSAT, TAW))
        out.append(dr)
    return np.array(out)


def reservorio(entra, sale, tau):
    s, out, f = 0.0, [], np.exp(-1 / tau)
    for a, b in zip(entra, sale):
        s = s * f + a - b
        out.append(s)
    return np.array(out)


def dias_desde(evento: np.ndarray) -> np.ndarray:
    out, n = [], 60
    for e in evento:
        n = 0 if e else n + 1
        out.append(min(n, 60))
    return np.array(out)


filas = []
for (t, trat), d in diario.groupby(["temporada", "tratamiento"]):
    d = d.sort_values("das").copy()
    d["fecha"] = pd.Timestamp(SIEMBRAS[t]) + pd.to_timedelta(d.das - 1, unit="D")
    c = clima.reindex(d.fecha)
    d["lluvia"] = c.lluvia.fillna(0).to_numpy()
    d["eto"] = c.eto.ffill().bfill().to_numpy()
    for col in ("T2M_MAX", "RH2M", "ALLSKY_SFC_SW_DWN"):
        d[col] = c[col].ffill().bfill().to_numpy()
    d["riego"] = d.riego_mm.fillna(0)
    for n in (3, 5, 10):
        d[f"riego_{n}d"] = d.riego.rolling(n, min_periods=1).sum()
        d[f"lluvia_{n}d"] = d.lluvia.rolling(n, min_periods=1).sum()
        d[f"eto_{n}d"] = d.eto.rolling(n, min_periods=1).sum()
    d["dias_desde_riego"] = dias_desde(d.riego.to_numpy() > 0)
    d["dias_desde_lluvia"] = dias_desde(d.lluvia.to_numpy() >= 5)
    d["ndvi"] = ndvi_franja(t, trat, d.das.to_numpy())
    d["kc"] = np.clip(1.0 * d.ndvi + 0.45, 0.6, 1.3)
    sale = (d.kc * d.eto).to_numpy()
    kcf = np.interp(d.das, [1, 25, 50, 95, 125], [1.05, 1.05, 1.20, 1.20, 0.75])  # Kc FAO-56 (sin satélite)
    sale_f = kcf * d.eto.to_numpy()
    for tau in (2, 5, 10, 20):
        d[f"S_tau{tau}"] = reservorio((d.lluvia + d.riego).to_numpy(), sale, tau)
        d[f"S_tau{tau}_sin_riego"] = reservorio(d.lluvia.to_numpy(), sale, tau)
        d[f"S_tau{tau}_kcfao"] = reservorio((d.lluvia + d.riego).to_numpy(), sale_f, tau)
    for k in (0.2, 0.5, 0.8):
        d[f"Dr_k{k}"] = balance_fao(d.lluvia.to_numpy(), d.riego.to_numpy(), d.kc.to_numpy(), d.eto.to_numpy(), k)
        d[f"Dr_k{k}_sin_riego"] = balance_fao(d.lluvia.to_numpy(), np.zeros(len(d)), d.kc.to_numpy(), d.eto.to_numpy(), k)
        d[f"Dr_k{k}_kcfao"] = balance_fao(d.lluvia.to_numpy(), d.riego.to_numpy(), kcf, d.eto.to_numpy(), k)
    d["temporada"], d["tratamiento"] = t, trat
    filas.append(d)
D = pd.concat(filas, ignore_index=True)
for e in ("vegetativa", "reproductiva", "maduracion"):
    D[f"etapa_{e}"] = (D.etapa == e).astype(float)
M = D[D.vwc_pct.notna()].reset_index(drop=True)
M["grupo"] = M.temporada * 100 + (M.das - 1) // 10  # bloque de 10 días de una temporada (todas las franjas juntas)
y = M.vwc_pct.to_numpy()

RIEGO = ["riego", "riego_3d", "riego_5d", "riego_10d", "dias_desde_riego"]
LLUVIA = ["lluvia", "lluvia_3d", "lluvia_5d", "lluvia_10d", "dias_desde_lluvia"]
CLIMA = ["eto", "eto_3d", "eto_5d", "eto_10d", "T2M_MAX", "RH2M", "ALLSKY_SFC_SW_DWN"]
PLANTA = ["ndvi", "kc"]
BAL = [f"S_tau{t}" for t in (2, 5, 10, 20)] + [f"Dr_k{k}" for k in (0.2, 0.5, 0.8)]
BAL_SR = [f"S_tau{t}_sin_riego" for t in (2, 5, 10, 20)] + [f"Dr_k{k}_sin_riego" for k in (0.2, 0.5, 0.8)]
BAL_FAO = [f"S_tau{t}_kcfao" for t in (2, 5, 10, 20)] + [f"Dr_k{k}_kcfao" for k in (0.2, 0.5, 0.8)]
TIEMPO = ["das", "etapa_vegetativa", "etapa_reproductiva", "etapa_maduracion"]
CONJ = {
    "completo: agua + clima + planta + balance": RIEGO + LLUVIA + CLIMA + PLANTA + BAL + TIEMPO,
    "sin riego (el agricultor no reporta)": LLUVIA + CLIMA + PLANTA + BAL_SR + TIEMPO,
    "sin satélite (Kc de la tabla FAO)": RIEGO + LLUVIA + CLIMA + BAL_FAO + TIEMPO,
    "sin clima": RIEGO + LLUVIA + PLANTA + BAL + TIEMPO,
}


def modelos():
    return {
        "balance físico (recta)": lambda cols: (make_pipeline(StandardScaler(), LinearRegression()), None, [c for c in cols if c in BAL + BAL_SR + BAL_FAO]),
        "Ridge": lambda cols: (make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-3, 3, 13))), None, cols),
        "Random Forest": lambda cols: (RandomForestRegressor(n_estimators=500, random_state=SEM, n_jobs=-1),
                                       {"max_depth": [3, 5, None], "min_samples_leaf": [3, 8]}, cols),
        "XGBoost": lambda cols: (XGBRegressor(n_estimators=300, subsample=0.8, random_state=SEM, n_jobs=4, verbosity=0),
                                 {"max_depth": [2, 3], "learning_rate": [0.03, 0.1], "min_child_weight": [3, 8]}, cols),
    }


def clase(v):
    return np.select([v < 27.1, v > 30.0], ["deficit", "exceso"], "optimo")


def validar(cols, nombre_mod, esquema):
    est, rej, usar = modelos()[nombre_mod](cols)
    pred = np.full(len(M), np.nan)
    if esquema == "bloques de 10 días":
        cortes = GroupKFold(n_splits=10).split(M, y, M.grupo)
    else:
        cortes = [(np.where(M.temporada != t)[0], np.where(M.temporada == t)[0]) for t in (1, 2, 3)]
    base = np.full(len(M), np.nan)
    for tr, te in cortes:
        X_tr, X_te = M.loc[tr, usar], M.loc[te, usar]
        if rej:
            gs = GridSearchCV(est, rej, cv=GroupKFold(n_splits=5), scoring="neg_root_mean_squared_error", n_jobs=1)
            gs.fit(X_tr, y[tr], groups=M.grupo.to_numpy()[tr])
            m = gs.best_estimator_
        else:
            m = est.fit(X_tr, y[tr])
        pred[te] = m.predict(X_te)
        base[te] = y[tr].mean()
    return pred, base


def resumen(pred, base, mask=None):
    m = np.ones(len(y), bool) if mask is None else mask
    yy, pp, bb = y[m], pred[m], base[m]
    return {"n": int(m.sum()), "rmse_pct": float(np.sqrt(np.mean((yy - pp) ** 2))),
            "rmse_base_pct": float(np.sqrt(np.mean((yy - bb) ** 2))),
            "r2": float(1 - np.sum((yy - pp) ** 2) / np.sum((yy - yy.mean()) ** 2)),
            "r": float(np.corrcoef(yy, pp)[0, 1]),
            "acierto_semaforo": float(np.mean(clase(yy) == clase(pp))),
            "acierto_semaforo_base": float(np.mean(clase(yy) == clase(bb)))}


res, preds = [], []
for esquema in ("bloques de 10 días", "una temporada afuera"):
    for conj, cols in CONJ.items():
        for nm in modelos():
            if nm == "balance físico (recta)" and conj in ("sin clima",):
                continue
            p, b = validar(cols, nm, esquema)
            res.append({"esquema": esquema, "entradas": conj, "modelo": nm, **resumen(p, b)})
            for et in ("vegetativa", "reproductiva", "maduracion"):
                res.append({"esquema": esquema, "entradas": conj, "modelo": nm, "etapa": et,
                            **resumen(p, b, (M.etapa == et).to_numpy())})
            if conj.startswith("completo"):
                preds.append(M[["temporada", "tratamiento", "das", "etapa", "vwc_pct"]].assign(
                    pred=p, base=b, modelo=nm, esquema=esquema))
            print(f"{esquema:22s} | {conj:42s} | {nm:24s} | RMSE {res[-4]['rmse_pct']:.2f} (base {res[-4]['rmse_base_pct']:.2f})"
                  f" R² {res[-4]['r2']:+.2f} semáforo {res[-4]['acierto_semaforo']:.2f} (base {res[-4]['acierto_semaforo_base']:.2f})",
                  flush=True)
R = pd.DataFrame(res)
R["etapa"] = R.get("etapa").fillna("todas")
R.round(4).to_csv(OUT / "resultados.csv", index=False)
pd.concat(preds).to_csv(OUT / "predicciones.csv", index=False)
M.to_csv(OUT / "tabla_entrenamiento.csv", index=False)

# importancia: XGBoost completo, entrenado con todo
cols = CONJ["completo: agua + clima + planta + balance"]
xg = XGBRegressor(n_estimators=300, max_depth=2, learning_rate=0.1, min_child_weight=3, subsample=0.8,
                  random_state=SEM, verbosity=0).fit(M[cols], y)
from sklearn.inspection import permutation_importance  # noqa: E402

pi = permutation_importance(xg, M[cols], y, n_repeats=30, random_state=SEM)
imp = pd.DataFrame({"variable": cols, "importancia": pi.importances_mean}).sort_values("importancia", ascending=False)
imp.to_csv(OUT / "importancia_xgboost.csv", index=False)
print(imp.head(12).to_string(index=False))
