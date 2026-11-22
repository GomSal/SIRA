"""Reentrenamiento de B, ETAPA 1: elegir la ecuación del balance en validación (la prueba sellada NO se toca).

Compiten 17 variantes, cada una SOLA (no se mezclan):
  S, reservorio (índice de precipitación antecedente con entrada neta; informe §11.3–11.4):
      S(t) = e^(−1/τ)·S(t−1) + α·L(t) + R(t) − Kc(t)·ETo(t),   S = 0 el día de siembra
      τ ∈ {2, 5, 10, 20, 30, 45, 60} días, α ∈ {0,5, 1,0}                      → 14 variantes
  Dr, déficit FAO-56 con exceso que drena (scripts/modelo_humedad_unificado.py, balance_fao):
      Dr(t) = clip[ Dr(t−1) − L(t) − R(t) + Ks·Kc(t)·ETo(t) ],  drenaje k si Dr < 0,  Dr = 0 el día de siembra
      k ∈ {0,2, 0,5, 0,8}                                                       → 3 variantes
  L lluvia CHIRPS, ETo FAO-56 de NASA POWER, R riego registrado de Nataima (así fue la realidad del ensayo),
  Kc = 1,0·NDVI + 0,45 (0,6–1,3; NDVI Sentinel-2 de la franja, calibrado en el informe §11.4).

Modelo de la etapa 1 (fijo, sin búsqueda de hiperparámetros, para no sobreajustar la validación):
  regresión logística multinomial, entradas estandarizadas, clases balanceadas, C = 1,
  x = [ecuación, lluvia de 5 días, ETo de 5 días]. Sin contadores de riego (acordado).
Respuesta: clase TDR (suficiente ≥ 30 %, media 27,1–30 %, baja < 27,1 %), 198 mediciones, GS1+GS2+GS3.

Validación: las MISMAS 4 particiones externas del semáforo (StratifiedGroupKFold, bloques de 10 días, semilla 0).
  Dentro de cada una, solo con sus datos de desarrollo: validación interna de 5 particiones (semilla 0+f+1) →
  AUC-ROC macro fuera de muestra de cada variante. Gana la mayor. La parte de prueba de cada partición no se usa.
Regla de empate (fijada antes de correr): si el IC 95 % de (AUC del mejor − AUC de la variante), por bootstrap de
  bloques de 10 días (300), incluye el 0, empatan; entre las empatadas gana la más cercana a lo calibrado en §11.4
  (reservorio con τ = 45, α = 0,5): distancia |log(τ/45)| + |α − 0,5|; las Dr cuentan como las más lejanas.
También se corre la misma elección con los 198 datos (validación interna, semilla 99): es la que usaría el modelo de uso.
Salida: reports/b_reentreno/etapa1/
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports/b_reentreno/etapa1"
OUT.mkdir(parents=True, exist_ok=True)
SEM = 0
SIEMBRAS = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}
ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}
FRAC = {"oeste": "frac_MIRI", "centro": "frac_AWD+MIRI", "este": "frac_CD"}
MALAS_S2 = {"2021-07-07", "2021-08-11", "2021-09-15", "2022-01-03"}
TAW, P_AG, ZR = 6.7, 0.65, 150.0
RAW, DSAT = P_AG * TAW, ZR * (0.4765 - 0.30)
TAUS, ALFAS, KS_ = (2, 5, 10, 20, 30, 45, 60), (0.5, 1.0), (0.2, 0.5, 0.8)

# ---------------------------------------------------------------- series diarias (misma construcción que la tabla de B)
diario = pd.read_csv(RAIZ / "data/processed/nataima_diario.csv")
clima = pd.read_csv(RAIZ / "data/processed/modelo_b/clima_dia.csv", parse_dates=["fecha"]).set_index("fecha")
pix = pd.read_csv(RAIZ / "data/processed/modelo_b/pixeles_lote_s2_landsat.csv", parse_dates=["fecha"])
s2 = pix[(pix.sensor == "S2") & (pix[list(FRAC.values())].max(axis=1) >= 0.9)
         & ~pix.fecha.dt.strftime("%Y-%m-%d").isin(MALAS_S2)].copy()
s2["fis"] = s2[list(FRAC.values())].idxmax(axis=1).map({v: k for k, v in FRAC.items()})
s2 = s2.groupby(["fecha", "fis"], as_index=False).ndvi.mean()


def ndvi_franja(t, trat, das):
    fis = dict(zip(ROT[t], ("oeste", "centro", "este")))[trat]
    s = s2[s2.fis == fis].copy()
    s["das"] = (s.fecha - pd.Timestamp(SIEMBRAS[t])).dt.days + 1
    s = s[s.das.between(-15, 140)].sort_values("das")
    return np.interp(das, s.das, s.ndvi)


def reservorio(L, R, sale, tau, alfa):
    s, out, f = 0.0, [], np.exp(-1 / tau)
    for l_, r_, b_ in zip(L, R, sale):
        s = f * s + alfa * l_ + r_ - b_
        out.append(s)
    return np.array(out)


def deficit(L, R, kc, eto, k):
    dr, out = 0.0, []
    for i in range(len(L)):
        dr -= L[i] + R[i]
        ks = 1.0 if dr <= RAW else max(0.0, (TAW - dr) / ((1 - P_AG) * TAW))
        dr += ks * kc[i] * eto[i]
        if dr < 0:
            dr *= (1 - k)
        dr = float(np.clip(dr, -DSAT, TAW))
        out.append(dr)
    return np.array(out)


VARS = {f"S_tau{t}_a{a}": ("S", t, a) for t in TAUS for a in ALFAS}
VARS.update({f"Dr_k{k}": ("Dr", k, None) for k in KS_})
filas = []
for (t, trat), d in diario.groupby(["temporada", "tratamiento"]):
    d = d.sort_values("das").reset_index(drop=True)
    d["fecha"] = pd.Timestamp(SIEMBRAS[t]) + pd.to_timedelta(d.das - 1, unit="D")
    c = clima.reindex(d.fecha)
    L = c.lluvia.fillna(0).to_numpy()
    eto = c.eto.ffill().bfill().to_numpy()
    R = d.riego_mm.fillna(0).to_numpy()
    kc = np.clip(1.0 * ndvi_franja(t, trat, d.das.to_numpy()) + 0.45, 0.6, 1.3)
    for nom, (tipo, p, a) in VARS.items():
        d[nom] = reservorio(L, R, kc * eto, p, a) if tipo == "S" else deficit(L, R, kc, eto, p)
    d["lluvia_5d"] = pd.Series(L).rolling(5, min_periods=1).sum().to_numpy()
    d["eto_5d"] = pd.Series(eto).rolling(5, min_periods=1).sum().to_numpy()
    d["temporada"], d["tratamiento"] = t, trat
    filas.append(d[d.vwc_pct.notna()])
M = pd.concat(filas, ignore_index=True).sort_values(["temporada", "tratamiento", "das"]).reset_index(drop=True)
# control: debe coincidir con la tabla del semáforo (mismas filas, mismo orden, mismas S_tau20 y Dr_k0.5 con α = 1)
T = pd.read_csv(RAIZ / "reports/humedad_unificada/tabla_entrenamiento.csv").sort_values(
    ["temporada", "tratamiento", "das"]).reset_index(drop=True)
assert len(M) == len(T) == 198 and (M[["temporada", "tratamiento", "das"]].values == T[["temporada", "tratamiento", "das"]].values).all()
assert np.allclose(M["S_tau20_a1.0"], T.S_tau20) and np.allclose(M["Dr_k0.5"], T["Dr_k0.5"])
y = np.select([M.vwc_pct < 27.1, M.vwc_pct < 30.0], [2, 1], 0)
G = (M.temporada * 100 + (M.das - 1) // 10).to_numpy()


def auc_macro(yy, P):
    a = [roc_auc_score(yy == k, P[:, k]) for k in range(3) if 0 < (yy == k).sum() < len(yy)]
    return float(np.mean(a)) if a else np.nan


def oof(idx, var, semilla):
    """Predicciones fuera de muestra de la logística de la etapa 1, con validación interna por bloques."""
    X = M.loc[idx, [var, "lluvia_5d", "eto_5d"]].to_numpy()
    P = np.full((len(idx), 3), np.nan)
    for a, b in StratifiedGroupKFold(5, shuffle=True, random_state=semilla).split(X, y[idx], G[idx]):
        m = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, class_weight="balanced", max_iter=5000))
        m.fit(X[a], y[idx][a])
        pb = np.zeros((len(b), 3))
        pb[:, m[-1].classes_] = m.predict_proba(X[b])
        P[b] = pb
    return P


def distancia(var):
    tipo, p, a = VARS[var]
    return abs(np.log(p / 45)) + abs(a - 0.5) if tipo == "S" else 99.0


def elegir(idx, semilla, etiqueta):
    Ps = {v: oof(idx, v, semilla) for v in VARS}
    aucs = {v: auc_macro(y[idx], P) for v, P in Ps.items()}
    mejor = max(aucs, key=aucs.get)
    g = G[idx]
    u = np.unique(g)
    pos = {q: np.where(g == q)[0] for q in u}
    rng = np.random.default_rng(SEM)
    muestras = [ii for ii in (np.concatenate([pos[q] for q in rng.choice(u, len(u))]) for _ in range(300))
                if len(np.unique(y[idx][ii])) == 3]
    filas_ = []
    for v in VARS:
        dif = np.array([auc_macro(y[idx][ii], Ps[mejor][ii]) - auc_macro(y[idx][ii], Ps[v][ii]) for ii in muestras])
        lo, hi = np.quantile(dif, [0.025, 0.975])
        filas_.append({"particion": etiqueta, "variante": v, "tipo": VARS[v][0], "auc_macro_validacion": round(aucs[v], 4),
                       "dif_vs_mejor": round(aucs[mejor] - aucs[v], 4), "dif_IC95": f"{lo:+.3f} a {hi:+.3f}",
                       "empata_con_mejor": bool(lo <= 0 <= hi) or v == mejor})
    F = pd.DataFrame(filas_)
    emp = F[F.empata_con_mejor].variante.tolist()
    elegida = min(emp, key=distancia)
    F["elegida"] = F.variante == elegida
    return F, mejor, elegida, emp


outer = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=SEM)
todo, resumen = [], []
for f, (dev, te) in enumerate(outer.split(M, y, G)):
    F, mejor, eleg, emp = elegir(dev, SEM + f + 1, f"desarrollo de la prueba {f + 1}")
    todo.append(F)
    resumen.append({"particion": f + 1, "n_desarrollo": len(dev), "mejor_auc": mejor, "auc_mejor": F.auc_macro_validacion.max(),
                    "empatadas": len(emp), "elegida": eleg, "auc_elegida": float(F[F.elegida].auc_macro_validacion.iloc[0])})
    print(f"partición {f + 1}: mejor {mejor} ({F.auc_macro_validacion.max():.3f}) · empatadas {len(emp)} · elegida {eleg}", flush=True)
F, mejor, eleg, emp = elegir(np.arange(len(M)), SEM + 99, "todos los datos (modelo de uso)")
todo.append(F)
resumen.append({"particion": "todos", "n_desarrollo": len(M), "mejor_auc": mejor, "auc_mejor": F.auc_macro_validacion.max(),
                "empatadas": len(emp), "elegida": eleg, "auc_elegida": float(F[F.elegida].auc_macro_validacion.iloc[0])})
print(f"todos los datos: mejor {mejor} ({F.auc_macro_validacion.max():.3f}) · empatadas {len(emp)} · elegida {eleg}")
V = pd.concat(todo)
V.to_csv(OUT / "validacion_por_variante.csv", index=False)
pd.DataFrame(resumen).to_csv(OUT / "eleccion.csv", index=False)
tabla = V[V.particion.str.startswith("desarrollo")].groupby("variante").agg(
    auc_media=("auc_macro_validacion", "mean"), veces_empata=("empata_con_mejor", "sum"), veces_elegida=("elegida", "sum"))
tabla["spearman_con_TDR_descriptivo"] = [M[v].corr(M.vwc_pct, method="spearman") * (1 if VARS[v][0] == "S" else -1) for v in tabla.index]
tabla = tabla.sort_values("auc_media", ascending=False).round(3)
tabla.to_csv(OUT / "resumen_por_variante.csv")
print(tabla.to_string())
json.dump({"modelo_etapa1": "logística multinomial C=1, balanceada, x=[ecuación, lluvia_5d, eto_5d]",
           "variantes": len(VARS), "regla_empate": "IC 95 % de la diferencia de AUC incluye 0 → la más cercana a τ=45, α=0,5"},
          open(OUT / "protocolo.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
