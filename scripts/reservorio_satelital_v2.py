"""Reservorio SIN riego real, con el satélite como fuente de observación: óptico primero, radar si no hay óptico.

  S(t) = S(t−1)·e^(−1/τ) + α·lluvia_CHIRPS − Kc(NDVI)·ETo_POWER
  día con imagen óptica despejada (Sentinel-2 o Landsat):  S ← (1 − w_o)·S + w_o·Ŝ_opt
  si no, día con radar Sentinel-1:                         S ← (1 − w_r)·S + w_r·Ŝ_rad
  Ŝ_opt = ridge(NDWI, LSWI(=NDMI), MNDWI, NDVI, STR, sensor, días)   STR = (1 − SWIR)²/(2·SWIR), base de OPTRAM
  Ŝ_rad = ridge(VH, VV, VH−VV, órbita, Kc, días)
  Dr_estimado = c0·S + c1
Ŝ se entrena contra el reservorio con riego real (τ = 45, α = 0,5) en las 2 temporadas de entrenamiento; τ, α, w_o,
w_r, c0, c1 también. Se prueba en la 3ª contra el TDR. Variantes: ideal (riego real), P0 (nada), óptico, radar,
óptico + radar de relleno (la regla acordada).
Uso: python scripts/reservorio_satelital_v2.py [oeste|este]
"""
import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

RAIZ = Path(__file__).resolve().parents[1]
src = (RAIZ / "scripts/prueba_balance.py").read_text(encoding="utf-8").split("todo, params = []")[0]
ns = {"__file__": str(RAIZ / "scripts/prueba_balance.py")}
exec(compile(src, "prueba_balance", "exec"), ns)
ROT = sys.argv[1] if len(sys.argv) > 1 else "oeste"
SIEMBRAS = {t: pd.Timestamp(v) for t, v in ns["SIEMBRAS"].items()}
FRAC = {"O": "frac_MIRI", "C": "frac_AWD+MIRI", "E": "frac_CD"}

# ---- óptico por franja física y fecha: S2 con píxeles ≥ 90 % en la franja; Landsat con franja dominante ≥ 50 %
e1 = pd.read_csv(RAIZ / "data/s3/ronda1_b/out/e1_pixeles_lote.csv", parse_dates=["fecha"])
e1["fis"] = e1[list(FRAC.values())].idxmax(axis=1).map({v: k for k, v in FRAC.items()})
e1["pureza"] = e1[list(FRAC.values())].max(axis=1)
e1 = e1[((e1.sensor == "S2") & (e1.pureza >= 0.9)) | ((e1.sensor == "L") & (e1.pureza >= 0.5))]
e1["str_"] = (1 - e1.swir1.clip(1e-3, 1)) ** 2 / (2 * e1.swir1.clip(1e-3, 1))
opt = (e1.groupby(["fecha", "fis", "sensor"], as_index=False)[["ndwi", "ndmi", "mndwi", "ndvi", "str_"]].mean()
       .sort_values("sensor", ascending=False).drop_duplicates(["fecha", "fis"]))  # si hay S2 y L el mismo día, S2
radar = pd.read_csv(RAIZ / "data/interim/radar_s1_franjas.csv", parse_dates=["fecha"]).drop_duplicates(["fecha", "franja_fisica"])

S = {}
for t in (1, 2, 3):
    for pos, trat in zip(("O", "C", "E"), ns["ROT"][ROT][t]):
        d = ns["series"](t, trat, ROT).reset_index(drop=True)
        d["kc"] = np.clip(1.0 * d.ndvi.to_numpy() + 0.45, 0.2, 1.5)
        o = opt[opt.fis == pos].copy(); o["das"] = (o.fecha - SIEMBRAS[t]).dt.days + 1
        d = d.join(o.set_index("das")[["ndwi", "ndmi", "mndwi", "str_", "sensor"]].rename(columns={"ndwi": "o_ndwi",
                   "ndmi": "o_lswi", "mndwi": "o_mndwi", "str_": "o_str", "sensor": "o_sensor"}), on="das")
        d["o_ndvi"] = d.das.map(o.set_index("das").ndvi)
        r = radar[radar.franja_fisica == pos].copy(); r["das"] = (r.fecha - SIEMBRAS[t]).dt.days + 1
        d = d.join(r.set_index("das")[["vv_db", "vh_db", "vh_vv_db", "orbita"]], on="das")
        S[(t, trat)] = d


def rasgos_opt(d):
    return np.c_[d.o_ndwi, d.o_lswi, d.o_mndwi, d.o_ndvi, d.o_str, (d.o_sensor == "L").astype(float), d.das / 125]


def rasgos_rad(d):
    return np.c_[d.vh_db, d.vv_db, d.vh_vv_db, (d.orbita == "ascending_150").astype(float), d.kc, d.das / 125]


def ridge(X, y, lam=3.0):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    beta = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    return lambda Xn: (Xn - mu) / sd @ beta + y.mean()


def correr(d, tau, alfa, riego_real, wo=0.0, wr=0.0, so=None, sr=None):
    dec, s, out = np.exp(-1 / tau), 0.0, []
    lluvia = (d.lluvia_mm if riego_real else d.chirps).fillna(0).to_numpy()
    riego = d.riego_mm.fillna(0).to_numpy() if riego_real else np.zeros(len(d))
    eto, kc = d.eto.fillna(d.eto.mean()).to_numpy(), d.kc.fillna(1.05).to_numpy()
    for i in range(len(d)):
        s = s * dec + alfa * lluvia[i] + riego[i] - kc[i] * eto[i]
        if so is not None and not np.isnan(so[i]):
            s = (1 - wo) * s + wo * so[i]
        elif sr is not None and not np.isnan(sr[i]):
            s = (1 - wr) * s + wr * sr[i]
        out.append(s)
    return np.array(out)


for d in S.values():
    d["S_real"] = correr(d, 45, 0.5, riego_real=True)
clase = lambda v: np.select([v < 0, v > 4.35], [0, 2], 1)  # noqa: E731


def predictor(ent, rasgos, col):
    X, y = [], []
    for (t, tr), d in S.items():
        if t in ent:
            m = d[col].notna() & d.kc.notna()
            X.append(rasgos(d[m])); y.append(d.S_real[m])
    X, y = np.vstack(X), np.concatenate(y)
    f = ridge(X, y)

    def aplicar(d):
        out = np.full(len(d), np.nan)
        m = (d[col].notna() & d.kc.notna()).to_numpy()
        if m.any():
            out[m] = f(rasgos(d[m]))
        return out
    return aplicar, len(y)


res = []
W = [0.25, 0.5, 0.75, 1.0]
for prueba in (1, 2, 3):
    ent = [t for t in (1, 2, 3) if t != prueba]
    f_opt, n_o = predictor(ent, rasgos_opt, "o_ndwi")
    f_rad, n_r = predictor(ent, rasgos_rad, "vh_db")
    cache = {k: (f_opt(d), f_rad(d)) for k, d in S.items()}
    variantes = {"ideal (riego real)": (True, [0.0], [0.0], False, False),
                 "P0 (sin satélite)": (False, [0.0], [0.0], False, False),
                 "óptico": (False, W, [0.0], True, False),
                 "radar": (False, [0.0], W, False, True),
                 "óptico + radar de relleno": (False, W, W, True, True)}
    for nombre, (rr, WO, WR, uo, ur) in variantes.items():
        def evaluar(p, temps):
            tau, alfa, wo, wr = p
            xs, ys = [], []
            for (t, tr), d in S.items():
                if t in temps:
                    so, sr = cache[(t, tr)]
                    s = correr(d, tau, alfa, rr, wo, wr, so if uo else None, sr if ur else None)
                    m = d.dr_obs.notna().to_numpy()
                    xs.append(s[m]); ys.append(d.dr_obs.to_numpy()[m])
            return np.concatenate(xs), np.concatenate(ys)

        def costo(p):
            x, yy = evaluar(p, ent)
            A = np.c_[x, np.ones_like(x)]
            c = np.linalg.lstsq(A, yy, rcond=None)[0]
            return float(np.sqrt(np.mean((A @ c - yy) ** 2))), c

        rej = list(itertools.product([20, 30, 45, 60, 90], [0.1, 0.25, 0.5, 0.75], WO, WR))
        mejor = min(rej, key=lambda p: costo(p)[0])
        c = costo(mejor)[1]
        x, yy = evaluar(mejor, [prueba])
        yh = c[0] * x + c[1]
        yb = float(np.mean(evaluar(mejor, ent)[1]))
        res.append({"rotacion": ROT, "variante": nombre, "temporada_prueba": prueba, "tau": mejor[0], "alfa": mejor[1],
                    "w_optico": mejor[2], "w_radar": mejor[3], "obs_opt_entren": n_o, "obs_rad_entren": n_r,
                    "spearman": round(float(spearmanr(yh, yy)[0]), 2),
                    "rmse_mm": round(float(np.sqrt(np.mean((yh - yy) ** 2))), 2),
                    "rmse_base_mm": round(float(np.sqrt(np.mean((yb - yy) ** 2))), 2),
                    "acierto_clase": round(float(np.mean(clase(yh) == clase(yy))), 2),
                    "acierto_base": round(float(np.mean(clase(np.full_like(yy, yb)) == clase(yy))), 2), "n": len(yy)})
R = pd.DataFrame(res)
(RAIZ / "reports/radar").mkdir(parents=True, exist_ok=True)
R.to_csv(RAIZ / f"reports/radar/reservorio_satelital_v2_{ROT}.csv", index=False)
orden = list(dict.fromkeys(R.variante))
print(R.pivot_table(index="variante", columns="temporada_prueba", values=["spearman", "rmse_mm"]).reindex(orden).round(2).to_string())
print("RMSE línea base:", R.groupby("temporada_prueba").rmse_base_mm.first().to_dict())
cob = {k: (int(d.o_ndwi.notna().sum()), int(d.vh_db.notna().sum()), int((d.o_ndwi.notna() | d.vh_db.notna()).sum()))
       for k, d in S.items()}
print("observaciones por serie (óptico, radar, unión):", {f"GS{k[0]}": v for k, v in cob.items() if k[1] == "CD"})
