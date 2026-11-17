"""SIRA · Ronda 1 del modelo B (diagnóstico): ¿qué entradas explican la humedad real del suelo en Nataima?

Corre solo, sin intervención (EC2 o local). Etapas, cada una guarda su salida en --out al terminar:
  E1 satélite : píxeles del lote + ventana de 2 km, fechas despejadas de Sentinel-2 L2A y Landsat C2 L2
                (Planetary Computer, sin cuenta).
  E2 clima    : ETo FAO-56 (NASA POWER) y lluvia (CHIRPS), acumulados de 1/3/5/10 días.
  E3 OPTRAM   : trapecio óptico (Sadeghi et al. 2017): STR = (1−SWIR2)²/(2·SWIR2) vs NDVI; bordes seco y húmedo
                ajustados con la nube de píxeles despejados de la ventana; W = posición dentro del trapecio.
  E4 tabla    : una fila por píxel·fecha del lote, con la humedad real (curva PCHIP por franja, ponderada por
                el área que el píxel cubre de cada franja) y todas las entradas.
  E5 aporte   : correlación de Spearman de cada entrada con la humedad y correlación parcial descontando
                NDMI y NDWI.
  E6 modelos  : M0 OPTRAM calibrado (lineal) vs Random Forest con distintos grupos de entradas; validación
                cronológica (GS1→GS2, GS1+GS2→GS3) y dejando una temporada afuera; métricas por franja·fecha con
                intervalos bootstrap; clases déficit (<27,1 %) / óptimo / exceso (>30 %).
Uso:  python job_b.py --out out            (corrida completa)
      python job_b.py --out out_qa --qa    (prueba corta; imprime "QA OK" si todo el camino funciona)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tarfile
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

AQUI = Path(__file__).resolve().parent
DATOS = AQUI / "datos"
LON, LAT, ELEV_M = -74.9621, 4.1895, 323.0
MEDIA_VENTANA_M = 1000
SIEMBRAS = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}
FRANJAS = ("MIRI", "AWD+MIRI", "CD")
UMBRAL_DEFICIT, UMBRAL_EXCESO = 27.1, 30.0
T0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def subir_parcial(out: Path) -> None:
    """Sube un .tar.gz con todo lo construido hasta ahora (si hay URL prefirmada en el entorno)."""
    url = os.environ.get("SIRA_URL_PARCIAL")
    if not url:
        return
    import requests
    tgz = out.parent / f"{out.name}_parcial.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(out, arcname=out.name)
    for intento in range(3):
        try:
            with open(tgz, "rb") as f:
                r = requests.put(url, data=f, timeout=120)
            if r.status_code < 300:
                log(f"parcial subido ({tgz.stat().st_size / 1e6:.1f} MB)")
                return
        except Exception as e:  # noqa: BLE001
            log(f"fallo subiendo parcial: {e}")
        time.sleep(5)


def reintentar(fn, intentos: int = 4, espera: float = 8.0):
    for k in range(intentos):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if k == intentos - 1:
                raise
            log(f"  reintento {k + 1}: {str(e)[:100]}")
            time.sleep(espera * (k + 1))


# ---------------------------------------------------------------- E1 satélite
def e1_satelite(out: Path, qa: bool) -> pd.DataFrame:
    import planetary_computer as pc
    import pystac_client
    import rasterio
    from pyproj import Transformer
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    fechas = pd.read_csv(DATOS / "fechas_despejadas_lote.csv")
    fechas = fechas[fechas.despejado & fechas.coleccion.isin(["sentinel-2-l2a", "landsat-c2-l2"])]
    if qa:  # prueba corta: 2 fechas Sentinel-2 y 1 Landsat dentro del rango de la curva de humedad
        f_ok = fechas[fechas.das.between(35, 100)]
        fechas = pd.concat([f_ok[f_ok.coleccion == "sentinel-2-l2a"].head(2),
                            f_ok[f_ok.coleccion == "landsat-c2-l2"].head(1)])
    pix = {"sentinel-2-l2a": pd.read_csv(DATOS / "pixeles_franjas_s2_10m.csv"),
           "landsat-c2-l2": pd.read_csv(DATOS / "pixeles_franjas_landsat_30m.csv")}
    cat = reintentar(lambda: pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                                       modifier=pc.sign_inplace))
    filas_lote, filas_ventana, fallidas = [], [], []
    for k, r in enumerate(fechas.itertuples(), 1):
        try:
            col = r.coleccion
            it = reintentar(lambda: cat.get_collection(col).get_item(r.id))
            if col == "sentinel-2-l2a":
                nombres = {"azul": "B02", "verde": "B03", "rojo": "B04", "nir": "B08", "nir8a": "B8A",
                           "swir1": "B11", "swir2": "B12", "qa": "SCL"}
                base = str(it.properties.get("s2:processing_baseline", "00.00"))
                offset = -1000.0 if base >= "04.00" else 0.0  # baseline 04.00+ añade +1000 a los DN
                escala = lambda dn: (dn + offset) / 10000.0  # noqa: E731
                res = 10.0
            else:
                nombres = {"azul": "blue", "verde": "green", "rojo": "red", "nir": "nir08", "nir8a": "nir08",
                           "swir1": "swir16", "swir2": "swir22", "qa": "qa_pixel"}
                escala = lambda dn: dn * 0.0000275 - 0.2  # noqa: E731
                res = 30.0
            bandas = {}
            with rasterio.open(it.assets[nombres["rojo"]].href) as ref:
                crs, trf = ref.crs, ref.transform
                x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(LON, LAT)
                w = from_bounds(x - MEDIA_VENTANA_M, y - MEDIA_VENTANA_M, x + MEDIA_VENTANA_M, y + MEDIA_VENTANA_M,
                                transform=trf).round_offsets().round_lengths()
                wtr = ref.window_transform(w)
                shape = (int(w.height), int(w.width))
            for clave, banda in nombres.items():
                def leer(banda=banda):
                    with rasterio.open(it.assets[banda].href) as src:
                        win = from_bounds(*rasterio.windows.bounds(w, trf), transform=src.transform)
                        return src.read(1, window=win, out_shape=shape, boundless=True, fill_value=0,
                                        resampling=Resampling.nearest)
                bandas[clave] = reintentar(leer).astype(float)
            q = bandas.pop("qa").astype(np.int64)
            if col == "sentinel-2-l2a":
                limpio = np.isin(q, [4, 5, 6])
            else:
                malo = q & 0b11111
                limpio = (malo == 0) & ((q & (1 << 6)) | (q & (1 << 7)) > 0)
            R = {c: escala(v) for c, v in bandas.items()}
            validos = limpio & (R["rojo"] > 0) & (R["nir"] > 0) & (R["swir2"] > 0)
            ind = indices(R)
            # píxeles del lote (tabla de fracciones por franja, centros en el mismo CRS)
            p = pix[col]
            inv = ~wtr
            cc, rr = inv * (p.x_centro.to_numpy(), p.y_centro.to_numpy())
            cc, rr = np.floor(cc).astype(int), np.floor(rr).astype(int)
            dentro = (rr >= 0) & (rr < shape[0]) & (cc >= 0) & (cc < shape[1])
            for i in np.where(dentro)[0]:
                a, b = rr[i], cc[i]
                if not validos[a, b]:
                    continue
                fila = {"sensor": "S2" if col.startswith("sentinel") else "L",
                        "satelite": it.properties.get("platform", ""), "fecha": str(r.fecha),
                        "pixel_id": p.pixel_id.iloc[i], **{f"frac_{f}": p[f"frac_{f}"].iloc[i] for f in FRANJAS},
                        "frac_lote": p.frac_lote.iloc[i], **{n: float(v[a, b]) for n, v in ind.items()}}
                filas_lote.append(fila)
            # nube de puntos de la ventana para el trapecio de OPTRAM (submuestreo fijo)
            sel = np.where(validos.ravel())[0]
            rng = np.random.default_rng(k)
            sel = rng.choice(sel, size=min(len(sel), 6000), replace=False) if len(sel) else sel
            filas_ventana.append(pd.DataFrame({"sensor": "S2" if col.startswith("sentinel") else "L",
                                               "fecha": str(r.fecha),
                                               "ndvi": ind["ndvi"].ravel()[sel], "str": ind["str"].ravel()[sel]}))
            log(f"E1 {k}/{len(fechas)} {col} {r.fecha}: {int(validos.sum())} píxeles válidos en la ventana")
        except Exception as e:  # noqa: BLE001  (una imagen dañada o con acceso denegado no detiene todo)
            import re
            msg = re.sub(r"\?\S*", "?<token omitido>", str(e))  # no guardar tokens de acceso en los resultados
            fallidas.append({"coleccion": r.coleccion, "id": r.id, "fecha": str(r.fecha), "error": msg[:300]})
            log(f"E1 {k}/{len(fechas)} {r.coleccion} {r.fecha}: OMITIDA ({str(e)[:80]})")
        if k % 10 == 0:  # avance guardado y subido cada 10 imágenes
            pd.DataFrame(filas_lote).to_csv(out / "e1_pixeles_lote.csv", index=False)
            pd.DataFrame(fallidas).to_csv(out / "e1_imagenes_omitidas.csv", index=False)
            subir_parcial(out)
    lote = pd.DataFrame(filas_lote)
    vent = pd.concat(filas_ventana, ignore_index=True) if filas_ventana else pd.DataFrame()
    lote.to_csv(out / "e1_pixeles_lote.csv", index=False)
    vent.to_csv(out / "e1_ventana_optram.csv.gz", index=False)
    pd.DataFrame(fallidas, columns=["coleccion", "id", "fecha", "error"]).to_csv(out / "e1_imagenes_omitidas.csv", index=False)
    if len(fallidas) > 0.25 * len(fechas):
        raise RuntimeError(f"demasiadas imágenes omitidas: {len(fallidas)} de {len(fechas)}")
    log(f"E1 listo: {len(lote)} filas píxel·fecha del lote, {len(vent)} puntos de ventana; {len(fallidas)} imágenes omitidas")
    return lote, vent


def indices(R: dict) -> dict:
    eps = 1e-6
    sw2 = np.clip(R["swir2"], 1e-4, 1.0)
    return {
        "ndmi": (R["nir8a"] - R["swir1"]) / (R["nir8a"] + R["swir1"] + eps),
        "ndwi": (R["verde"] - R["nir"]) / (R["verde"] + R["nir"] + eps),
        "ndvi": (R["nir"] - R["rojo"]) / (R["nir"] + R["rojo"] + eps),
        "mndwi": (R["verde"] - R["swir1"]) / (R["verde"] + R["swir1"] + eps),
        "str": (1 - sw2) ** 2 / (2 * sw2),
        "swir1": R["swir1"], "swir2": R["swir2"], "nir": R["nir"],
    }


# ---------------------------------------------------------------- E2 clima
def eto_fao56(tmax, tmin, rh, u2, rs, doy, lat_deg=LAT, z=ELEV_M):
    tmean = (tmax + tmin) / 2
    P = 101.3 * ((293 - 0.0065 * z) / 293) ** 5.26
    gamma = 0.000665 * P
    es_f = lambda t: 0.6108 * np.exp(17.27 * t / (t + 237.3))  # noqa: E731
    es = (es_f(tmax) + es_f(tmin)) / 2
    ea = rh / 100 * es
    delta = 4098 * es_f(tmean) / (tmean + 237.3) ** 2
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * np.cos(2 * np.pi * doy / 365)
    dec = 0.409 * np.sin(2 * np.pi * doy / 365 - 1.39)
    ws = np.arccos(-np.tan(phi) * np.tan(dec))
    Ra = 24 * 60 / np.pi * 0.0820 * dr * (ws * np.sin(phi) * np.sin(dec) + np.cos(phi) * np.cos(dec) * np.sin(ws))
    Rso = (0.75 + 2e-5 * z) * Ra
    Rns = 0.77 * rs
    sigma = 4.903e-9
    Rnl = sigma * (((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2) * (0.34 - 0.14 * np.sqrt(ea)) * \
        (1.35 * np.clip(rs / Rso, 0.25, 1.0) - 0.35)
    Rn = Rns - Rnl
    return (0.408 * delta * Rn + gamma * 900 / (tmean + 273) * u2 * (es - ea)) / (delta + gamma * (1 + 0.34 * u2))


def e2_clima(out: Path) -> pd.DataFrame:
    p = json.loads((DATOS / "power_eto.json").read_text())["properties"]["parameter"]
    df = pd.DataFrame({k: pd.Series(v) for k, v in p.items()})
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.replace(-999.0, np.nan).interpolate(limit=3)
    df["eto"] = eto_fao56(df.T2M_MAX, df.T2M_MIN, df.RH2M, df.WS2M, df.ALLSKY_SFC_SW_DWN, df.index.dayofyear)
    ch = json.loads((DATOS / "chirps_climateserv.json").read_text())["data"]
    df["lluvia"] = pd.Series({pd.Timestamp(x["year"], x["month"], x["day"]): x["value"]["avg"] for x in ch})
    for n in (1, 3, 5, 10):
        df[f"lluvia_{n}d"] = df.lluvia.rolling(n, min_periods=1).sum()
        df[f"eto_{n}d"] = df.eto.rolling(n, min_periods=1).sum()
    df["balance_5d"] = df.lluvia_5d - df.eto_5d
    df["balance_10d"] = df.lluvia_10d - df.eto_10d
    df.index.name = "fecha"
    df.to_csv(out / "e2_clima_dia.csv")
    log(f"E2 listo: ETo FAO-56 mediana {df.eto.median():.2f} mm/d (p5 {df.eto.quantile(.05):.2f}, p95 {df.eto.quantile(.95):.2f})")
    return df


# ---------------------------------------------------------------- E3 OPTRAM
def bordes_optram(v: pd.DataFrame) -> dict:
    v = v[(v.ndvi > 0.05) & (v.ndvi < 0.95) & np.isfinite(v.str)]
    bins = np.arange(0.05, 0.951, 0.025)
    v = v.assign(b=pd.cut(v.ndvi, bins))
    g = v.groupby("b", observed=True).agg(n=("str", "size"), ndvi=("ndvi", "median"),
                                          seco=("str", lambda s: s.quantile(0.02)),
                                          humedo=("str", lambda s: s.quantile(0.98)))
    g = g[g.n >= 50]
    sd, id_ = np.polyfit(g.ndvi, g.seco, 1)
    sw, iw = np.polyfit(g.ndvi, g.humedo, 1)
    return {"i_seco": float(id_), "s_seco": float(sd), "i_humedo": float(iw), "s_humedo": float(sw), "n_bins": int(len(g))}


def e3_optram(out: Path, lote: pd.DataFrame, vent: pd.DataFrame) -> pd.DataFrame:
    params = {s: bordes_optram(vent[vent.sensor == s]) for s in sorted(vent.sensor.unique())}
    (out / "e3_optram_bordes.json").write_text(json.dumps(params, indent=1))
    w = np.full(len(lote), np.nan)
    for s, p in params.items():
        m = (lote.sensor == s).to_numpy()
        seco = p["i_seco"] + p["s_seco"] * lote.ndvi[m]
        hum = p["i_humedo"] + p["s_humedo"] * lote.ndvi[m]
        w[m] = np.clip((lote.str[m] - seco) / (hum - seco), -0.2, 1.2)
    lote = lote.assign(optram_w=w)
    log(f"E3 listo: bordes OPTRAM {json.dumps(params)}")
    return lote


# ---------------------------------------------------------------- E4 tabla
def e4_tabla(out: Path, lote: pd.DataFrame, clima: pd.DataFrame) -> pd.DataFrame:
    hum = pd.read_csv(DATOS / "humedad_continua.csv")
    riego = pd.read_csv(DATOS / "nataima_diario.csv")[["temporada", "das", "tratamiento", "riego_mm"]]
    filas = []
    for r in lote.itertuples():
        f = pd.Timestamp(r.fecha)
        temp, das = None, None
        for t, s in SIEMBRAS.items():
            d = (f - pd.Timestamp(s)).days + 1
            if 1 <= d <= 130:
                temp, das = t, d
        if temp is None:
            continue
        pesos = {fr: lote.loc[r.Index, f"frac_{fr}"] for fr in FRANJAS}
        tot = sum(pesos.values())
        if tot <= 0:
            continue
        vals, dists, ok = [], [], True
        for fr, p in pesos.items():
            if p <= 0:
                continue
            h = hum[(hum.temporada == temp) & (hum.tratamiento == fr) & (hum.das == das)]
            if h.empty:
                ok = False
                break
            vals.append(p * h.vwc_pct.iloc[0])
            dists.append(int(h.dist_medicion_dias.iloc[0]))
        if not ok:
            continue
        rg = riego[(riego.temporada == temp) & (riego.das.between(das - 9, das))]
        riego_pond = {n: sum(pesos[fr] * rg[(rg.tratamiento == fr) & (rg.das > das - n)].riego_mm.sum()
                             for fr in FRANJAS) / tot for n in (3, 5, 10)}
        franja_dom = max(pesos, key=pesos.get)
        c = clima.loc[f]
        filas.append({**lote.loc[r.Index].to_dict(), "temporada": temp, "das": das,
                      "vwc_pct": sum(vals) / tot, "dist_medicion_dias": max(dists),
                      "franja_dominante": franja_dom, "pureza": pesos[franja_dom],
                      **{k: float(c[k]) for k in ("lluvia_1d", "lluvia_3d", "lluvia_5d", "lluvia_10d",
                                                   "eto_1d", "eto_3d", "eto_5d", "eto_10d", "balance_5d", "balance_10d")},
                      **{f"riego_{n}d": v for n, v in riego_pond.items()}})
    t = pd.DataFrame(filas)
    t["clase"] = np.select([t.vwc_pct < UMBRAL_DEFICIT, t.vwc_pct > UMBRAL_EXCESO], ["deficit", "exceso"], "optimo")
    t.to_csv(out / "e4_tabla_b.csv", index=False)
    log(f"E4 listo: {len(t)} filas; por temporada {t.groupby('temporada').size().to_dict()}; "
        f"fechas {t.fecha.nunique()}; con medición a ≤2 días {int((t.dist_medicion_dias <= 2).sum())}")
    return t


# ---------------------------------------------------------------- E5 aporte
ENTRADAS = {
    "indices": ["ndmi", "ndwi", "ndvi", "mndwi"],
    "optram": ["optram_w"],
    "clima": ["lluvia_1d", "lluvia_3d", "lluvia_5d", "lluvia_10d", "eto_5d", "eto_10d", "balance_5d", "balance_10d"],
    "etapa": ["das"],
    "riego": ["riego_3d", "riego_5d", "riego_10d"],
}


def e5_aporte(out: Path, t: pd.DataFrame) -> pd.DataFrame:
    from scipy.stats import rankdata, spearmanr
    d = t[t.dist_medicion_dias <= 2]
    filas = []
    ctrl = np.column_stack([rankdata(d.ndmi), rankdata(d.ndwi), np.ones(len(d))])
    ry = rankdata(d.vwc_pct)
    res_y = ry - ctrl @ np.linalg.lstsq(ctrl, ry, rcond=None)[0]
    for grupo, cols in ENTRADAS.items():
        for c in cols:
            rho, p = spearmanr(d[c], d.vwc_pct, nan_policy="omit")
            rx = rankdata(d[c])
            res_x = rx - ctrl @ np.linalg.lstsq(ctrl, rx, rcond=None)[0]
            parcial = np.corrcoef(res_x, res_y)[0, 1] if c not in ("ndmi", "ndwi") else np.nan
            filas.append({"grupo": grupo, "entrada": c, "spearman": round(float(rho), 3), "p_valor": float(p),
                          "parcial_sin_ndmi_ndwi": round(float(parcial), 3) if parcial == parcial else np.nan})
    a = pd.DataFrame(filas)
    a.to_csv(out / "e5_aporte_entradas.csv", index=False)
    log("E5 listo:\n" + a.to_string(index=False))
    return a


# ---------------------------------------------------------------- E6 modelos
def metricas(y, yhat) -> dict:
    from sklearn.metrics import f1_score
    clase = lambda v: np.select([v < UMBRAL_DEFICIT, v > UMBRAL_EXCESO], [0, 2], 1)  # noqa: E731
    return {"rmse": float(np.sqrt(np.mean((y - yhat) ** 2))), "mae": float(np.mean(np.abs(y - yhat))),
            "f1_macro": float(f1_score(clase(y), clase(yhat), average="macro", labels=[0, 1, 2], zero_division=0)),
            "n": int(len(y))}


def e6_modelos(out: Path, t: pd.DataFrame, qa: bool) -> pd.DataFrame:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.linear_model import LinearRegression

    modelos = {
        "M0_optram_lineal": None,
        "M1_indices_clima": ENTRADAS["indices"] + ENTRADAS["clima"],
        "M2_+optram": ENTRADAS["indices"] + ENTRADAS["clima"] + ENTRADAS["optram"],
        "M3_+etapa": ENTRADAS["indices"] + ENTRADAS["clima"] + ENTRADAS["optram"] + ENTRADAS["etapa"],
        "M4_+riego": sum(ENTRADAS.values(), []),
    }
    esquemas = {"cronologico": [([1], 2), ([1, 2], 3)],
                "una_afuera": [([2, 3], 1), ([1, 3], 2), ([1, 2], 3)]}
    t = t.dropna(subset=ENTRADAS["indices"] + ENTRADAS["optram"])
    preds, filas = [], []
    for esquema, pliegues in esquemas.items():
        for entren, prueba in pliegues:
            tr, te = t[t.temporada.isin(entren)], t[t.temporada == prueba]
            if len(tr) < 10 or len(te) < 3:
                continue
            for nombre, cols in modelos.items():
                if cols is None:
                    lr = LinearRegression().fit(tr[["optram_w"]], tr.vwc_pct)
                    yhat = lr.predict(te[["optram_w"]])
                else:
                    rf = RandomForestRegressor(n_estimators=100 if qa else 500, min_samples_leaf=5,
                                               random_state=0, n_jobs=-1).fit(tr[cols], tr.vwc_pct)
                    yhat = rf.predict(te[cols])
                preds.append(te[["temporada", "fecha", "das", "sensor", "pixel_id", "franja_dominante", "pureza",
                                 "dist_medicion_dias", "vwc_pct"]].assign(esquema=esquema, modelo=nombre,
                                                                           entrenado_con=str(entren), pred=yhat))
    P = pd.concat(preds, ignore_index=True)
    P.to_csv(out / "e6_predicciones.csv.gz", index=False)
    # evaluación principal: franja·fecha con píxeles Sentinel-2 casi puros (≥90 % en una franja) y medición a ≤2 días
    ev = P[(P.sensor == "S2") & (P.pureza >= 0.9) & (P.dist_medicion_dias <= 2)]
    agg = ev.groupby(["esquema", "modelo", "temporada", "fecha", "franja_dominante"], as_index=False).agg(
        y=("vwc_pct", "mean"), yhat=("pred", "mean"))
    rng = np.random.default_rng(0)
    nboot = 50 if qa else 1000
    for (esquema, modelo), g in agg.groupby(["esquema", "modelo"]):
        m = metricas(g.y.to_numpy(), g.yhat.to_numpy())
        claves = g[["temporada", "fecha"]].drop_duplicates().to_numpy()
        bs = []
        for _ in range(nboot):  # bootstrap por bloques: se remuestrean fechas completas
            idx = rng.integers(0, len(claves), len(claves))
            sel = pd.concat([g[(g.temporada == claves[i][0]) & (g.fecha == claves[i][1])] for i in idx])
            bs.append(np.sqrt(np.mean((sel.y - sel.yhat) ** 2)))
        filas.append({"esquema": esquema, "modelo": modelo, **{k: round(v, 3) if isinstance(v, float) else v for k, v in m.items()},
                      "rmse_ic95_inf": round(float(np.percentile(bs, 2.5)), 3),
                      "rmse_ic95_sup": round(float(np.percentile(bs, 97.5)), 3)})
    # también: píxeles Landsat (etiqueta ponderada), medición a ≤2 días
    evl = P[(P.sensor == "L") & (P.dist_medicion_dias <= 2)]
    for (esquema, modelo), g in evl.groupby(["esquema", "modelo"]):
        m = metricas(g.vwc_pct.to_numpy(), g.pred.to_numpy())
        filas.append({"esquema": esquema + "_landsat_pixel", "modelo": modelo,
                      **{k: round(v, 3) if isinstance(v, float) else v for k, v in m.items()}})
    R = pd.DataFrame(filas).sort_values(["esquema", "rmse"])
    R.to_csv(out / "e6_resultados_modelos.csv", index=False)
    log("E6 listo:\n" + R.to_string(index=False))
    return R


# ---------------------------------------------------------------- principal
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--qa", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    estado = {"etapa": "inicio", "qa": a.qa}
    try:
        lote, vent = e1_satelite(out, a.qa); estado["etapa"] = "E1"; subir_parcial(out)
        clima = e2_clima(out); estado["etapa"] = "E2"
        lote = e3_optram(out, lote, vent); estado["etapa"] = "E3"; subir_parcial(out)
        t = e4_tabla(out, lote, clima); estado["etapa"] = "E4"; subir_parcial(out)
        if len(t) == 0:
            raise RuntimeError("tabla vacía")
        if a.qa:
            # en QA puede no haber filas suficientes de las 3 temporadas: se duplica para probar el camino completo
            t = pd.concat([t.assign(temporada=k) for k in (1, 2, 3)], ignore_index=True)
            t["dist_medicion_dias"] = 0
        e5_aporte(out, t); estado["etapa"] = "E5"
        e6_modelos(out, t, a.qa); estado["etapa"] = "E6"
        estado["ok"] = True
    except Exception:  # noqa: BLE001
        estado["ok"] = False
        estado["error"] = traceback.format_exc()
        log("ERROR:\n" + estado["error"])
    estado["segundos"] = round(time.time() - T0, 1)
    (out / "estado.json").write_text(json.dumps(estado, indent=1))
    subir_parcial(out)
    if estado["ok"] and a.qa:
        print("QA OK", flush=True)
    return 0 if estado["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
