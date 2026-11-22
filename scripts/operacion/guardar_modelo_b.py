"""Guarda el Modelo B de uso y los calendarios de riego supuesto. No selecciona nada: misma especificación ya elegida.

1. ARIMAX(1) con las 7 entradas de reports/semaforo_clasificador/modelo_de_uso.json, ajustado con las 198 mediciones
   TDR de Nataima, exactamente como ARIMAXClasificador.fit de scripts/semaforo_clasificador.py (series diarias de cada
   franja apiladas con 30 días vacíos; el filtro de Kalman salta los días sin medición).
2. Calendarios de riego supuesto AWD+MIRI: los 3 calendarios medidos (GS1, GS2, GS3) por días desde siembra.
   En operación el Tramo 1 se corre con cada uno y se promedia μ (aval del 21-nov: "calendario medio de AWD+MIRI").
3. Control: las ecuaciones de scripts/operacion/b_entradas.py reproducen las entradas de la tabla de entrenamiento.

Salida: models/semaforo_b/arimax.json, models/semaforo_b/calendarios_riego.csv (3 tratamientos × 3 temporadas)
"""
import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

warnings.filterwarnings("ignore")
RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts/operacion"))
from b_entradas import ENTRADAS, MODELO_B, ArimaxB, propagar  # noqa: E402

OUT = MODELO_B.parent
OUT.mkdir(parents=True, exist_ok=True)
M = pd.read_csv(RAIZ / "reports/humedad_unificada/tabla_entrenamiento.csv")
M = M.sort_values(["temporada", "tratamiento", "das"]).reset_index(drop=True)
uso = json.load(open(RAIZ / "reports/semaforo_clasificador/modelo_de_uso.json", encoding="utf-8"))
assert uso["elegido"] == "ARIMAX" and uso["hiperparametros"]["p"] == 1 and uso["entradas"] == ENTRADAS

# ---------------------------------------------------------------- 1. ARIMAX de uso
X = M[ENTRADAS].to_numpy(float)
mu_x, sd_x = X.mean(0), X.std(0) + 1e-9
Z = (X - mu_x) / sd_x
endog, exog = [], []
df = pd.DataFrame(Z, columns=ENTRADAS).assign(hum=M.vwc_pct, t=M.temporada, tr=M.tratamiento, das=M.das)
for _, g in df.groupby(["t", "tr"]):
    full = pd.DataFrame({"das": np.arange(g.das.min(), g.das.max() + 1)}).merge(g, on="das", how="left")
    full[ENTRADAS] = full[ENTRADAS].interpolate(limit_direction="both")
    endog += list(full.hum) + [np.nan] * 30
    exog += full[ENTRADAS].values.tolist() + [[0.0] * len(ENTRADAS)] * 30
res = SARIMAX(np.array(endog, float), exog=np.array(exog, float), order=(1, 0, 0), trend="c").fit(disp=False, maxiter=200)
par = dict(zip(res.model.param_names, np.asarray(res.params)))
phi = par["ar.L1"]
modelo = {
    "modelo": "ARIMAX(1): humedad TDR = c + Σ β·x_estandarizada + u, u AR(1) (statsmodels SARIMAX, order (1,0,0), trend c)",
    "datos": "198 mediciones TDR de Nataima, GS1+GS2+GS3, 3 tratamientos (reports/humedad_unificada/tabla_entrenamiento.csv)",
    "entradas": ENTRADAS, "media_x": mu_x.tolist(), "desv_x": sd_x.tolist(),
    "beta": [par[f"x{i + 1}"] for i in range(len(ENTRADAS))], "intercepto": par["intercept"], "phi": phi,
    "sigma2_innovacion": par["sigma2"], "sigma_pct": float(np.sqrt(par["sigma2"] / (1 - phi ** 2))),
    "prediccion": "sin TDR: μ = c/(1 − φ) + Σ β·(x − media)/desv ; σ = √(σ²/(1 − φ²))",
    "regla_color": "déficit = 1,5·(30 − μ) mm; se pinta con déficit + 1,5·σ: suficiente ≤ 0 · media 0–4,34 · baja > 4,34 mm",
    "validacion": "prueba sellada por bloques de 10 días: reports/semaforo_clasificador/ y regla de color del 22-nov-2026",
}
json.dump(modelo, open(MODELO_B, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
B = ArimaxB()
print("ARIMAX de uso:", {c: round(b, 2) for c, b in zip(ENTRADAS, B.beta)},
      f"c {B.c:.2f} φ {B.phi:.2f} σ {B.sigma:.2f} %")
mu = B.mu({c: M[c].to_numpy(float) for c in ENTRADAS})
print(f"en los 198 datos: μ medio {mu.mean():.2f} (TDR {M.vwc_pct.mean():.2f}), r {np.corrcoef(mu, M.vwc_pct)[0, 1]:.2f}")

# ---------------------------------------------------------------- 2. calendarios de riego supuesto
d = pd.read_csv(RAIZ / "data/processed/nataima_diario.csv")
cal = d.assign(col=d.tratamiento + "|GS" + d.temporada.astype(str)).pivot_table(index="das", columns="col", values="riego_mm")
cal = cal.reindex(np.arange(1, d.das.max() + 1)).fillna(0)
cal.index.name = "das"
cal.to_csv(OUT / "calendarios_riego.csv")
print("calendarios de riego medidos (mm totales):", cal.sum().round(0).to_dict(), "· días", len(cal))

# ---------------------------------------------------------------- 3. control de las ecuaciones
SIEMBRAS = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}
ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}
FRAC = {"oeste": "frac_MIRI", "centro": "frac_AWD+MIRI", "este": "frac_CD"}
MALAS = {"2021-07-07", "2021-08-11", "2021-09-15", "2022-01-03"}
clima = pd.read_csv(RAIZ / "data/processed/modelo_b/clima_dia.csv", parse_dates=["fecha"]).set_index("fecha")
pix = pd.read_csv(RAIZ / "data/processed/modelo_b/pixeles_lote_s2_landsat.csv", parse_dates=["fecha"])
s2 = pix[(pix.sensor == "S2") & (pix[list(FRAC.values())].max(axis=1) >= 0.9) & ~pix.fecha.dt.strftime("%Y-%m-%d").isin(MALAS)].copy()
s2["fis"] = s2[list(FRAC.values())].idxmax(axis=1).map({v: k for k, v in FRAC.items()})
s2 = s2.groupby(["fecha", "fis"], as_index=False).ndvi.mean()
dif = []
for (t, trat), g in d.groupby(["temporada", "tratamiento"]):
    g = g.sort_values("das")
    fe = pd.Timestamp(SIEMBRAS[t]) + pd.to_timedelta(g.das - 1, unit="D")
    c = clima.reindex(fe)
    s = s2[s2.fis == dict(zip(ROT[t], ("oeste", "centro", "este")))[trat]].copy()
    s["das"] = (s.fecha - pd.Timestamp(SIEMBRAS[t])).dt.days + 1
    s = s[s.das.between(-15, 140)].sort_values("das")
    col = lambda v: np.asarray(v, float)[:, None]  # noqa: E731
    o = propagar(col(c.lluvia.fillna(0)), col(c.eto.ffill().bfill()), col(g.riego_mm.fillna(0)),
                 col(np.interp(g.das, s.das, s.ndvi)), np.ones((len(g), 1), bool))[0]
    ref = M[(M.temporada == t) & (M.tratamiento == trat)].set_index("das")
    i = g.das.to_numpy().searchsorted(ref.index.to_numpy())
    dif.append({c_: float(np.abs(o[c_][i, 0] - ref[c_].to_numpy()).max()) for c_ in ENTRADAS})
dif = pd.DataFrame(dif).max()
print("control, diferencia máxima con la tabla de entrenamiento:", dif.round(6).to_dict())
assert (dif < 1e-6).all(), "las ecuaciones de operación no reproducen las del entrenamiento"
print(f"guardado: {MODELO_B.relative_to(RAIZ)} y {(OUT / 'calendarios_riego.csv').relative_to(RAIZ)}")
