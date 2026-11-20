"""Reservorio SIN riego real: el satélite reemplaza al cuaderno de riego (versión de producción).

  S(t) = S(t−1)·e^(−1/τ) + α·lluvia_CHIRPS − Kc(NDVI)·ETo_POWER          (sin riego: nadie lo reporta)
  cada fecha de radar:  S ← (1 − w)·S + w·Ŝ_radar                          (asimilación / "nudging")
  Ŝ_radar = ridge(VH, VV, VH−VV, órbita, NDVI, días desde siembra)         (entrenado contra el S con riego real)
  variante óptica: si Sentinel-2 ve el lote inundado (LSWI + 0,05 ≥ NDVI o MNDWI > 0) → S ← máx(S, S_inundado)
  Dr_estimado = c0·S + c1

Todo se calibra con 2 temporadas y se prueba en la 3ª (τ, α, w, ridge, c0, c1). Kc = 1,0·NDVI + 0,45 (sección 8.4).
Comparación en la temporada de prueba, días con TDR medido:
  ideal   = reservorio con riego REAL (techo, sección 8.4)
  P0      = sin riego y sin satélite (piso)
  radar   = sin riego + asimilación del radar
  radar+ó = además, reinicio por inundación vista por Sentinel-2
Uso: python scripts/reservorio_satelital.py [este|oeste]
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
radar = pd.read_csv(RAIZ / "data/interim/radar_s1_franjas.csv", parse_dates=["fecha"])
KC_A, KC_B = 1.0, 0.45

S = {}
for t in (1, 2, 3):
    for pos, trat in zip(("O", "C", "E"), ns["ROT"][ROT][t]):
        d = ns["series"](t, trat, ROT).reset_index(drop=True)
        d["kc"] = np.clip(KC_A * d.ndvi.to_numpy() + KC_B, 0.2, 1.5)
        r = radar[radar.franja_fisica == pos].copy()
        r["das"] = (r.fecha - SIEMBRAS[t]).dt.days + 1
        r = r.drop_duplicates("das").set_index("das")
        d = d.join(r[["vv_db", "vh_db", "vh_vv_db", "orbita"]], on="das")
        S[(t, trat)] = d


def correr(d, tau, alfa, riego_real, w=0.0, s_hat=None, inund=None):
    dec, s, out = np.exp(-1 / tau), 0.0, []
    lluvia = (d.lluvia_mm if riego_real else d.chirps).fillna(0).to_numpy()
    riego = d.riego_mm.fillna(0).to_numpy() if riego_real else np.zeros(len(d))
    eto = d.eto.fillna(d.eto.mean()).to_numpy()
    kc = d.kc.fillna(1.05).to_numpy()
    for i in range(len(d)):
        s = s * dec + alfa * lluvia[i] + riego[i] - kc[i] * eto[i]
        if s_hat is not None and not np.isnan(s_hat[i]):
            s = (1 - w) * s + w * s_hat[i]
        if inund is not None and d.mojado_sat.iloc[i]:
            s = max(s, inund)
        out.append(s)
    return np.array(out)


def rasgos(d):
    return np.c_[d.vh_db, d.vv_db, d.vh_vv_db, (d.orbita == "ascending_150").astype(float),
                 d.kc, d.das / 125.0]


def ridge(X, y, lam=1.0):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    beta = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    return lambda Xn: (Xn - mu) / sd @ beta + y.mean()


TAU0, ALFA0 = 45, 0.5  # reservorio "verdadero" con riego real (sección 8.4)
for d in S.values():
    d["S_real"] = correr(d, TAU0, ALFA0, riego_real=True)

clase = lambda v: np.select([v < 0, v > 4.35], [0, 2], 1)  # noqa: E731
res, preds = [], []
for prueba in (1, 2, 3):
    ent = [t for t in (1, 2, 3) if t != prueba]
    # 1) radar → S (solo fechas de radar de las temporadas de entrenamiento)
    X, y = [], []
    for (t, tr), d in S.items():
        if t in ent:
            m = d.vh_db.notna() & d.kc.notna()
            X.append(rasgos(d[m])); y.append(d.S_real[m])
    X, y = np.vstack(X), np.concatenate(y)
    modelo = ridge(X, y, lam=3.0)
    s_inund = float(np.percentile(y, 80))

    def s_hat(d):
        out = np.full(len(d), np.nan)
        m = (d.vh_db.notna() & d.kc.notna()).to_numpy()
        if m.any():
            out[m] = modelo(rasgos(d[m]))
        return out

    variantes = {"ideal": dict(riego_real=True), "P0": dict(riego_real=False),
                 "radar": dict(riego_real=False, usa_radar=True),
                 "radar+optico": dict(riego_real=False, usa_radar=True, usa_opt=True)}
    for nombre, cfg in variantes.items():
        rej = itertools.product([20, 30, 45, 60, 90], [0.1, 0.25, 0.5, 0.75],
                                [0.25, 0.5, 0.75, 1.0] if cfg.get("usa_radar") else [0.0])

        def evaluar(p, temps):
            tau, alfa, w = p
            xs, ys = [], []
            for (t, tr), d in S.items():
                if t in temps:
                    s = correr(d, tau, alfa, cfg["riego_real"], w, s_hat(d) if cfg.get("usa_radar") else None,
                               s_inund if cfg.get("usa_opt") else None)
                    m = d.dr_obs.notna().to_numpy()
                    xs.append(s[m]); ys.append(d.dr_obs.to_numpy()[m])
            return np.concatenate(xs), np.concatenate(ys)

        def costo(p):
            x, yy = evaluar(p, ent)
            A = np.c_[x, np.ones_like(x)]
            c = np.linalg.lstsq(A, yy, rcond=None)[0]
            return float(np.sqrt(np.mean((A @ c - yy) ** 2))), c

        mejor = min(rej, key=lambda p: costo(p)[0])
        c = costo(mejor)[1]
        x, yy = evaluar(mejor, [prueba])
        yh = c[0] * x + c[1]
        yb = float(np.mean(evaluar(mejor, ent)[1]))
        res.append({"rotacion": ROT, "variante": nombre, "temporada_prueba": prueba, "tau": mejor[0], "alfa": mejor[1],
                    "w_radar": mejor[2], "spearman": round(float(spearmanr(yh, yy)[0]), 2),
                    "rmse_mm": round(float(np.sqrt(np.mean((yh - yy) ** 2))), 2),
                    "rmse_base_mm": round(float(np.sqrt(np.mean((yb - yy) ** 2))), 2),
                    "acierto_clase": round(float(np.mean(clase(yh) == clase(yy))), 2),
                    "acierto_base": round(float(np.mean(clase(np.full_like(yy, yb)) == clase(yy))), 2), "n": len(yy)})
R = pd.DataFrame(res)
out = RAIZ / "reports" / "radar"
out.mkdir(parents=True, exist_ok=True)
R.to_csv(out / f"reservorio_satelital_{ROT}.csv", index=False)
print(R.pivot_table(index="variante", columns="temporada_prueba", values=["spearman", "rmse_mm"]).round(2).to_string())
print("RMSE línea base por temporada:", R.groupby("temporada_prueba").rmse_base_mm.first().to_dict())
