"""¿Cuántos años de historia necesita el modelo predictor (A)? Prueba de significancia y ranking por año.

Datos: ventana de 3×3 km alrededor de Nataima, rejilla de 30 m (scripts/modelo_a/extraer_ventana.py), 1984–2026,
Sentinel-2 + Landsat 4/5/7/8/9, y clima diario CHIRPS + NASA POWER (scripts/modelo_a/bajar_clima.py).

1. Calibración de Landsat a la escala de Sentinel-2 (por plataforma e índice):
   - L7, L8, L9: recta S2 = a + b·L con pares del mismo pixel a ≤ 2 días (2017–2025).
   - L4, L5 (sin solape con S2): mapeo de cuantiles a L7 en su solape (1999–2011) y luego la recta de L7.
2. Pixeles agrícolas: desviación estándar del NDVI de Sentinel-2 (2017–2025) ≥ umbral (ciclos de siembra y cosecha).
   Se toma una muestra fija de pixeles (los vecinos de un mismo lote son casi copias).
3. Ejemplos: origen = fecha despejada t; objetivo = otra fecha despejada t+Δ con 1 ≤ Δ ≤ 30 días.
   Entradas: índice en t, el otro índice en t, dos rezagos con su edad, pendiente, Δ, día del año, lluvia y ETo de t a
   t+Δ (observadas) y de los 30 días previos, sensor de origen y de objetivo. Respuesta: el índice en t+Δ.
4. Partición cronológica (ventana deslizante), sin tocar la prueba sellada (desde 2025-11-18):
   validación 1 = 2022-11-18…2023-11-17, validación 2 = 2023-11-18…2024-11-17, validación 3 = 2024-11-18…2025-11-17.
   Entrenamiento de cada partición: objetivos antes del inicio de la validación y orígenes desde L años antes.
5. Modelo de la prueba: XGBoost (el más barato de los candidatos; el estudio es sobre los datos, no sobre el modelo).
6. Pruebas:
   a) Historia acumulada L ∈ {1, 2, 3, 4, 5, 6, 8, 10, 13, 16, 20, 25, 30, todo}: RMSE de validación agregado;
      IC 95 % por bootstrap de bloques (partición × 10 días del origen), pareado entre L.
      Regla (fijada antes de ver resultados): el L más corto cuyo RMSE NO es significativamente peor que el del mejor L
      (IC 95 % de la diferencia incluye el 0), en NDVI y en NDMI; se elige el mayor de los dos.
   b) Ranking por año: con toda la historia, quitar un año y medir cuánto empeora la validación (ΔRMSE con IC 95 %).
Salida: reports/modelo_a/anios/
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

RAIZ = Path(__file__).resolve().parents[2]
IN = RAIZ / "data/interim/modelo_a"
OUT = RAIZ / "reports/modelo_a/anios"
OUT.mkdir(parents=True, exist_ok=True)
SEM = 0
rng = np.random.default_rng(SEM)
T0 = time.time()
BASE = pd.Timestamp("1984-01-01")
SELLO = pd.Timestamp("2025-11-18")
FOLDS = [(pd.Timestamp("2022-11-18"), pd.Timestamp("2023-11-18")), (pd.Timestamp("2023-11-18"), pd.Timestamp("2024-11-18")),
         (pd.Timestamp("2024-11-18"), SELLO)]
LS = [1, 2, 3, 4, 5, 6, 8, 10, 13, 16, 20, 25, 30, 99]
N_PIX = 1500
N_BOOT = 1000


def log(m):
    print(f"[{time.time() - T0:6.0f} s] {m}", flush=True)


# ---------------------------------------------------------------- datos
z = np.load(IN / "ventana_3km.npz")
meta = pd.read_csv(IN / "ventana_3km_fechas.csv", parse_dates=["fecha"])
NDVI, NDMI = z["ndvi"].astype(np.float64), z["ndmi"].astype(np.float64)
ok = np.isfinite(NDVI) & np.isfinite(NDMI) & (NDVI > -1) & (NDVI < 1) & (NDMI > -1) & (NDMI < 1)
NDVI[~ok], NDMI[~ok] = np.nan, np.nan
plat = meta.plataforma.str.replace("Sentinel-2[ABC]", "S2", regex=True).to_numpy()
fecha = meta.fecha.to_numpy()
log(f"{len(meta)} imágenes; pixel·fecha despejados {int(ok.sum()):,}; por plataforma: "
    f"{pd.Series(ok.sum(1), index=plat).groupby(level=0).sum().to_dict()}")

# ---------------------------------------------------------------- 1. calibración Landsat → S2
calib = []
es_s2 = plat == "S2"
dia = (meta.fecha - BASE).dt.days.to_numpy()
A_CAL = {}
for p in ("landsat-7", "landsat-8", "landsat-9"):
    for nom, M in (("ndvi", NDVI), ("ndmi", NDMI)):
        xs, ys = [], []
        for i in np.where(plat == p)[0]:
            if meta.fecha[i] >= SELLO:
                continue
            for j in np.where(es_s2 & (np.abs(dia - dia[i]) <= 2))[0]:
                m = np.isfinite(M[i]) & np.isfinite(M[j])
                xs.append(M[i, m]); ys.append(M[j, m])
        x, y = np.concatenate(xs) if xs else np.array([]), np.concatenate(ys) if ys else np.array([])
        b, a = np.polyfit(x, y, 1) if len(x) > 100 else (1.0, 0.0)
        A_CAL[(p, nom)] = (a, b)
        calib.append({"plataforma": p, "indice": nom, "pares_pixel": len(x), "a": a, "b": b,
                      "r2": float(np.corrcoef(x, y)[0, 1] ** 2) if len(x) > 100 else np.nan,
                      "rmse_antes": float(np.sqrt(np.mean((x - y) ** 2))) if len(x) else np.nan,
                      "rmse_despues": float(np.sqrt(np.mean((a + b * x - y) ** 2))) if len(x) else np.nan})
# L4/L5 → L7 por cuantiles en el solape 1999–2011, luego la recta de L7
qs = np.linspace(0.01, 0.99, 99)
sol = (meta.fecha >= "1999-04-15") & (meta.fecha < "2012-01-01")
for p in ("landsat-4", "landsat-5"):
    for nom, M in (("ndvi", NDVI), ("ndmi", NDMI)):
        src = M[(plat == "landsat-5") & sol.to_numpy()]
        ref = M[(plat == "landsat-7") & sol.to_numpy()]
        src, ref = src[np.isfinite(src)], ref[np.isfinite(ref)]
        A_CAL[(p, nom)] = ("cuantiles", np.quantile(src, qs), np.quantile(ref, qs))
        calib.append({"plataforma": p, "indice": nom, "pares_pixel": len(src), "a": np.nan, "b": np.nan, "r2": np.nan,
                      "rmse_antes": np.nan, "rmse_despues": np.nan,
                      "nota": f"cuantiles a L7 en 1999–2011 ({len(src):,} valores L5, {len(ref):,} L7) + recta L7→S2"})
pd.DataFrame(calib).round(4).to_csv(OUT / "calibracion_landsat_s2.csv", index=False)


def a_s2(v, p, nom):
    if p == "S2":
        return v
    c = A_CAL[(p, nom)]
    if c[0] == "cuantiles":
        v = np.interp(v, c[1], c[2])
        c = A_CAL[("landsat-7", nom)]
    return c[0] + c[1] * v


for i, p in enumerate(plat):
    NDVI[i], NDMI[i] = a_s2(NDVI[i], p, "ndvi"), a_s2(NDMI[i], p, "ndmi")
log("calibración: " + "; ".join(f"{r['plataforma']} {r['indice']} n={r['pares_pixel']:,} R²={r['r2']:.2f}"
                                for r in calib if np.isfinite(r["r2"])))

# ---------------------------------------------------------------- 2. pixeles agrícolas
s2_hist = es_s2 & (meta.fecha < SELLO).to_numpy()
sd = np.nanstd(NDVI[s2_hist], axis=0)
nobs = np.isfinite(NDVI[s2_hist]).sum(0)
UMBRAL_SD = 0.15
agri = np.where((sd >= UMBRAL_SD) & (nobs >= 50))[0]
pix = np.sort(rng.choice(agri, size=min(N_PIX, len(agri)), replace=False))
log(f"pixeles con ≥50 obs S2: {(nobs >= 50).sum()} · agrícolas (sd NDVI ≥ {UMBRAL_SD}): {len(agri)} · muestra {len(pix)}")

# ---------------------------------------------------------------- 3. clima y ejemplos
cl = pd.read_csv(IN / "clima_1981_2026.csv", parse_dates=["fecha"]).set_index("fecha")
cl = cl.loc[BASE:]
ll = cl.lluvia.fillna(cl.lluvia.mean()).to_numpy()
et = cl.eto.interpolate().bfill().to_numpy()
cll, cet = np.r_[0, np.cumsum(ll)], np.r_[0, np.cumsum(et)]  # suma (a, b] = c[b+1] − c[a+1]
s = lambda c, a, b: c[np.minimum(b, len(c) - 2) + 1] - c[np.minimum(a, len(c) - 2) + 1]  # noqa: E731
SENS = {"S2": 0, "landsat-4": 1, "landsat-5": 1, "landsat-7": 2, "landsat-8": 3, "landsat-9": 3}
cod = np.array([SENS[p] for p in plat])

filas = []
for p in pix:
    m = np.isfinite(NDVI[:, p])
    idx = np.where(m)[0]
    d, v, w, c = dia[idx], NDVI[idx, p], NDMI[idx, p], cod[idx]
    # una observación por día (si coinciden S2 y Landsat el mismo día, gana S2)
    _, u = np.unique(d, return_index=True)
    d, v, w, c = d[u], v[u], w[u], c[u]
    n = len(d)
    for i in range(n):
        j = i + 1
        while j < n and d[j] - d[i] <= 30:
            l1 = i - 1 if i >= 1 and d[i] - d[i - 1] <= 60 else -1
            l2 = i - 2 if i >= 2 and d[i] - d[i - 2] <= 90 else -1
            filas.append((p, d[i], d[j], v[i], w[i], v[l1] if l1 >= 0 else np.nan, d[i] - d[l1] if l1 >= 0 else np.nan,
                          w[l1] if l1 >= 0 else np.nan, v[l2] if l2 >= 0 else np.nan, d[i] - d[l2] if l2 >= 0 else np.nan,
                          c[i], c[j], v[j], w[j]))
            j += 1
E = pd.DataFrame(filas, columns=["pixel", "d0", "d1", "ndvi0", "ndmi0", "ndvi_l1", "edad_l1", "ndmi_l1", "ndvi_l2",
                                 "edad_l2", "sensor0", "sensor1", "y_ndvi", "y_ndmi"])
E["delta"] = E.d1 - E.d0
E["pend_ndvi"] = (E.ndvi0 - E.ndvi_l1) / E.edad_l1
E["pend_ndmi"] = (E.ndmi0 - E.ndmi_l1) / E.edad_l1
doy = (BASE + pd.to_timedelta(E.d0, unit="D")).dt.day_of_year
E["doy_sin"], E["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
E["lluvia_h"], E["eto_h"] = s(cll, E.d0.to_numpy(), E.d1.to_numpy()), s(cet, E.d0.to_numpy(), E.d1.to_numpy())
E["lluvia_30"], E["eto_30"] = s(cll, E.d0.to_numpy() - 30, E.d0.to_numpy()), s(cet, E.d0.to_numpy() - 30, E.d0.to_numpy())
E["f0"] = BASE + pd.to_timedelta(E.d0, unit="D")
E["f1"] = BASE + pd.to_timedelta(E.d1, unit="D")
E = E[E.f1 < SELLO].reset_index(drop=True)  # la prueba sellada no se toca
E["anio0"] = E.f0.dt.year
X_COLS = ["ndvi0", "ndmi0", "ndvi_l1", "edad_l1", "ndmi_l1", "ndvi_l2", "edad_l2", "pend_ndvi", "pend_ndmi", "delta",
          "doy_sin", "doy_cos", "lluvia_h", "eto_h", "lluvia_30", "eto_30", "sensor0", "sensor1"]
E.groupby("anio0").size().rename("ejemplos").to_csv(OUT / "ejemplos_por_anio.csv")
log(f"ejemplos: {len(E):,} (antes de 2013: {(E.anio0 < 2013).sum():,}) · Δ mediana {E.delta.median():.0f} días")


# ---------------------------------------------------------------- 4–6. pruebas
def ajustar_predecir(tr, va, y):
    m = XGBRegressor(n_estimators=300, max_depth=6, learning_rate=0.1, subsample=0.8, colsample_bytree=0.8,
                     min_child_weight=5, tree_method="hist", n_jobs=16, random_state=SEM, verbosity=0)
    m.fit(E.loc[tr, X_COLS], E.loc[tr, y])
    return m.predict(E.loc[va, X_COLS])


VA = {}
for k, (a, b) in enumerate(FOLDS):
    VA[k] = np.where((E.f0 >= a) & (E.f1 < b))[0]
va_all = np.concatenate([VA[k] for k in range(3)])
bloque = np.concatenate([k * 1000 + ((E.f0.iloc[VA[k]] - FOLDS[k][0]).dt.days // 10).to_numpy() for k in range(3)])
ub, bidx = np.unique(bloque, return_inverse=True)
# bootstrap de bloques como matriz de conteos (repetición × bloque): RMSE* = sqrt(C·Σe² / C·n), sin copiar filas
C = np.stack([np.bincount(rng.integers(0, len(ub), len(ub)), minlength=len(ub)) for _ in range(N_BOOT)]).astype(float)
NB = np.bincount(bidx, minlength=len(ub)).astype(float)
Y = {y: E.loc[va_all, f"y_{y}"].to_numpy() for y in ("ndvi", "ndmi")}
PERS = {y: E.loc[va_all, f"{y}0"].to_numpy() for y in ("ndvi", "ndmi")}


def rmse_boot(err2):
    return np.sqrt((C @ np.bincount(bidx, weights=err2, minlength=len(ub))) / (C @ NB))


res, preds = [], {}
for y in ("ndvi", "ndmi"):
    e2p = (Y[y] - PERS[y]) ** 2
    for L in LS:
        pr = []
        n_tr = 0
        for k, (a, b) in enumerate(FOLDS):
            tr = np.where((E.f1 < a) & (E.f0 >= a - pd.DateOffset(years=L)))[0]
            n_tr += len(tr)
            pr.append(ajustar_predecir(tr, VA[k], f"y_{y}"))
        pr = np.concatenate(pr)
        preds[(y, L)] = (Y[y] - pr) ** 2
        r = float(np.sqrt(preds[(y, L)].mean()))
        res.append({"indice": y, "anios_atras": L if L < 99 else "todo (desde 1984)", "L": L, "ejemplos_entrenamiento_3_particiones": n_tr,
                    "rmse_val": r, "rmse_persistencia": float(np.sqrt(e2p.mean())), "skill_vs_persistencia": 1 - r / np.sqrt(e2p.mean())})
        log(f"{y} L={L:>2}: RMSE {r:.4f} (persistencia {np.sqrt(e2p.mean()):.4f}) · {n_tr:,} ejemplos")
R = pd.DataFrame(res)
np.savez_compressed(OUT.parent / "anios_errores_val.npz", **{f"{y}_{L}": v for (y, L), v in preds.items()}, bloque=bloque)
# IC 95 % de la diferencia pareada frente al mejor L y frente al L siguiente
eleccion = {}
for y in ("ndvi", "ndmi"):
    Ry = R[R.indice == y]
    Lbest = int(Ry.loc[Ry.rmse_val.idxmin(), "L"])
    bb = rmse_boot(preds[(y, Lbest)])
    for i, L in enumerate(LS):
        d = rmse_boot(preds[(y, L)]) - bb
        R.loc[(R.indice == y) & (R.L == L), "dif_vs_mejor"] = float(np.sqrt(preds[(y, L)].mean()) - np.sqrt(preds[(y, Lbest)].mean()))
        R.loc[(R.indice == y) & (R.L == L), "dif_vs_mejor_IC95"] = f"{np.quantile(d, .025):+.4f} a {np.quantile(d, .975):+.4f}"
        R.loc[(R.indice == y) & (R.L == L), "peor_que_el_mejor_signif"] = bool(np.quantile(d, .025) > 0)
        if i + 1 < len(LS):
            g = rmse_boot(preds[(y, L)]) - rmse_boot(preds[(y, LS[i + 1])])
            R.loc[(R.indice == y) & (R.L == L), "ganancia_al_agregar_mas_anios_IC95"] = f"{np.quantile(g, .025):+.4f} a {np.quantile(g, .975):+.4f}"
            R.loc[(R.indice == y) & (R.L == L), "ganancia_signif"] = bool(np.quantile(g, .025) > 0)
    ok_L = R[(R.indice == y) & ~R.peor_que_el_mejor_signif.astype(bool)].L
    eleccion[y] = {"mejor_L": Lbest, "L_mas_corto_no_peor": int(ok_L.min())}
R.round(5).to_csv(OUT / "historia_acumulada.csv", index=False)
L_elegido = max(v["L_mas_corto_no_peor"] for v in eleccion.values())
log(f"elección: {eleccion} → L = {L_elegido}")

# ranking por año (toda la historia; quitar un año)
rank = []
for y in ("ndvi", "ndmi"):
    base = preds[(y, 99)]
    bb = rmse_boot(base)
    for an in range(1984, 2025):
        pr = []
        for k, (a, b) in enumerate(FOLDS):
            tr = np.where((E.f1 < a) & (E.anio0 != an))[0]
            pr.append(ajustar_predecir(tr, VA[k], f"y_{y}"))
        e2 = (Y[y] - np.concatenate(pr)) ** 2
        d = rmse_boot(e2) - bb
        rank.append({"indice": y, "anio": an, "ejemplos_del_anio": int((E.anio0 == an).sum()),
                     "empeora_al_quitarlo": float(np.sqrt(e2.mean()) - np.sqrt(base.mean())),
                     "IC95": f"{np.quantile(d, .025):+.5f} a {np.quantile(d, .975):+.5f}",
                     "aporta_signif": bool(np.quantile(d, .025) > 0), "estorba_signif": bool(np.quantile(d, .975) < 0)})
    log(f"ranking {y} listo")
K = pd.DataFrame(rank).sort_values(["indice", "empeora_al_quitarlo"], ascending=[True, False])
K.round(6).to_csv(OUT / "ranking_por_anio.csv", index=False)
(OUT / "eleccion.json").write_text(json.dumps({"por_indice": eleccion, "anios_atras_elegidos": L_elegido,
                                               "pixeles_muestra": int(len(pix)), "ejemplos": int(len(E)),
                                               "segundos": round(time.time() - T0)}, indent=1), encoding="utf-8")
log("listo")
