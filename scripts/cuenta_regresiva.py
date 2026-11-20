"""Semáforo como CUENTA REGRESIVA: "si deja de regar hoy, ¿en cuántos días pasa a amarillo y a rojo?"

En la app no se le pregunta nada al agricultor: se supone el lote regado hoy (lo normal en arroz) y el secado se
propaga con el clima y la planta. Aquí se prueba sobre el pasado de Nataima (GS1 + GS2 + GS3, 198 mediciones TDR):

  1. Arranque: el día del último riego registrado antes de cada medición, el suelo queda lleno (déficit Dr = 0,
     capacidad de campo). En la app ese día es "hoy".
  2. Propagación SIN riego hasta el día de la medición, con el clima observado en lugar del pronóstico
     (lluvia CHIRPS y ETo FAO-56 de NASA POWER; en la app, GEOS-S2S) y la planta observada (Kc = 1,0·NDVI + 0,45 con el
     NDVI de Sentinel-2 de la franja; en la app, el NDVI que pronostica A).
     Balance FAO-56 de la capa de raíz: Dr(t) = Dr(t−1) − lluvia + Ks·Kc·ETo, con TAW 6,7 mm, p 0,65 (RAW 4,35 mm).
  3. Color, con los umbrales del artículo (Martínez-Vega et al. 2025), sin ajustar nada a los datos:
       verde     Dr ≤ 0                 (humedad ≥ capacidad de campo, 30 %)
       amarillo  0 < Dr ≤ 4,34 mm       (bajo capacidad de campo, antes del déficit crítico)
       rojo      Dr > 4,34 mm           (déficit crítico; 4,34 mm en 150 mm de raíz = 27,1 %)

Como no se ajusta ningún parámetro con el TDR, las 198 mediciones son prueba. Se compara con:
  - "solo días desde el riego" (el mismo arranque, sin clima ni planta): ¿el clima aporta?
  - el semáforo B actual (ARIMAX con el riego registrado completo), reports/semaforo_clasificador/.
IC 95 %: bootstrap de bloques de 10 días (1000). Salida: reports/cuenta_regresiva/
"""
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports" / "cuenta_regresiva"
OUT.mkdir(parents=True, exist_ok=True)
SIEMBRAS = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}
ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}  # oeste/centro/este
FRAC = {"oeste": "frac_MIRI", "centro": "frac_AWD+MIRI", "este": "frac_CD"}
MALAS_S2 = {"2021-07-07", "2021-08-11", "2021-09-15", "2022-01-03"}
TAW, P_AG = 6.7, 0.65
RAW = P_AG * TAW
DR_ROJO = 4.34
CLASES = ["verde", "amarillo", "rojo"]

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


def secado(lluvia, kc, eto):
    """Dr desde suelo lleno (Dr = 0) sin riego; el exceso de lluvia sobre capacidad de campo drena (Dr no baja de 0)."""
    dr, out = 0.0, []
    for ll, k, e in zip(lluvia, kc, eto):
        dr = max(dr - ll, 0.0)
        ks = 1.0 if dr <= RAW else max(0.0, (TAW - dr) / ((1 - P_AG) * TAW))
        dr = min(dr + ks * k * e, TAW)
        out.append(dr)
    return np.array(out)


filas = []
for (t, trat), d in diario.groupby(["temporada", "tratamiento"]):
    d = d.sort_values("das").reset_index(drop=True)
    d["fecha"] = pd.Timestamp(SIEMBRAS[t]) + pd.to_timedelta(d.das - 1, unit="D")
    c = clima.reindex(d.fecha)
    ll, eto = c.lluvia.fillna(0).to_numpy(), c.eto.ffill().bfill().to_numpy()
    kc = np.clip(1.0 * ndvi_franja(t, trat, d.das.to_numpy()) + 0.45, 0.6, 1.3)
    riego = d.riego_mm.fillna(0).to_numpy() > 0
    for i in np.where(d.vwc_pct.notna())[0]:
        previos = np.where(riego[: i + 1])[0]
        if len(previos) == 0:
            continue  # sin riego previo en la temporada: no hay arranque
        r = previos[-1]
        # el día del riego el suelo queda lleno; se propaga desde el día siguiente hasta la medición
        dr = secado(ll[r + 1: i + 1], kc[r + 1: i + 1], eto[r + 1: i + 1])
        filas.append({"temporada": t, "tratamiento": trat, "das": int(d.das[i]), "vwc_pct": d.vwc_pct[i],
                      "dias_desde_riego": i - r, "dr_mm": float(dr[-1]) if len(dr) else 0.0,
                      "lluvia_desde_riego": float(ll[r + 1: i + 1].sum()), "eto_desde_riego": float(eto[r + 1: i + 1].sum())})
R = pd.DataFrame(filas)
R["y"] = np.select([R.vwc_pct < 27.1, R.vwc_pct < 30.0], [2, 1], 0)
R["pred"] = np.select([R.dr_mm > DR_ROJO, R.dr_mm > 0], [2, 1], 0)
R["grupo"] = R.temporada * 100 + (R.das - 1) // 10


def puntajes(s):
    """Puntaje por clase a partir de un déficit (más déficit → más rojo): verde −s, rojo s, amarillo cerca del centro."""
    return np.c_[-s, -np.abs(s - DR_ROJO / 2), s]


def metricas(y, P, pred=None):
    out = {f"auc_{c}": roc_auc_score(y == k, P[:, k]) for k, c in enumerate(CLASES) if 0 < (y == k).sum() < len(y)}
    out["auc_macro"] = float(np.mean(list(out.values())))
    if pred is not None:
        for k, c in enumerate(CLASES):
            out[f"recall_{c}"] = float(np.mean(pred[y == k] == k)) if (y == k).any() else np.nan
            out[f"precision_{c}"] = float(np.mean(y[pred == k] == k)) if (pred == k).any() else np.nan
        out["exactitud_balanceada"] = float(np.mean([out[f"recall_{c}"] for c in CLASES]))
    return out


y = R.y.to_numpy()
VAR = {
    "cuenta regresiva (riego → secado con clima y planta)": (puntajes(R.dr_mm.to_numpy()), R.pred.to_numpy()),
    "solo días desde el riego (sin clima)": (puntajes(R.dias_desde_riego.to_numpy().astype(float)), None),
}
# semáforo B actual en las mismas mediciones (sus predicciones de prueba sellada)
B = pd.read_csv(RAIZ / "reports/semaforo_clasificador/predicciones_prueba.csv")
Bm = R[["temporada", "tratamiento", "das"]].merge(B, on=["temporada", "tratamiento", "das"], how="left")
assert Bm.p_verde.notna().all()
PB = Bm[["p_verde", "p_amarillo", "p_rojo"]].to_numpy()
VAR["semáforo B actual (ARIMAX, riego registrado completo)"] = (PB, PB.argmax(1))

rng = np.random.default_rng(0)
u = np.unique(R.grupo)
ix = {g: np.where(R.grupo == g)[0] for g in u}
mues = [ii for ii in (np.concatenate([ix[g] for g in rng.choice(u, len(u), replace=True)]) for _ in range(1000))
        if len(np.unique(y[ii])) == 3]
tabla = []
for nom, (P, pred) in VAR.items():
    m = metricas(y, P, pred)
    bs = pd.DataFrame([metricas(y[ii], P[ii], None if pred is None else pred[ii]) for ii in mues])
    for k, v in m.items():
        tabla.append({"version": nom, "metrica": k, "valor": round(v, 3),
                      "IC95": f"{bs[k].quantile(.025):.2f}–{bs[k].quantile(.975):.2f}"})
T = pd.DataFrame(tabla)
T.to_csv(OUT / "metricas.csv", index=False)
R.to_csv(OUT / "predicciones.csv", index=False)
cm = pd.crosstab(pd.Series([CLASES[k] for k in y], name="real"), pd.Series([CLASES[k] for k in R.pred], name="cuenta regresiva"))
cm.to_csv(OUT / "matriz_confusion.csv")
print(f"mediciones con riego previo: {len(R)} de 198 | clases real: {np.bincount(y, minlength=3)}")
print(T.pivot(index="metrica", columns="version", values="valor").round(2).to_string())
print(T[T.metrica.isin(["auc_macro", "auc_rojo", "recall_rojo", "precision_rojo"])].to_string(index=False))
print(cm.to_string())
