"""Semáforo de humedad por pixel, de hoy a 30 días:  A + GEOS-S2S → ecuaciones S y Dr → B (ARIMAX) → mapa.

Esquema (acordado 21/22-nov-2026; documento: docs/esquema_semaforo.md). Nada se entrena aquí:
  A   Random Forest de NDVI y NDMI por pixel (data/interim/modelo_a/kaggle_final/resultados_a/modelos): detecta la
      siembra con la curva del NDVI y pronostica NDVI/NDMI de hoy a +30 días.
  Ecuaciones (scripts/operacion/b_entradas.py, las mismas con que se entrenó B):
      S_τ(t) = e^(−1/τ)·S_τ(t−1) + lluvia + riego − Kc·ETo        τ = 20 y 5 días
      Dr(t)  = Dr(t−1) − lluvia − riego + Ks·Kc·ETo                 FAO-56, k = 0,5
      Kc = 1,0·NDVI + 0,45 (0,6–1,3)
    Tramo 1, siembra → hoy: CHIRPS y NASA POWER observados, NDVI observado del pixel, riego supuesto AWD+MIRI
      (los 3 calendarios medidos en Nataima; se corre con cada uno y se promedia μ).
    Tramo 2, hoy → +30 días: lluvia y ETo de GEOS-S2S (último inicio ≤ hoy, 5 miembros, corregidos con
      paso8_geos_correccion.csv), NDVI de A, sin riego desde hoy. Los estados siguen del tramo 1 sin cortar.
  B   ARIMAX(1) de uso (models/semaforo_b/arimax.json), entrenado con el TDR de Nataima: con las 7 entradas de cada día
      da μ = humedad volumétrica esperada (%) y σ = 3,12 % (su error típico sin TDR).
  Color: déficit = 1,5·(30 − μ) mm; se pinta con déficit + margen (1,5·σ = 4,7 mm), intervalos de Martínez-Vega et al.
      2025: suficiente ≤ 0 · media 0–4,34 mm · baja > 4,34 mm. La tarjeta muestra ese mismo déficit con margen.
  Alarma tardía (no cambia el color): si el NDMI pronosticado por A queda en el 10 % más seco de la curva normal del
      arroz aun sumándole el error de A, un pixel suficiente lleva la marca "posible enfermedad o deficiencia nutricional".

Uso:  python scripts/operacion/semaforo_c.py [--hoy 2026-11-15] [--lado_km 2] [--lat 4.1895 --lon -74.9621]
      python scripts/operacion/semaforo_c.py --qa      (reproduce las predicciones de prueba de A con esta tubería)
Salida: reports/semaforo_c/ (mapas PNG por horizonte, resumen.json, pixeles.csv.gz, mapa.html)
"""
import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from PIL import Image
from pyproj import Transformer

warnings.filterwarnings("ignore")
RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from b_entradas import DSAT, ENTRADAS, TAW, ArimaxB, propagar, propagar_dr  # noqa: E402
CUBO = RAIZ / "data/interim/modelo_a/kaggle_nb1/datos_a"
RES_A = RAIZ / "data/interim/modelo_a/kaggle_final/resultados_a"
GEOS = RAIZ / "data/processed/modelo_a/geos"
BASE = pd.Timestamp("2000-01-01")
SELLO, ANIOS = pd.Timestamp("2025-11-18"), 10
Z_SECO = -1.28  # 10 % más seco de la curva normal del NDMI (alarma tardía)
HORIZONTE = 30
SENS = {"S2": 0, "landsat-7": 1, "landsat-8": 2, "landsat-9": 2}
CLASES = {0: "suficiente", 1: "media", 2: "baja"}

ap = argparse.ArgumentParser()
ap.add_argument("--hoy", default="2026-11-15")
ap.add_argument("--lado_km", type=float, default=2.0)
ap.add_argument("--lat", type=float, default=4.1895)
ap.add_argument("--lon", type=float, default=-74.9621)
ap.add_argument("--salida", default=str(RAIZ / "reports/semaforo_c"))
ap.add_argument("--qa", action="store_true")
ap.add_argument("--riego", default="CD", choices=["AWD+MIRI", "MIRI", "CD"], help="régimen supuesto hasta hoy")
A = ap.parse_args()
OUT = Path(A.salida)
OUT.mkdir(parents=True, exist_ok=True)
T0 = time.time()


def log(m):
    print(f"[{time.time() - T0:6.0f} s] {m}", flush=True)


# ---------------------------------------------------------------- 1. cubo de la zona
z = np.load(CUBO / "cubo.npz")
NG, X0, Y0 = int(z["n"]), float(z["x0"]), float(z["y0"])
xc, yc = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(A.lon, A.lat)
cc, fc = int((xc - X0) // 10), int((Y0 - yc) // 10)
med = int(A.lado_km * 1000 / 20)
f0, f1, c0, c1 = max(fc - med, 0), min(fc + med, NG), max(cc - med, 0), min(cc + med, NG)
FIL, COL = np.mgrid[f0:f1, c0:c1]
IDX = (FIL * NG + COL).ravel()
NF, NC = f1 - f0, c1 - c0
if A.qa:  # solo los pixeles de la prueba de A
    IDX = np.unique(pd.read_csv(RES_A / "paso8_predicciones_prueba.csv.gz", usecols=["pixel"]).pixel.to_numpy())
meta = pd.read_csv(CUBO / "cubo_fechas.csv", parse_dates=["fecha"])
HOY = pd.Timestamp("2026-11-18") if A.qa else pd.Timestamp(A.hoy)
DHOY = (HOY - BASE).days
plat = meta.plataforma.str.replace(r"Sentinel-2[A-Z]", "S2", regex=True).to_numpy()
dia = (meta.fecha - BASE).dt.days.to_numpy()
usar = dia <= DHOY  # causal: solo imágenes hasta hoy
orden = np.lexsort((plat != "S2", dia))
orden = orden[usar[orden]]
DIA, PLAT = dia[orden], plat[orden]
V = z["ndvi"][:, IDX][orden].astype(np.float32)
W = z["ndmi"][:, IDX][orden].astype(np.float32)
AZ = z["azul"][:, IDX][orden].astype(np.float32)
log(f"zona {NF}×{NC} pixeles de 10 m ({NF * NC / 100:.0f} ha) · {len(DIA)} imágenes hasta {HOY.date()}")

# calibración Landsat → Sentinel-2 (la de A)
cal = pd.read_csv(RES_A / "paso2_calibracion_landsat_s2.csv")
for r in cal.itertuples():
    M_ = V if r.indice == "ndvi" else W
    M_[PLAT == r.plataforma] = r.a + r.b * M_[PLAT == r.plataforma]

# bruma (misma regla de A): cae > 0,10 frente a la fecha válida anterior y siguiente (≤ 30 d), o azul ≥ 0,075 con
# vecinas vegetadas
for k in range(V.shape[1]):
    ok = np.where(np.isfinite(V[:, k]))[0]
    if len(ok) < 3:
        continue
    v, d, az = V[ok, k], DIA[ok], AZ[ok, k]
    pv, nv = np.r_[np.nan, v[:-1]], np.r_[v[1:], np.nan]
    pok = np.r_[False, (d[1:] - d[:-1]) <= 30]
    nok = np.r_[(d[1:] - d[:-1]) <= 30, False]
    malo = ok[(pok & nok & (v < pv - 0.10) & (v < nv - 0.10)) | (pok & nok & (az >= 0.075) & (pv > 0.5) & (nv > 0.5))]
    V[malo, k] = np.nan
    W[malo, k] = np.nan
del AZ
COD = np.array([SENS.get(p, 1) for p in PLAT])
# pixeles agrícolas, misma regla de A: desviación del NDVI de Sentinel-2 ≥ 0,15 con ≥ 50 observaciones
s2 = PLAT == "S2"
AGRI = (np.nanstd(V[s2], axis=0) >= 0.15) & (np.isfinite(V[s2]).sum(0) >= 50)
log(f"calibración y bruma listas · agrícolas {AGRI.sum()} de {len(AGRI)}")


# ---------------------------------------------------------------- 2. siembra por la curva del NDVI (regla de A)
def siembras(d, v):
    out, k = [], 0
    while k < len(v):
        if v[k] <= 0.35:
            j = k + 1
            while j < len(v) and d[j] - d[k] <= 90 and v[j] < 0.65:
                if v[j] <= 0.35:
                    k = j
                j += 1
            if j < len(v) and v[j] >= 0.65 and 20 <= d[j] - d[k] <= 90:
                out.append(d[k])
                k = j
                while k < len(v) and v[k] > 0.35:
                    k += 1
                continue
        k += 1
    return np.array(out, dtype=int)


def das_de(s, dias):
    if len(s) == 0:
        return np.full(len(dias), np.nan)
    i = np.searchsorted(s, dias, side="right") - 1
    out = np.where(i >= 0, dias - s[np.clip(i, 0, None)], np.nan).astype(float)
    out[(out > 150) | (out < 0)] = np.nan
    return out


def etapa_de(das):
    return np.select([np.isnan(das), das <= 45, das <= 82, das <= 130], [0, 1, 2, 3], 0)


SIEM = []
for k in range(V.shape[1]):
    ok = np.isfinite(V[:, k])
    SIEM.append(siembras(DIA[ok], V[ok, k]))
log(f"siembras detectadas en {sum(len(s) > 0 for s in SIEM)} de {len(SIEM)} pixeles")

# ---------------------------------------------------------------- 3. pronóstico de A
meta_a = json.load(open(RES_A / "modelos/meta.json", encoding="utf-8"))
MOD = {i: joblib.load(RES_A / f"modelos/predictor_{i}.joblib") for i in ("ndvi", "ndmi")}
for m in MOD.values():
    m.n_jobs = -1


def ejemplos(k, d0_fijo=None, d1s=None):
    """Variables de A para el pixel k: origen = última fecha despejada ≤ hoy (o d0_fijo), objetivos d1s."""
    ok = np.isfinite(V[:, k]) & np.isfinite(W[:, k])
    d, v, w, c = DIA[ok], V[ok, k], W[ok, k], COD[ok]
    if len(d) == 0:
        return None
    _, u = np.unique(d, return_index=True)
    d, v, w, c = d[u], v[u], w[u], c[u]
    i0 = (np.searchsorted(d, d0_fijo) if d0_fijo is not None else len(d) - 1)
    if d0_fijo is not None and (i0 >= len(d) or d[i0] != d0_fijo):
        return None
    d1 = np.asarray(d1s)
    n = len(d1)
    d0 = np.full(n, d[i0])
    a1 = d[i0] - d[i0 - 1] if i0 >= 1 else np.nan
    a2 = d[i0] - d[i0 - 2] if i0 >= 2 else np.nan
    a1 = np.nan if a1 > 60 else a1
    a2 = np.nan if a2 > 90 else a2
    das0 = das_de(SIEM[k], d0)
    das1 = das0 + (d1 - d0)
    das1[das1 > 150] = np.nan
    doy0 = (BASE + pd.to_timedelta(d0, unit="D")).day_of_year.to_numpy()
    doy1 = (BASE + pd.to_timedelta(d1, unit="D")).day_of_year.to_numpy()
    f = {"ndvi0": np.full(n, v[i0]), "ndmi0": np.full(n, w[i0]), "delta": d1 - d0,
         "sensor0": np.full(n, c[i0]), "sensor1": np.zeros(n),
         "das0": das0, "das1": das1, "etapa0": etapa_de(das0), "etapa1": etapa_de(das1),
         "doy_sin": np.sin(2 * np.pi * doy0 / 365.25), "doy_cos": np.cos(2 * np.pi * doy0 / 365.25),
         "doy1_sin": np.sin(2 * np.pi * doy1 / 365.25), "doy1_cos": np.cos(2 * np.pi * doy1 / 365.25),
         "ndvi_l1": np.full(n, v[i0 - 1] if np.isfinite(a1) else np.nan), "edad_l1": np.full(n, a1),
         "ndmi_l1": np.full(n, w[i0 - 1] if np.isfinite(a1) else np.nan),
         "ndvi_l2": np.full(n, v[i0 - 2] if np.isfinite(a2) else np.nan), "edad_l2": np.full(n, a2)}
    f["pend_ndvi"] = (f["ndvi0"] - f["ndvi_l1"]) / f["edad_l1"]
    f["pend_ndmi"] = (f["ndmi0"] - f["ndmi_l1"]) / f["edad_l1"]
    return pd.DataFrame(f)


def predecir(T):
    return {i: MOD[i].predict(T[meta_a["variables"][i]].to_numpy(np.float32)) for i in ("ndvi", "ndmi")}


if A.qa:
    # QA: las filas de prueba de A cuyos pixeles caen en la zona, reconstruidas con esta tubería
    P = pd.read_csv(RES_A / "paso8_predicciones_prueba.csv.gz", parse_dates=["f0", "f1"])
    col_de = {g: k for k, g in enumerate(IDX)}
    P = P[P.pixel.isin(col_de)].copy()
    tablas, grupos = [], []
    for (px, f0_), g in P.groupby(["pixel", "f0"]):
        T = ejemplos(col_de[px], (f0_ - BASE).days, (g.f1 - BASE).dt.days.to_numpy())
        if T is None:
            continue
        tablas.append(T)
        grupos.append(g)
    log(f"QA: {len(grupos)} orígenes reconstruidos de {P.groupby(['pixel', 'f0']).ngroups}")
    Q = pd.concat(grupos)
    pr = predecir(pd.concat(tablas, ignore_index=True))
    Q["qa_ndvi"], Q["qa_ndmi"] = pr["ndvi"], pr["ndmi"]
    res = {i: {"filas": len(Q), "dif_media_abs": float((Q[f"qa_{i}"] - Q[f"pred_{i}"]).abs().mean()),
               "dif_max_abs": float((Q[f"qa_{i}"] - Q[f"pred_{i}"]).abs().max()),
               "corr": float(np.corrcoef(Q[f"qa_{i}"], Q[f"pred_{i}"])[0, 1])} for i in ("ndvi", "ndmi")}
    json.dump(res, open(OUT / "qa_reproduce_a.json", "w", encoding="utf-8"), indent=1)
    log(f"QA, esta tubería frente a las predicciones de prueba de A en Kaggle: {res}")
    raise SystemExit

D1 = DHOY + np.arange(0, HORIZONTE + 1)
partes, pix_ok = [], []
for k in np.where(AGRI)[0]:
    T = ejemplos(k, d1s=D1)
    if T is None or T.delta.iloc[0] > 20:  # sin imagen despejada en los últimos 20 días
        continue
    T["k"] = k
    partes.append(T)
    pix_ok.append(k)
T = pd.concat(partes, ignore_index=True)
T["h"] = np.tile(np.arange(HORIZONTE + 1), len(pix_ok))
obs0 = T.delta == 0  # hoy hay imagen: el día 0 es observado
pr = predecir(T)
T["ndvi"] = np.where(obs0, T.ndvi0, pr["ndvi"])
T["ndmi"] = np.where(obs0, T.ndmi0, pr["ndmi"])
log(f"A pronosticó {len(pix_ok)} pixeles × {HORIZONTE + 1} días ({len(T):,} filas)")

NDVI = T.ndvi.to_numpy().reshape(len(pix_ok), HORIZONTE + 1)
NDMI = T.ndmi.to_numpy().reshape(len(pix_ok), HORIZONTE + 1)
DAS = T.das1.to_numpy().reshape(len(pix_ok), HORIZONTE + 1)
DELTA = T.delta.to_numpy().reshape(len(pix_ok), HORIZONTE + 1)
EN_CULTIVO = np.isfinite(DAS)

# ---------------------------------------------------------------- 4. clima: GEOS-S2S corregido
cl = pd.read_csv(CUBO / "clima.csv", parse_dates=["fecha"]).set_index("fecha")
per = cl.loc[SELLO - pd.DateOffset(years=ANIOS):SELLO - pd.Timedelta(days=1)]
CLIM_ETO = per.eto.groupby(per.index.day_of_year).mean()
CLIM_LL = per.lluvia.groupby(per.index.day_of_year).mean()
CLIM_T = ((per.T2M_MAX + per.T2M_MIN) / 2).groupby(per.index.day_of_year).mean()
corr = pd.read_csv(RES_A / "paso8_geos_correccion.csv")
fac = {(r.variable, int(r.mes), int(r.plazo)): r.factor for r in corr.itertuples()}
g = pd.read_csv(GEOS / "geos_nrt.csv", parse_dates=["fecha"])
g["inicio"] = pd.to_datetime(g.inicio.astype(str))
INI = g.inicio[g.inicio <= HOY].max()
g = g[g.inicio == INI]
gp = g.pivot_table(index=["miembro", "fecha"], columns="variable", values="valor").reset_index()
for v_ in ("pr_sfc", "t2mmax", "t2mmin"):
    if v_ not in gp:
        gp[v_] = np.nan
gp["plazo"] = (gp.fecha - INI).dt.days
gp["plz"] = np.select([gp.plazo <= 10, gp.plazo <= 20], [0, 1], 2)
gp["mes"] = gp.fecha.dt.month
gp["pr"] = gp.pr_sfc * 86400 * [fac.get(("pr", m, p), 1.0) for m, p in zip(gp.mes, gp.plz)]
tmed = (gp.t2mmax + gp.t2mmin) / 2 - 273.15 + np.array([fac.get(("t", m, p), 0.0) for m, p in zip(gp.mes, gp.plz)])
doy = gp.fecha.dt.day_of_year
gp["eto"] = doy.map(CLIM_ETO) * (tmed + 17.8) / (doy.map(CLIM_T) + 17.8)
FECHAS = pd.date_range(HOY + pd.Timedelta(days=1), periods=HORIZONTE)
MIEMBROS = sorted(gp.miembro.unique())
LL = np.zeros((len(MIEMBROS), HORIZONTE))
ETO = np.zeros((len(MIEMBROS), HORIZONTE))
relleno = 0
for a_, mb in enumerate(MIEMBROS):
    q = gp[gp.miembro == mb].set_index("fecha").reindex(FECHAS)
    sin = q.pr.isna() | q.eto.isna()
    relleno += int(sin.sum())
    LL[a_] = q.pr.fillna(pd.Series(FECHAS.day_of_year.map(CLIM_LL), index=FECHAS)).to_numpy()
    ETO[a_] = q.eto.fillna(pd.Series(FECHAS.day_of_year.map(CLIM_ETO), index=FECHAS)).to_numpy()
log(f"GEOS-S2S inicio {INI.date()} · {len(MIEMBROS)} miembros · lluvia media 30 d {LL.sum(1).mean():.0f} mm · "
    f"ETo {ETO.sum(1).mean():.0f} mm · días con climatología {relleno} de {len(MIEMBROS) * HORIZONTE}")

# ---------------------------------------------------------------- 5. Modelo B: tramo 1 (siembra → hoy) y tramo 2 (hoy → +30)
MB = ArimaxB()
CAL = pd.read_csv(RAIZ / "models/semaforo_b/calendarios_riego.csv", index_col="das")  # das 1… · "<tratamiento>|GS<t>"
CAL = CAL[[c_ for c_ in CAL if c_.split("|")[0] == A.riego]]  # los 3 calendarios medidos del régimen supuesto
NCAL, NP = CAL.shape[1], len(pix_ok)
EN0 = np.isfinite(DAS[:, 0])  # en cultivo hoy
SOW = np.where(EN0, DHOY - np.nan_to_num(DAS[:, 0]), DHOY + 1).astype(int)  # día de siembra (das = 0 en A, día 1 en B)
DIAS1 = np.arange(int(SOW[EN0].min()) if EN0.any() else DHOY, DHOY + 1)
f1_ = BASE + pd.to_timedelta(DIAS1, unit="D")
L1 = cl.lluvia.reindex(f1_).fillna(0).to_numpy()  # CHIRPS observado
E1 = cl.eto.reindex(f1_).ffill().bfill().to_numpy()  # ETo FAO-56 con NASA POWER observado
NV1 = np.empty((len(DIAS1), NP))  # NDVI observado del pixel, llevado a diario (calibrado y sin bruma)
for i, k in enumerate(pix_ok):
    ok = np.isfinite(V[:, k])
    NV1[:, i] = np.interp(DIAS1, DIA[ok], V[ok, k])
NV1[-1] = NDVI[:, 0]  # hoy: el mismo NDVI del día 0 de A
ACT1 = (DIAS1[:, None] >= SOW[None, :]) & EN0[None, :]
DAS1 = DIAS1[:, None] - SOW[None, :] + 1  # días desde siembra como en el entrenamiento (siembra = día 1)
R1 = np.zeros((len(DIAS1), NCAL, NP))
for c_ in range(NCAL):
    tabla_r = np.r_[CAL.iloc[:, c_].to_numpy(), 0.0]  # después del último día del calendario: sin riego
    R1[:, c_] = np.where(ACT1, tabla_r[np.clip(DAS1, 1, len(CAL) + 1) - 1], 0.0)
fo1 = (len(DIAS1), NCAL, NP)
X1, EST = propagar(np.broadcast_to(L1[:, None, None], fo1), np.broadcast_to(E1[:, None, None], fo1), R1,
                   np.broadcast_to(NV1[:, None, :], fo1), np.broadcast_to(ACT1[:, None, :], fo1))
# B solo hasta hoy: μ de hoy = promedio de los 3 calendarios. El margen de B (1,5·σ) se aplica una vez, al arranque:
#   Dr0 = 1,5·(30 − μ0) + 1,5·σ        (aval del 22-nov: "ARIMAX y su corrección de margen solo quedan hasta hoy")
MU0 = MB.mu({c_: X1[c_][-1] for c_ in ENTRADAS}).mean(0)
DR0_ESP = 1.5 * (30.0 - MU0)  # déficit esperado por B (sin margen), vista técnica
DR0 = DR0_ESP + 1.5 * MB.sigma
# tramo 2, de hoy a +30: déficit FAO-56 (Dr_modificada) desde Dr0, con GEOS-S2S y el NDVI de A, sin riego.
# Se pinta con los límites del artículo tal cual, sin volver a sumar margen.
fo2 = (HORIZONTE, len(MIEMBROS), NP)
DR2 = propagar_dr(DR0[None, :], np.broadcast_to(LL.T[:, :, None], fo2), np.broadcast_to(ETO.T[:, :, None], fo2),
                  np.broadcast_to(NDVI[:, 1:].T[:, None, :], fo2))
DRP = np.c_[np.clip(DR0, -DSAT, TAW), DR2.mean(1).T]  # déficit que se pinta y se muestra (promedio de miembros GEOS)
DRB = np.c_[DR0_ESP, np.full((NP, HORIZONTE), np.nan)]  # déficit esperado de B, solo hoy
MU = 30.0 - DRP / 1.5  # humedad equivalente (%) del déficit que se pinta
S20 = np.c_[X1["S_tau20"][-1].mean(0), np.full((NP, HORIZONTE), np.nan)]  # S de hoy (entrada de B)
DR_EC = np.c_[X1["Dr_k0.5"][-1].mean(0), np.full((NP, HORIZONTE), np.nan)]  # Dr de la ecuación hoy (entrada de B)
CLASE = np.select([DRP > 4.34, DRP > 0], [2, 1], 0)  # Martínez-Vega et al. 2025: 27,1 % y 30 %
log(f"B: tramo 1 desde {(BASE + pd.Timedelta(days=int(DIAS1[0]))).date()} con {NCAL} calendarios {A.riego} · "
    f"μ hoy mediana {np.nanmedian(MU0[EN0]):.1f} % (P25–P75 {np.nanpercentile(MU0[EN0], 25):.1f}–"
    f"{np.nanpercentile(MU0[EN0], 75):.1f}) · σ {MB.sigma:.2f} % → margen {1.5 * MB.sigma:.1f} mm")

# alarma tardía del NDMI pronosticado por A (no cambia el color): hoja más seca de lo normal con suelo suficiente
cur = pd.read_csv(RES_A / "paso1_curva_por_dias_desde_siembra.csv")
mu_c = lambda das: np.interp(das, cur.das_b + 5, cur.ndmi0_mean)  # noqa: E731  (centro del tramo de 10 días)
sd_c = lambda das: np.interp(das, cur.das_b + 5, cur.ndmi0_std)  # noqa: E731
pa = pd.read_csv(RES_A / "paso8_prueba_clima_observado.csv")
pa = pa[(pa.indice == "ndmi") & pa.grupo.str.match(r"h\d+$")]
H_ERR = pa.grupo.str[1:].astype(int).to_numpy()
RMSE_H = np.interp(DELTA, H_ERR, pa.rmse.to_numpy())
RMSE_H = np.where(DELTA == 0, 0.0, RMSE_H)
ANOM = ((NDMI + RMSE_H - mu_c(DAS)) / sd_c(DAS)) < Z_SECO
MARCA = (CLASE == 0) & ANOM
CLASE = np.where(EN_CULTIVO, CLASE, -1)
MARCA &= EN_CULTIVO
# días hasta "baja" sin riego
DIAS_BAJA = np.where((CLASE == 2).any(1), (CLASE == 2).argmax(1), -1)

# ---------------------------------------------------------------- 6. salida: mapas, resumen y visor
COLORES = {0: (46, 158, 91, 190), 1: (242, 194, 48, 200), 2: (224, 75, 75, 210), "marca": (142, 68, 173, 210)}
ll_de = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
xa, yb = X0 + 10 * c0, Y0 - 10 * f1
xb, ya = X0 + 10 * c1, Y0 - 10 * f0
(lon_a, lat_b), (lon_b, lat_a) = ll_de.transform(xa, yb), ll_de.transform(xb, ya)
pk = np.array(pix_ok)
resumen = {"hoy": str(HOY.date()), "geos_inicio": str(INI.date()), "miembros_geos": len(MIEMBROS),
           "zona_ha": NF * NC / 100, "limites_latlon": [[lat_b, lon_a], [lat_a, lon_b]],
           "modelo_b": {"sigma_pct": round(MB.sigma, 2), "margen_mm": round(1.5 * MB.sigma, 2),
                        "riego_supuesto": f"{A.riego} de Nataima hasta hoy (promedio de GS1, GS2 y GS3); sin riego desde hoy"},
           "horizontes": []}
enc = CLASE[:, 0] >= 0
db = DIAS_BAJA[enc]
resumen["dias_hasta_baja_sin_riego_pct"] = {
    "ya hoy": round(100 * np.mean(db == 0), 1), "1-2 días": round(100 * np.mean((db >= 1) & (db <= 2)), 1),
    "3-5 días": round(100 * np.mean((db >= 3) & (db <= 5)), 1), "6-30 días": round(100 * np.mean(db >= 6), 1),
    "no llega en 30 días": round(100 * np.mean(db < 0), 1)}
for h in (0, 7, 15, 30):
    img = np.zeros((NF * NC, 4), np.uint8)
    for c_ in (0, 1, 2):
        img[pk[CLASE[:, h] == c_]] = COLORES[c_]
    img[pk[MARCA[:, h]]] = COLORES["marca"]
    Image.fromarray(img.reshape(NF, NC, 4), "RGBA").save(OUT / f"semaforo_h{h:02d}.png")
    cult = CLASE[:, h] >= 0
    n_c = int(cult.sum())
    fila = {"h": h, "fecha": str((HOY + pd.Timedelta(days=h)).date()), "ha_en_cultivo": n_c / 100,
            "lluvia_mm_hasta_h": float(LL[:, :h].sum(1).mean()) if h else 0.0,
            "eto_mm_hasta_h": float(ETO[:, :h].sum(1).mean()) if h else 0.0}
    for c_, nom in CLASES.items():
        sel = CLASE[:, h] == c_
        dr_ = np.clip(DRP[sel, h], 0, None)  # déficit con margen: el mismo número que decide el color
        fila[nom] = {"pct": round(100 * sel.sum() / max(n_c, 1), 1), "ha": sel.sum() / 100,
                     "deficit_L_m2_medio": round(float(dr_.mean()), 2) if sel.any() else 0.0,
                     "agua_m3_zona": round(float(dr_.sum() * 0.1), 0)}  # mm × 100 m² / 1000
    fila["media"]["dias_hasta_baja_mediana"] = (int(np.median(DIAS_BAJA[(CLASE[:, h] == 1) & (DIAS_BAJA >= h)] - h))
                                                if ((CLASE[:, h] == 1) & (DIAS_BAJA >= h)).any() else None)
    fila["posible_enfermedad_o_deficiencia_nutricional"] = {"pct": round(100 * MARCA[:, h].sum() / max(n_c, 1), 1)}
    resumen["horizontes"].append(fila)
json.dump(resumen, open(OUT / "resumen.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
filas = pd.DataFrame({"pixel": IDX[pk], "das_hoy": DAS[:, 0], "dias_hasta_baja": DIAS_BAJA})
for h in (0, 7, 15, 30):
    filas[f"clase_h{h}"] = CLASE[:, h]
    filas[f"humedad_pct_h{h}"] = MU[:, h].round(2)  # humedad equivalente del déficit pintado
    filas[f"deficit_B_sin_margen_mm_h{h}"] = DRB[:, h].round(2)  # 1,5·(30 − μ0), solo hoy
    filas[f"deficit_mm_h{h}"] = DRP[:, h].round(2)  # el que decide el color (hoy con margen; luego Dr FAO)
    filas[f"S_tau20_h{h}"] = S20[:, h].round(2)
    filas[f"dr_ecuacion_mm_h{h}"] = DR_EC[:, h].round(2)  # Dr de la ecuación (entrada del ARIMAX, vista técnica)
    filas[f"ndvi_h{h}"] = NDVI[:, h].round(3)
    filas[f"ndmi_h{h}"] = NDMI[:, h].round(3)
    filas[f"marca_h{h}"] = MARCA[:, h]
filas.to_csv(OUT / "pixeles.csv.gz", index=False)
HTML = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Semáforo de humedad SIRA</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{--bg:#1b2a47;--card:#16233d;--ink:#f3f5f9;--muted:#a9b4c8;--baja:#e04b4b;--media:#f2c230;--suf:#2e9e5b;--marca:#8e44ad}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,sans-serif}
.wrap{max-width:1100px;margin:auto;padding:16px;display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:16px}
#mapa{height:520px;border-radius:12px}
.card{background:var(--card);border-radius:12px;padding:12px 16px;margin-bottom:12px;border-left:4px solid var(--c)}
.card h3{margin:0 0 4px;font-size:18px}.card p{margin:2px 0}.card small{color:var(--muted)}
.pie{grid-column:1/-1;color:var(--muted);font-size:13px}
.slider{grid-column:1/-1}.slider input{width:100%}.marcas{display:flex;justify-content:space-between;color:var(--muted)}
@media(max-width:760px){.wrap{grid-template-columns:1fr}#mapa{height:380px}}
</style></head><body><div class="wrap">
<div class="slider"><div>Horizonte de predicción: <b id="hlabel"></b></div>
<input id="h" type="range" min="0" max="3" step="1" value="0"><div class="marcas"><span>hoy</span><span>7 d</span><span>15 d</span><span>30 d</span></div></div>
<div id="mapa"></div><div id="tarjetas"></div>
<div class="pie" id="pie"></div></div>
<script>
const R = __RESUMEN__;
const map = L.map('mapa', {zoomControl:true, scrollWheelZoom:false}).fitBounds(R.limites_latlon);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {maxZoom:19, attribution:'Imágenes &copy; Esri'}).addTo(map);
let capa = null;
const f = (x, d=1) => Number(x).toLocaleString('es-CO', {maximumFractionDigits:d});
function pintar(i){
  const H = R.horizontes[i];
  document.getElementById('hlabel').textContent = H.h === 0 ? `hoy (${H.fecha})` : `${H.h} días (${H.fecha})`;
  if (capa) map.removeLayer(capa);
  capa = L.imageOverlay(`semaforo_h${String(H.h).padStart(2,'0')}.png`, R.limites_latlon, {opacity:0.85}).addTo(map);
  const b = H.baja, m = H.media, s = H.suficiente, e = H.posible_enfermedad_o_deficiencia_nutricional;
  document.getElementById('tarjetas').innerHTML = `
  <div class="card" style="--c:var(--baja)"><h3>Humedad baja · ${f(b.pct)} %</h3>
   <p>Déficit ≈ ${f(b.deficit_L_m2_medio)} L/m² (${f(b.deficit_L_m2_medio*10000,0)} L/ha): regar ya</p><small>≈ ${f(b.agua_m3_zona,0)} m³ de agua en esta zona</small></div>
  <div class="card" style="--c:var(--media)"><h3>Humedad media · ${f(m.pct)} %</h3>
   <p>Faltan ≈ ${f(m.deficit_L_m2_medio)} L/m²${m.dias_hasta_baja_mediana!=null ? `; pasa a baja en ≈ ${m.dias_hasta_baja_mediana} días sin riego` : ''}</p><small>≈ ${f(m.agua_m3_zona,0)} m³ de agua en esta zona</small></div>
  <div class="card" style="--c:var(--suf)"><h3>Humedad suficiente · ${f(s.pct)} %</h3>
   <p>${H.h === 0 ? 'Hoy el suelo tiene agua suficiente: no requiere riego' : 'No requiere riego: alcanza con la lluvia pronosticada'}</p><small>Lluvia pronosticada hasta este día: ${f(H.lluvia_mm_hasta_h,0)} mm · ETo ${f(H.eto_mm_hasta_h,0)} mm</small></div>
  ${e.pct > 0 ? `<div class="card" style="--c:var(--marca)"><h3>Posible enfermedad o deficiencia nutricional · ${f(e.pct)} %</h3>
   <p>La hoja se ve más seca de lo normal aunque el suelo tenga agua</p><small>Revisar en campo</small></div>` : ''}`;
}
document.getElementById('h').addEventListener('input', e => pintar(+e.target.value));
const DB = R.dias_hasta_baja_sin_riego_pct;
document.getElementById('tarjetas').insertAdjacentHTML('afterend', '');
const extra = document.createElement('div'); extra.className = 'pie';
extra.innerHTML = '<b>Si no se riega desde hoy, el cultivo llega a humedad baja:</b> ' + Object.entries(DB).map(([k, v]) => `${k}: ${f(v)} %`).join(' · ');
document.querySelector('.wrap').insertBefore(extra, document.getElementById('pie'));
document.getElementById('pie').textContent = `${f(R.zona_ha,0)} ha alrededor del lote de Nataima · hoy ${R.hoy} · clima GEOS-S2S (inicio ${R.geos_inicio}, ${R.miembros_geos} miembros) · riego supuesto ${R.modelo_b.riego_supuesto} · hoy: humedad corregida a Nataima (ARIMAX) con margen de ${f(R.modelo_b.margen_mm)} L/m²; desde mañana: balance FAO-56 sin riego · umbrales 30 % y 27,1 % (Martínez-Vega et al. 2025)`;
pintar(0);
setTimeout(() => { map.invalidateSize(); map.fitBounds(R.limites_latlon); }, 50);
</script></body></html>"""
(OUT / "mapa.html").write_text(HTML.replace("__RESUMEN__", json.dumps(resumen, ensure_ascii=False)), encoding="utf-8")
log("resumen: " + " | ".join(f"h{f['h']}: suficiente {f['suficiente']['pct']} % · media {f['media']['pct']} % · "
                              f"baja {f['baja']['pct']} % · marca {f['posible_enfermedad_o_deficiencia_nutricional']['pct']} %"
                              for f in resumen["horizontes"]))
