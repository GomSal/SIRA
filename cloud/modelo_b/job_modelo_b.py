"""SIRA · Modelo B (Corrector satélite → dron): los 8 pasos de la metodología, de punta a punta.

Respuesta: el índice que midió el dron (Fig. 7 de Martínez-Vega et al. 2025): NDVI, NDRE u OSAVI, en cada franja
(Sentinel-2) o píxel de 30 m (Landsat) y fecha. Un corrector por índice.
Entradas candidatas: índices del satélite (absolutos y relativos al lote), Prithvi-EO-2.0-300M-TL congelado (PCA),
clima (CHIRPS + ETo FAO-56 de NASA POWER), riego del tratamiento (Fig. 4) y días desde la siembra.

Etapas (cada una con su tope de tiempo en el user-data):
  --qa                : pasos 1–8 con repeticiones reducidas + Prithvi en 1 fecha; mide tiempos y proyecta el total.
  --etapa prithvi     : paso 2, parte Prithvi (bajar HLS de Planetary Computer, control de calidad, resúmenes).
  --etapa modelo      : pasos 1–8 completos (usa prithvi_embeddings.csv de la etapa anterior).
Salidas en --out. Estado final en <out>/estado_job.txt (OK, QA_OK, ERROR_DATOS, ERROR_NO_REGRESION, ERROR_PRITHVI,
ERROR_NUMERICO, ERROR_PASO<n>, ESTIMADO_EXCEDE). Semillas fijas: misma entrada → misma salida.
"""
from __future__ import annotations

import os
import sys

_ETAPA = sys.argv[sys.argv.index("--etapa") + 1] if "--etapa" in sys.argv[:-1] else ""
if _ETAPA != "prithvi":  # un hilo por proceso; el paralelismo va por procesos (torch fija sus hilos aparte)
    for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import platform  # noqa: E402
import tarfile  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
import warnings  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.interpolate import PchipInterpolator  # noqa: E402

warnings.filterwarnings("ignore")
AQUI = Path(__file__).resolve().parent
DATOS = AQUI / "datos"
SEMILLA = 20261117
T0 = time.time()

# ---------------------------------------------------------------- constantes del proyecto (de corrector_dron_v2.py)
LON, LAT = -74.9621, 4.1895
SIEMBRAS = {1: pd.Timestamp("2021-05-27"), 2: pd.Timestamp("2021-12-04"), 3: pd.Timestamp("2022-07-09")}
FIS = {"MIRI": "oeste", "AWD+MIRI": "centro", "CD": "este"}  # nombre GS1 de la franja → posición física
FRAC = {"oeste": "frac_MIRI", "centro": "frac_AWD+MIRI", "este": "frac_CD"}
ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}  # oeste/centro/este
TRAT = ("CD", "MIRI", "AWD+MIRI")
DIA_ETAPA = {"vegetativa": 30, "reproductiva": 60, "maduracion": 100}
# error estándar del dron por temporada y etapa (Tabla 4): (NDVI, NDRE). OSAVI no está en la Tabla 4: se usa el de NDVI.
EE = {(1, "vegetativa"): (.008, .008), (1, "reproductiva"): (.001, .004), (1, "maduracion"): (.002, .004),
      (2, "vegetativa"): (.007, .009), (2, "reproductiva"): (.001, .004), (2, "maduracion"): (.004, .006),
      (3, "vegetativa"): (.014, .006), (3, "reproductiva"): (.005, .011), (3, "maduracion"): (.005, .006)}
VENTANA = (25, 115)
INDICES = ("NDVI", "NDRE", "OSAVI")
CONTRATO = {"NDVI": (23, 141), "NDRE": (14, 42), "OSAVI": (23, 141)}  # fechas, pares (§3 del handover)
IDX_SAT = ["ndvi", "ndre", "osavi", "ndwi", "ndmi", "mndwi", "str"]
CLIMA = ["lluvia_1d", "lluvia_3d", "lluvia_5d", "lluvia_10d", "eto", "eto_5d", "eto_10d", "balance_5d", "balance_10d",
         "T2M_MAX", "ALLSKY_SFC_SW_DWN"]
RIEGO = ["riego_acum", "riego_5d", "riego_10d"]
PROHIBIDAS = {"tratamiento", "temporada", "franja", "franja_fisica", "unidad", "pixel_id", "PRECTOTCORR", "posicion"}
NPRI = 1024
PCOLS = [f"p{i}" for i in range(NPRI)]
KS = (3, 5, 10, 20)
VARIANTES = ("B1", "B1_sin_riego", "B2", "B3")
RANGO_VAR = {"B1": 0, "B2": 1, "B3": 2}
FAMILIAS = ("lineal", "huber", "ridge", "lasso", "splines", "rf", "xgb")
FAM_B3 = ("ridge", "mlp")
RANGO_FAM = {f: i for i, f in enumerate(FAMILIAS)} | {"mlp": 7}
PRITHVI_REPO = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL"
PRITHVI_REV = "63adbd39c271da4c42f447e69b1a7c91a338cdc9"  # commit fijo del repositorio oficial en Hugging Face
PRITHVI_PESOS = "Prithvi_EO_V2_300M_TL.pt"
HLS_BANDAS = {"hls2-s30": ["B02", "B03", "B04", "B8A", "B11", "B12"], "hls2-l30": ["B02", "B03", "B04", "B05", "B06", "B07"]}
LADO, PARCHE, CELDA_LOTE = 224, 16, 7  # recorte 224×224 px de 30 m; el lote cae en el bloque (7, 7) de 16×16 px
PIX_LOTE = CELDA_LOTE * PARCHE + 7  # el píxel del centro del lote queda en la fila/columna 119 (bloque 112–127)


class ErrorEstado(RuntimeError):
    def __init__(self, estado: str, msg: str):
        super().__init__(msg)
        self.estado = estado


# ---------------------------------------------------------------- utilidades de corrida
def log(msg: str) -> None:
    print(f"[{time.time() - T0:7.1f}s] {msg}", flush=True)


def latido(que: str = "") -> None:
    p = os.environ.get("SIRA_LATIDO")
    if p:
        Path(p).write_text(f"{time.time():.0f} {que}\n", encoding="utf-8")


def subir(url_env: str, archivo: Path) -> None:
    url = os.environ.get(url_env)
    if not url or not archivo.exists():
        return
    import requests
    for _ in range(3):
        try:
            with open(archivo, "rb") as f:
                if requests.put(url, data=f, timeout=180).status_code < 300:
                    return
        except Exception as e:  # noqa: BLE001
            log(f"fallo subiendo {archivo.name}: {str(e)[:80]}")
        time.sleep(5)


def subir_parcial(out: Path) -> None:
    if not os.environ.get("SIRA_URL_PARCIAL"):
        return
    tgz = out.parent / f"{out.name}_parcial.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(out, arcname=out.name, filter=lambda ti: None if ti.name.endswith(".npz") else ti)
    subir("SIRA_URL_PARCIAL", tgz)
    log(f"parcial subido ({tgz.stat().st_size / 1e6:.1f} MB)")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def temporada_de(f: pd.Timestamp):
    for t, d0 in SIEMBRAS.items():
        das = (f - d0).days + 1
        if VENTANA[0] <= das <= VENTANA[1]:
            return t, das
    return None, None


# ================================================================ PASO 2 (parte tabular): datos, calidad, tablas
def cargar_base() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Filas S2 (franja × fecha) y Landsat (píxel × fecha) en la ventana 25–115, con índices, pesos por tratamiento y
    la marca de control de calidad. Misma lógica que scripts/corrector_dron_v2.py."""
    pix = pd.read_csv(DATOS / "pixeles_lote_s2_landsat.csv", parse_dates=["fecha"])
    pix["rojo"] = pix.nir * (1 - pix.ndvi) / (1 + pix.ndvi)  # se despeja del NDVI (B08/nir08 y rojo, igual que job_b)
    pix["osavi"] = (pix.nir - pix.rojo) / (pix.nir + pix.rojo + 0.16)
    # ---- Sentinel-2 por franja (píxeles ≥ 90 % dentro)
    s2 = pd.read_csv(DATOS / "correlacion_ndvi_ndre_datos.csv", parse_dates=["fecha"])
    s2 = s2[s2.das.between(*VENTANA)][["temporada", "fecha", "das", "franja", "ndvi", "ndre"]].copy()
    ps = pix[pix.sensor == "S2"]
    extra = []
    for r in s2.itertuples():
        g = ps[(ps.fecha == r.fecha) & (ps[FRAC[FIS[r.franja]]] >= 0.9)]
        extra.append({**{c: g[c].mean() for c in ("ndwi", "ndmi", "mndwi", "str", "osavi")},
                      "ndvi_pix": g.ndvi.mean(), "n_pix": len(g)})
    s2 = pd.concat([s2.reset_index(drop=True), pd.DataFrame(extra)], axis=1)
    s2["franja_fisica"] = s2.franja.map(FIS)
    s2["tratamiento"] = [dict(zip(("oeste", "centro", "este"), ROT[t]))[f] for t, f in zip(s2.temporada, s2.franja_fisica)]
    for t in TRAT:
        s2[f"w_{t}"] = (s2.tratamiento == t).astype(float)
    s2["sensor"], s2["satelite"], s2["unidad"] = "S2", "sentinel-2", s2.franja_fisica
    # ---- Landsat por píxel (≥ 50 % en el lote, referencia = mezcla ponderada por área)
    L = pix[(pix.sensor == "L") & (pix.frac_lote >= 0.5)].copy()
    td = L.fecha.apply(temporada_de)
    L["temporada"] = [a for a, _ in td]
    L["das"] = [b for _, b in td]
    L = L.dropna(subset=["temporada"]).copy()
    L["temporada"], L["das"] = L.temporada.astype(int), L.das.astype(int)
    for t in TRAT:
        L[f"w_{t}"] = 0.0
    for i, r in L.iterrows():
        for pos, trat in zip(("oeste", "centro", "este"), ROT[r.temporada]):
            L.loc[i, f"w_{trat}"] = r[FRAC[pos]] / r.frac_lote
    L["ndre"], L["unidad"], L["franja_fisica"], L["tratamiento"] = np.nan, L.pixel_id, "", "mezcla"
    cols = ["temporada", "fecha", "das", "sensor", "satelite", "unidad", "franja_fisica", "tratamiento",
            *IDX_SAT, *[f"w_{t}" for t in TRAT]]
    D = pd.concat([s2[cols + ["ndvi_pix"]], L[cols]], ignore_index=True)
    # ---- control de calidad (serie conjunta S2 + Landsat; azul alto en S2)
    lote = D.groupby(["temporada", "fecha", "sensor"], as_index=False).ndvi.mean().sort_values(["temporada", "fecha"],
                                                                                              kind="stable")
    lote["prev"] = lote.groupby("temporada").ndvi.shift(1)
    lote["sig"] = lote.groupby("temporada").ndvi.shift(-1)
    lote["caida"] = (lote.prev - lote.ndvi > 0.10) & (lote.sig - lote.ndvi > 0.10)
    rev = pd.concat([pd.read_csv(DATOS / f"revision_gs{t}.csv") for t in (1, 2, 3)])
    rev["fecha"] = pd.to_datetime(rev.fecha)
    azul = rev[(rev.azul_lote >= 0.075) & rev.das.between(*VENTANA)].set_index("fecha").azul_lote.to_dict()
    motivos = []
    for r in lote.itertuples():
        m = []
        if r.caida:
            m.append(f"caída de NDVI del lote ({r.ndvi:.3f} frente a {r.prev:.3f} y {r.sig:.3f})")
        if r.sensor == "S2" and r.fecha in azul:
            m.append(f"azul del lote {azul[r.fecha]:.3f} ≥ 0,075")
        motivos.append("; ".join(m) if m else "ok")
    lote["motivo"] = motivos
    calidad = lote.rename(columns={"ndvi": "ndvi_lote", "prev": "ndvi_anterior", "sig": "ndvi_siguiente"})
    calidad["das"] = [temporada_de(f)[1] for f in calidad.fecha]
    calidad = calidad[["temporada", "fecha", "das", "sensor", "ndvi_lote", "ndvi_anterior", "ndvi_siguiente", "motivo"]]
    malas = set(zip(calidad[calidad.motivo != "ok"].fecha, calidad[calidad.motivo != "ok"].sensor))
    D["ok"] = [(f, s) not in malas for f, s in zip(D.fecha, D.sensor)]
    return D, calidad


def agregar_clima_riego(D: pd.DataFrame) -> pd.DataFrame:
    cl = pd.read_csv(DATOS / "clima_dia.csv", parse_dates=["fecha"]).set_index("fecha")
    D = D.copy()
    for c in CLIMA:
        D[c] = cl.loc[D.fecha, c].to_numpy()
    rg = pd.read_csv(DATOS / "nataima_diario.csv")
    et = rg.drop_duplicates(["temporada", "das"]).set_index(["temporada", "das"]).etapa
    D["etapa"] = [et.get((t, d), "") for t, d in zip(D.temporada, D.das)]
    tab = {k: g.set_index("das").riego_mm.sort_index() for k, g in rg.groupby(["temporada", "tratamiento"])}
    for c, ventana in (("riego_acum", None), ("riego_5d", 5), ("riego_10d", 10)):
        val = np.zeros(len(D))
        for t in TRAT:
            r = np.array([tab[(tt, t)][(tab[(tt, t)].index <= d) & ((tab[(tt, t)].index > d - ventana) if ventana else True)].sum()
                          for tt, d in zip(D.temporada, D.das)])
            val += D[f"w_{t}"].to_numpy() * r
        D[c] = val
    D["sensor_L"] = (D.sensor == "L").astype(float)
    # índices relativos al lote: índice − media del lote ese día con ese sensor
    for c in IDX_SAT:
        D[f"{c}_rel"] = D[c] - D.groupby(["temporada", "fecha", "sensor"])[c].transform("mean")
    D["fid"] = D.fecha.dt.strftime("%Y-%m-%d")
    return D


def curvas_dron(rng=None) -> dict:
    """PCHIP por temporada × tratamiento × índice, solo para emparejar fechas. Con rng: + ruido del dron (EE × √3)."""
    dron = pd.read_csv(DATOS / "dron_indices.csv")
    curvas = {}
    for (t, trat, ind), g in dron.groupby(["temporada", "tratamiento", "indice"]):
        g = g.assign(dia=g.etapa.map(DIA_ETAPA)).sort_values("dia")
        v = g.valor.to_numpy(dtype=float)
        if rng is not None:
            k = 1 if ind == "NDRE" else 0
            v = v + rng.normal(0, [EE[(t, e)][k] * np.sqrt(3) for e in g.etapa])
        curvas[(t, trat, ind)] = PchipInterpolator(g.dia, v, extrapolate=True)
    return curvas


def respuesta(D: pd.DataFrame, ind: str, curvas: dict) -> np.ndarray:
    y = np.zeros(len(D))
    for t in TRAT:
        y += D[f"w_{t}"].to_numpy() * np.array([float(curvas[(tt, t, ind)](d)) for tt, d in zip(D.temporada, D.das)])
    return y


def agregar_prithvi(D: pd.DataFrame, emb: pd.DataFrame | None) -> pd.DataFrame:
    """Cada fila S2 o Landsat 8/9 recibe el vector Prithvi de su misma fecha HLS (±1 día; mismo sensor primero)."""
    D = D.copy()
    P = np.full((len(D), NPRI), np.nan)
    D["hls_item"] = ""
    if emb is not None and len(emb):
        emb = emb.copy()
        emb["fecha"] = pd.to_datetime(emb.fecha)
        E = emb[PCOLS].to_numpy()
        for i, r in enumerate(D.itertuples()):
            if r.satelite == "landsat-7":
                continue
            prefer = "hls2-s30" if r.sensor == "S2" else "hls2-l30"
            dd = (emb.fecha - r.fecha).abs().dt.days
            ok = np.where((dd <= 1).to_numpy())[0]
            if len(ok) == 0:
                continue
            orden = sorted(ok, key=lambda j: (emb.coleccion.iloc[j] != prefer, dd.iloc[j]))
            P[i] = E[orden[0]]
            D.iloc[i, D.columns.get_loc("hls_item")] = emb.item_id.iloc[orden[0]]
    D = pd.concat([D.reset_index(drop=True), pd.DataFrame(P, columns=PCOLS)], axis=1)
    D["tiene_hls"] = ~np.isnan(P[:, 0])
    return D


def candidatas(ind: str, T: pd.DataFrame, riego: bool = True) -> list[str]:
    idx = [c for c in IDX_SAT if T[c].notna().all()]
    c = idx + [f"{x}_rel" for x in idx]
    if T.sensor_L.nunique() > 1:
        c.append("sensor_L")
    c += CLIMA + (RIEGO if riego else []) + ["das"]
    return c


def construir_tablas(emb: pd.DataFrame | None) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    D0, calidad = cargar_base()
    D = agregar_clima_riego(D0[D0.ok].reset_index(drop=True))
    D = agregar_prithvi(D, emb)
    curvas = curvas_dron()
    tablas = {}
    for ind in INDICES:
        T = D.dropna(subset=[ind.lower()]).reset_index(drop=True).copy()
        T["y"] = respuesta(T, ind, curvas)
        tablas[ind] = T
    return tablas, calidad, D0


def contrato(tablas: dict) -> pd.DataFrame:
    filas = []
    for ind, T in tablas.items():
        f, p = T.fid.nunique(), len(T)
        filas.append({"indice": ind, "fechas": f, "pares": p, "esperado_fechas": CONTRATO[ind][0],
                      "esperado_pares": CONTRATO[ind][1], "fechas_hls": T[T.tiene_hls].fid.nunique(),
                      "pares_hls": int(T.tiene_hls.sum())})
        if (f, p) != CONTRATO[ind]:
            raise ErrorEstado("ERROR_DATOS", f"{ind}: {f} fechas y {p} pares; se esperaban {CONTRATO[ind]}")
        if T.y.isna().any() or not np.isfinite(T.y).all():
            raise ErrorEstado("ERROR_DATOS", f"{ind}: respuesta con NaN")
        cand = candidatas(ind, T)
        malas = [c for c in cand if c in PROHIBIDAS or c.startswith("vwc")]
        if malas:
            raise ErrorEstado("ERROR_DATOS", f"{ind}: columnas prohibidas entre las candidatas: {malas}")
        if T[cand].isna().any().any():
            raise ErrorEstado("ERROR_DATOS", f"{ind}: NaN en candidatas {T[cand].columns[T[cand].isna().any()].tolist()}")
        s2 = T[T.sensor == "S2"]
        if (s2.ndvi - s2.ndvi_pix).abs().max() > 1e-5:  # el CSV de correlación usa otra suma (≈2e-6)
            raise ErrorEstado("ERROR_DATOS", "NDVI por franja ≠ media de sus píxeles puros")
    return pd.DataFrame(filas)


def no_regresion(tablas: dict) -> pd.DataFrame:
    ref = pd.read_csv(DATOS / "corrector_dron_v2.csv")
    filas = []
    for ind in ("NDVI", "NDRE"):
        T = tablas[ind]
        for prueba in (1, 2, 3):
            tr, te = T[T.temporada != prueba], T[T.temporada == prueba]
            X = (lambda z: np.c_[np.ones(len(z)), z.ndvi, z.sensor_L]) if ind == "NDVI" else \
                (lambda z: np.c_[np.ones(len(z)), z.ndre])
            beta = np.linalg.lstsq(X(tr), tr.y, rcond=None)[0]
            r = float(np.corrcoef(X(te) @ beta, te.y)[0, 1])
            rr = ref[(ref.indice == ind) & (ref.temporada_prueba == prueba)].iloc[0]
            filas.append({"indice": ind, "temporada_prueba": prueba, "r": round(r, 4), "r_referencia": rr.r,
                          "dif": round(abs(r - rr.r), 4), "fechas": te.fid.nunique(), "fechas_ref": int(rr.fechas),
                          "pares": len(te), "pares_ref": int(rr.pares)})
    R = pd.DataFrame(filas)
    R["pasa"] = (R.dif <= 0.01) & (R.fechas == R.fechas_ref) & (R.pares == R.pares_ref)
    # la referencia está redondeada a 2 decimales: ±0,005 de redondeo + 0,01 de tolerancia
    if not R.pasa.all():
        raise ErrorEstado("ERROR_NO_REGRESION", "no reproduce corrector_dron_v2.csv:\n" + R.to_string(index=False))
    return R


def verificar_osavi(D0: pd.DataFrame) -> pd.DataFrame:
    """Rojo despejado del NDVI frente al rojo real de la imagen original, en 3 píxeles de 1 fecha S2 y 1 Landsat."""
    import planetary_computer as pc
    import pystac_client
    import rasterio
    fechas = pd.read_csv(DATOS / "fechas_despejadas_lote.csv")
    pix = pd.read_csv(DATOS / "pixeles_lote_s2_landsat.csv", parse_dates=["fecha"])
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    filas = []
    for sensor, col, banda in (("S2", "sentinel-2-l2a", "B04"), ("L", "landsat-c2-l2", "red")):
        ok = D0[(D0.sensor == sensor) & D0.ok & (D0.satelite != "landsat-7")]
        f = sorted(ok.fecha.unique())[len(ok.fecha.unique()) // 2]
        fila = fechas[(fechas.coleccion == col) & (pd.to_datetime(fechas.fecha) == f)].iloc[0]
        it = cat.get_collection(col).get_item(fila.id)
        p = pix[(pix.sensor == sensor) & (pix.fecha == f)].sort_values("frac_lote", ascending=False).head(3)
        with rasterio.open(it.assets[banda].href) as src:
            for r in p.itertuples():
                _, x, y = r.pixel_id.split("_")
                dn = float(next(src.sample([(float(x), float(y))]))[0])
                if sensor == "S2":
                    base = str(it.properties.get("s2:processing_baseline", "00.00"))
                    real = (dn + (-1000.0 if base >= "04.00" else 0.0)) / 10000.0
                else:
                    real = dn * 0.0000275 - 0.2
                desp = r.nir * (1 - r.ndvi) / (1 + r.ndvi)
                filas.append({"sensor": sensor, "fecha": str(pd.Timestamp(f).date()), "pixel_id": r.pixel_id,
                              "rojo_imagen": round(real, 5), "rojo_despejado": round(desp, 5),
                              "dif": round(abs(real - desp), 6)})
    V = pd.DataFrame(filas)
    if (V.dif > 1e-3).any():
        raise ErrorEstado("ERROR_DATOS", "el rojo despejado del NDVI no coincide con la imagen:\n" + V.to_string())
    return V


# ================================================================ PASO 2 (parte Prithvi): HLS → Prithvi congelado
def utm_lote():
    from pyproj import Transformer
    return Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(LON, LAT)


def bajar_hls(cat, coleccion: str, item_id: str):
    import rasterio
    from rasterio.windows import Window
    it = cat.get_collection(coleccion).get_item(item_id)
    with rasterio.open(it.assets["B04"].href) as ref:
        trf = ref.transform
        x, y = utm_lote()
        c0, r0 = int(math.floor((x - trf.c) / trf.a)), int(math.floor((y - trf.f) / trf.e))
        win = Window(c0 - PIX_LOTE, r0 - PIX_LOTE, LADO, LADO)
        wtr = ref.window_transform(win)
    arr = []
    for b in HLS_BANDAS[coleccion] + ["Fmask"]:
        def leer(b=b):
            with rasterio.open(it.assets[b].href) as src:
                return src.read(1, window=win, boundless=True, fill_value=(255 if b == "Fmask" else -9999))
        for k in range(4):
            try:
                arr.append(leer())
                break
            except Exception:  # noqa: BLE001
                if k == 3:
                    raise
                time.sleep(8 * (k + 1))
    return np.stack(arr[:6]).astype(np.int16), arr[6].astype(np.uint8), wtr, it.datetime


def stats_lote_hls(bandas, fmask, wtr) -> dict:
    pl = pd.read_csv(DATOS / "pixeles_lote_s2_landsat.csv")
    pl = pl[(pl.sensor == "L")].drop_duplicates("pixel_id")
    pl = pl[pl.frac_lote >= 0.5]
    inv = ~wtr
    rc = []
    for pid in pl.pixel_id:
        _, x, y = pid.split("_")
        c, r = inv * (float(x), float(y))
        rc.append((int(math.floor(r)), int(math.floor(c))))
    rr = np.array([a for a, _ in rc])
    cc = np.array([b for _, b in rc])
    assert ((rr // PARCHE == CELDA_LOTE) & (cc // PARCHE == CELDA_LOTE)).all(), "el lote no cae en el bloque (7, 7)"
    fm = fmask[rr, cc].astype(int)
    limpio = (fm != 255) & ((fm & 0b1110) == 0)  # Fmask: bit 1 nube, 2 adyacente, 3 sombra
    ref = bandas[:, rr, cc].astype(float) * 1e-4
    azul, rojo, nir = ref[0], ref[2], ref[3]
    ok = limpio & (bandas[0, rr, cc] != -9999)
    ndvi = (nir - rojo) / (nir + rojo)
    blk = fmask[CELDA_LOTE * PARCHE:(CELDA_LOTE + 1) * PARCHE, CELDA_LOTE * PARCHE:(CELDA_LOTE + 1) * PARCHE].astype(int)
    return {"frac_despejado_lote": float(limpio.mean()), "ndvi_lote": float(ndvi[ok].mean()) if ok.any() else np.nan,
            "azul_lote": float(azul[ok].mean()) if ok.any() else np.nan, "n_pix_lote": int(len(rr)),
            "frac_despejado_bloque": float(((blk != 255) & ((blk & 0b1110) == 0)).mean()),
            "frac_sin_dato_recorte": float((bandas[0] == -9999).mean())}


def cargar_prithvi():
    import importlib.util
    import torch
    from huggingface_hub import hf_hub_download
    kw_dl = {"local_dir": os.environ["SIRA_PRITHVI_DIR"]} if os.environ.get("SIRA_PRITHVI_DIR") else {}
    cfg_p = hf_hub_download(PRITHVI_REPO, "config.json", revision=PRITHVI_REV, **kw_dl)
    code_p = hf_hub_download(PRITHVI_REPO, "prithvi_mae.py", revision=PRITHVI_REV, **kw_dl)
    pesos_p = hf_hub_download(PRITHVI_REPO, PRITHVI_PESOS, revision=PRITHVI_REV, **kw_dl)
    spec = importlib.util.spec_from_file_location("prithvi_mae", code_p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cfg = json.loads(Path(cfg_p).read_text())["pretrained_cfg"]
    kw = {k: cfg[k] for k in ("img_size", "patch_size", "in_chans", "embed_dim", "depth", "num_heads",
                              "decoder_embed_dim", "decoder_depth", "decoder_num_heads", "mlp_ratio",
                              "coords_encoding", "coords_scale_learn", "mask_ratio", "norm_pix_loss")}
    kw["num_frames"] = 4
    modelo = mod.PrithviMAE(**kw)
    sd = torch.load(pesos_p, map_location="cpu", weights_only=True)
    for k in [k for k in sd if "pos_embed" in k]:  # igual que inference.py oficial: se recalcula (seno-coseno fijo)
        del sd[k]
    faltan, sobran = modelo.load_state_dict(sd, strict=False)
    assert all("pos_embed" in k for k in faltan) and not sobran, (faltan, sobran)
    enc = modelo.encoder.eval()
    del modelo.decoder
    torch.set_num_threads(os.cpu_count() or 1)
    info = {"repo": PRITHVI_REPO, "revision": PRITHVI_REV, "pesos_sha256": sha256(Path(pesos_p)),
            "mean": cfg["mean"], "std": cfg["std"], "bands": cfg["bands"]}
    return enc, cfg, info


def embedding(enc, cfg, frames: list[np.ndarray], fechas: list[pd.Timestamp]) -> np.ndarray:
    """Vector de 1024 del bloque que contiene el lote, promediado en el tiempo. Normalización oficial (inference.py):
    (DN − media) / desviación; sin dato (−9999) → 0,0001."""
    import torch
    mean = np.array(cfg["mean"], dtype=np.float32)[:, None, None]
    std = np.array(cfg["std"], dtype=np.float32)[:, None, None]
    x = np.stack([np.where(f == -9999, 0.0001, (f.astype(np.float32) - mean) / std) for f in frames], axis=1)  # C,T,H,W
    T = len(frames)
    xt = torch.from_numpy(x[None].astype(np.float32))
    tc = torch.tensor([[[f.year, f.dayofyear] for f in fechas]], dtype=torch.float32)
    lc = torch.tensor([[LAT, LON]], dtype=torch.float32)  # LocationEncoder: columna 0 = latitud, 1 = longitud
    enc.num_frames = T
    with torch.no_grad():
        z = enc.forward_features(xt, tc, lc)[-1][0].numpy()  # (1 + T·196, 1024)
    g = LADO // PARCHE
    tok = [1 + t * g * g + CELDA_LOTE * g + CELDA_LOTE for t in range(T)]
    v = z[tok].mean(axis=0)
    if not np.isfinite(v).all():
        raise ErrorEstado("ERROR_PRITHVI", "vector Prithvi no finito")
    return v


def etapa_prithvi(out: Path, qa: bool) -> dict:
    import planetary_computer as pc
    import pystac_client
    fechas = pd.read_csv(DATOS / "fechas_despejadas_lote.csv")
    h = fechas[fechas.coleccion.str.startswith("hls") & fechas.despejado & fechas.temporada.notna()].copy()
    h["fecha"] = pd.to_datetime(h.fecha)
    h["temporada"], h["das"] = h.temporada.astype(int), h.das.astype(int)
    h = h.sort_values(["fecha", "coleccion"]).reset_index(drop=True)
    objetivos = h[h.das.between(*VENTANA)]
    if qa:  # 1 fecha objetivo con 4 fechas de entrada (el caso más caro): la última de GS2, con su historia
        objetivos = objetivos[objetivos.temporada == 2].tail(1)
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    (out / "hls").mkdir(exist_ok=True)
    cache, tiempos = {}, {"descarga_s": [], "prithvi_s": []}

    def item(r):
        iid = getattr(r, "item_id", None) or r.id
        if iid not in cache:
            p = out / "hls" / f"{iid}.npz"
            t = time.time()
            if p.exists():
                z = np.load(p, allow_pickle=True)
                bandas, fm, wtr = z["b"], z["f"], tuple(z["t"])
                from affine import Affine
                wtr = Affine(*wtr[:6])
            else:
                bandas, fm, wtr, _ = bajar_hls(cat, r.coleccion, iid)
                np.savez_compressed(p, b=bandas, f=fm, t=np.array(tuple(wtr)))
            tiempos["descarga_s"].append(time.time() - t)
            cache[iid] = (bandas, stats_lote_hls(bandas, fm, wtr))
            latido(f"hls {iid}")
        return cache[iid]

    # control de calidad de HLS: Fmask del lote ≥ 90 % + el mismo control de bruma de S2/Landsat, por temporada
    necesarios = h if not qa else h[(h.temporada == 2) & (h.fecha <= objetivos.fecha.max())]
    filas = []
    for r in necesarios.itertuples():
        try:
            _, s = item(r)
            filas.append({"temporada": r.temporada, "fecha": r.fecha, "das": r.das, "coleccion": r.coleccion,
                          "item_id": r.id, **s, "error": ""})
        except Exception as e:  # noqa: BLE001
            import re
            filas.append({"temporada": r.temporada, "fecha": r.fecha, "das": r.das, "coleccion": r.coleccion,
                          "item_id": r.id, "error": re.sub(r"\?\S*", "?<token>", str(e))[:200]})
            log(f"HLS {r.id}: error {str(e)[:100]}")
    Q = pd.DataFrame(filas)
    for c in ("frac_despejado_lote", "ndvi_lote", "azul_lote"):
        if c not in Q:
            Q[c] = np.nan
    Q["motivo"] = "ok"
    Q.loc[Q.error != "", "motivo"] = "error de descarga"
    Q.loc[(Q.error == "") & (Q.frac_despejado_lote < 0.9), "motivo"] = "Fmask: < 90 % del lote despejado"
    en_v = Q.das.between(*VENTANA) & (Q.motivo == "ok")
    Q.loc[en_v & (Q.azul_lote >= 0.075), "motivo"] = "azul del lote ≥ 0,075"
    # misma toma que una imagen S2 L2A o Landsat ya descartada por bruma (HLS sale de la misma adquisición)
    _, cal_sl = cargar_base()
    malas_sl = {(pd.Timestamp(f), s) for f, s, m in zip(cal_sl.fecha, cal_sl.sensor, cal_sl.motivo) if m != "ok"}
    par = {"hls2-s30": "S2", "hls2-l30": "L"}
    misma = [(pd.Timestamp(f), par[c]) in malas_sl for f, c in zip(Q.fecha, Q.coleccion)]
    Q.loc[en_v & np.array(misma), "motivo"] = "misma toma descartada en S2 L2A/Landsat (bruma)"
    en_v = Q.das.between(*VENTANA) & (Q.motivo == "ok")
    for t, g in Q[en_v].groupby("temporada"):
        g = g.sort_values(["fecha", "coleccion"])
        prev, sig = g.ndvi_lote.shift(1), g.ndvi_lote.shift(-1)
        caida = (prev - g.ndvi_lote > 0.10) & (sig - g.ndvi_lote > 0.10)
        Q.loc[g.index[caida.to_numpy()], "motivo"] = "caída de NDVI del lote"
    Q.to_csv(out / "hls_calidad.csv", index=False)
    log("HLS calidad:\n" + Q[["temporada", "fecha", "das", "coleccion", "frac_despejado_lote", "ndvi_lote", "azul_lote",
                              "motivo"]].to_string(index=False))
    buenos = Q[Q.motivo == "ok"]
    t = time.time()
    enc, cfg, info = cargar_prithvi()
    tiempos["carga_modelo_s"] = time.time() - t
    filas, sin_vector = [], []
    for r in objetivos.itertuples():
        b = buenos[(buenos.item_id == r.id)]
        if b.empty:
            continue  # descartada por calidad: queda registrada en hls_calidad.csv
        try:
            hist = buenos[(buenos.temporada == r.temporada) & (buenos.fecha < r.fecha)].copy()
            hist["pref"] = hist.coleccion != r.coleccion
            hist = hist.sort_values(["fecha", "pref"]).drop_duplicates("fecha", keep="first").tail(3)
            usar = list(hist.itertuples()) + [b.iloc[0]]
            frames = [item(u)[0] for u in usar]
            t = time.time()
            v = embedding(enc, cfg, frames, [pd.Timestamp(u.fecha) for u in usar])
            tiempos["prithvi_s"].append(time.time() - t)
            filas.append({"fecha": str(r.fecha.date()), "temporada": r.temporada, "das": r.das, "coleccion": r.coleccion,
                          "item_id": r.id, "n_fechas": len(usar), "fechas_entrada": ";".join(str(pd.Timestamp(u.fecha).date()) for u in usar),
                          **{f"p{i}": float(x) for i, x in enumerate(v)}})
            log(f"Prithvi {r.fecha.date()} {r.coleccion} con {len(usar)} fechas: {tiempos['prithvi_s'][-1]:.1f} s")
        except ErrorEstado:
            raise
        except Exception as e:  # noqa: BLE001
            sin_vector.append(r.id)
            log(f"Prithvi {r.id}: sin vector ({str(e)[:120]})")
        latido(f"prithvi {r.fecha.date()}")
    E = pd.DataFrame(filas)
    n_obj = int(objetivos.id.isin(buenos.item_id).sum())
    if n_obj == 0 or len(sin_vector) > 0.3 * n_obj:
        raise ErrorEstado("ERROR_PRITHVI", f"sin vector en {len(sin_vector)} de {n_obj} fechas HLS despejadas")
    nombre = "prithvi_embeddings_qa.csv" if qa else "prithvi_embeddings.csv"
    E.to_csv(out / nombre, index=False, float_format="%.6g")
    subir("SIRA_URL_PRITHVI", out / nombre)
    info.update({"fechas_objetivo": n_obj, "con_vector": len(E), "sin_vector": sin_vector,
                 "descarga_media_s": float(np.mean(tiempos["descarga_s"])) if tiempos["descarga_s"] else 0.0,
                 "prithvi_medio_s": float(np.mean(tiempos["prithvi_s"])) if tiempos["prithvi_s"] else 0.0,
                 "carga_modelo_s": tiempos["carga_modelo_s"], "items_hls": int(len(h)), "objetivos_total": int(h.das.between(*VENTANA).sum()),
                 "nota_ubicacion": "recorte 224×224 px HLS; el píxel del lote en (119, 119) → bloque (7, 7) de 16×16 px (480 m)"})
    (out / ("prithvi_info_qa.json" if qa else "prithvi_info.json")).write_text(json.dumps(info, indent=1, default=str), encoding="utf-8")
    log(f"Prithvi listo: {len(E)} vectores de {n_obj} fechas objetivo")
    return info


# ================================================================ PASOS 3–7: partición, métricas, modelos, selección
def mse_fecha(e2: np.ndarray, fid: np.ndarray) -> np.ndarray:
    _, inv = np.unique(fid, return_inverse=True)
    return np.bincount(inv, e2) / np.bincount(inv)


def rmse_fecha(y, p, fid) -> float:
    return float(np.sqrt(np.mean(mse_fecha((np.asarray(y) - np.asarray(p)) ** 2, np.asarray(fid)))))


def metricas(y, p, fid, base: float, sensor=None, unidad=None) -> dict:
    y, p, fid = np.asarray(y, float), np.asarray(p, float), np.asarray(fid)
    m = {"rmse_fecha": rmse_fecha(y, p, fid), "rmse": float(np.sqrt(np.mean((y - p) ** 2))),
         "mae": float(np.mean(np.abs(y - p))), "r": float(np.corrcoef(y, p)[0, 1]) if len(y) > 2 and np.std(p) > 0 else np.nan,
         "rmse_base_fecha": rmse_fecha(y, np.full_like(y, base), fid), "rmse_base": float(np.sqrt(np.mean((y - base) ** 2))),
         "n_fechas": int(len(np.unique(fid))), "n_pares": int(len(y))}
    m["mejora_vs_base_pct"] = 100 * (1 - m["rmse_fecha"] / m["rmse_base_fecha"]) if m["rmse_base_fecha"] > 0 else np.nan
    if sensor is not None:
        from scipy.stats import kendalltau
        acierta, taus = [], []
        for f in np.unique(fid):
            s = (fid == f) & (np.asarray(sensor) == "S2")
            if s.sum() == 3:
                acierta.append(np.argmin(p[s]) == np.argmin(y[s]))
                taus.append(kendalltau(p[s], y[s]).statistic)
        m["pct_franja_mas_baja"] = 100 * float(np.mean(acierta)) if acierta else np.nan
        m["tau_kendall_medio"] = float(np.nanmean(taus)) if taus else np.nan
        m["fechas_orden"] = len(acierta)
    return m


@dataclass(frozen=True)
class Cfg:
    variante: str
    familia: str
    params: tuple
    vars: tuple
    k: int = 0

    def p(self) -> dict:
        return dict(self.params)

    def clave(self) -> str:
        return f"{self.variante}|{self.familia}|{json.dumps(self.p(), sort_keys=True)}|{','.join(self.vars)}|k={self.k}"

    def simplicidad(self) -> tuple:
        return (RANGO_VAR.get(self.variante, 0), RANGO_FAM[self.familia], len(self.vars) + (1 if self.k else 0), self.k)


def rejilla(fam: str, arboles_rf: int = 500) -> list[dict]:
    if fam == "lineal":
        return [{}]
    if fam == "huber":
        return [{"epsilon": e, "alpha": a} for e in (1.35, 1.5, 2.0) for a in (1e-4, 1e-2)]
    if fam == "ridge":
        return [{"alpha": float(a)} for a in np.logspace(-3, 3, 13)]
    if fam == "lasso":
        return [{"alpha": float(a)} for a in np.logspace(-4, 0, 9)]
    if fam == "splines":
        return [{"n_knots": n, "alpha": a} for n in (3, 4, 5) for a in (0.1, 1.0, 10.0)]
    if fam == "rf":
        return [{"n_estimators": arboles_rf, "max_depth": d, "min_samples_leaf": m, "max_features": f}
                for d in (2, 3, 4, None) for m in (3, 5, 10) for f in (1.0, "sqrt")]
    if fam == "xgb":
        return [{"n_estimators": n, "max_depth": d, "learning_rate": lr, "min_child_weight": w, "subsample": 0.8}
                for n in (100, 300) for d in (1, 2, 3) for lr in (0.03, 0.1) for w in (3, 5)]
    if fam == "mlp":
        return [{"hidden_layer_sizes": (16,), "alpha": a} for a in (1e-3, 1e-1)]
    raise ValueError(fam)


from sklearn.base import BaseEstimator, TransformerMixin  # noqa: E402


class PCAPrithvi(BaseEstimator, TransformerMixin):
    """PCA del vector Prithvi ajustado SOLO con el entrenamiento, sobre los vectores distintos (una fecha = un vector,
    peso igual por fecha). k se recorta a (fechas distintas − 1), el rango máximo posible."""

    def __init__(self, k: int = 5):
        self.k = k

    def fit(self, X, y=None):
        from sklearn.decomposition import PCA
        Xu = np.unique(np.asarray(X, float), axis=0)
        self.k_ = int(min(self.k, len(Xu) - 1))
        if self.k_ < 1:
            raise ValueError("PCA de Prithvi: menos de 2 fechas HLS en el entrenamiento")
        self.pca_ = PCA(n_components=self.k_, svd_solver="full", random_state=0).fit(Xu)
        return self

    def transform(self, X):
        return self.pca_.transform(np.asarray(X, float))


def construir(cfg: Cfg, semilla: int = 0):
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.linear_model import HuberRegressor, Lasso, LinearRegression, Ridge
    from sklearn.neural_network import MLPRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import SplineTransformer, StandardScaler
    from xgboost import XGBRegressor
    partes = []
    if cfg.vars:
        partes.append(("tab", StandardScaler(), list(cfg.vars)))
    if cfg.k:
        partes.append(("prithvi", Pipeline([("pca", PCAPrithvi(cfg.k)), ("esc", StandardScaler())]), PCOLS))
    pasos = [("entradas", ColumnTransformer(partes, remainder="drop"))]
    p = cfg.p()
    f = cfg.familia
    if f == "lineal":
        pasos.append(("modelo", LinearRegression()))
    elif f == "huber":
        pasos.append(("modelo", HuberRegressor(epsilon=p["epsilon"], alpha=p["alpha"], max_iter=2000)))
    elif f == "ridge":
        pasos.append(("modelo", Ridge(alpha=p["alpha"])))
    elif f == "lasso":
        pasos.append(("modelo", Lasso(alpha=p["alpha"], max_iter=100000)))
    elif f == "splines":
        pasos += [("splines", SplineTransformer(n_knots=p["n_knots"], degree=3)), ("modelo", Ridge(alpha=p["alpha"]))]
    elif f == "rf":
        pasos.append(("modelo", RandomForestRegressor(n_estimators=p["n_estimators"], max_depth=p["max_depth"],
                                                      min_samples_leaf=p["min_samples_leaf"], max_features=p["max_features"],
                                                      random_state=semilla, n_jobs=1)))
    elif f == "xgb":
        pasos.append(("modelo", XGBRegressor(n_estimators=p["n_estimators"], max_depth=p["max_depth"],
                                             learning_rate=p["learning_rate"], min_child_weight=p["min_child_weight"],
                                             subsample=p["subsample"], random_state=semilla, n_jobs=1, verbosity=0)))
    elif f == "mlp":
        pasos.append(("modelo", MLPRegressor(hidden_layer_sizes=p["hidden_layer_sizes"], alpha=p["alpha"],
                                             solver="lbfgs", max_iter=5000, random_state=semilla)))
    return Pipeline(pasos)


def mascara(variante: str, T: pd.DataFrame) -> np.ndarray:
    return T.tiene_hls.to_numpy() if variante in ("B2", "B3", "B1_mismas_filas") else np.ones(len(T), bool)


def ajustar_predecir(cfg: Cfg, tr: pd.DataFrame, te: pd.DataFrame, semilla: int = 0) -> np.ndarray:
    m = construir(cfg, semilla).fit(tr, tr.y.to_numpy())
    p = m.predict(te)
    return np.asarray(p, float)


def bloques_moviles(fids_ordenados: np.ndarray, rng, largo: int = 2) -> np.ndarray:
    n = len(fids_ordenados)
    if n == 0:
        return fids_ordenados
    inicios = np.arange(max(n - largo + 1, 1))
    el = []
    while len(el) < n:
        i = rng.choice(inicios)
        el += list(fids_ordenados[i:i + largo])
    return np.array(el[:n])


def remuestrear(T: pd.DataFrame, temporadas, rng) -> pd.DataFrame:
    """Bloques móviles de 2 fechas consecutivas dentro de cada temporada; cada copia de fecha recibe su propio id."""
    partes = []
    for t in temporadas:
        g = T[T.temporada == t]
        f = np.array(sorted(g.fid.unique()))
        for j, x in enumerate(bloques_moviles(f, rng)):
            partes.append(g[g.fid == x].assign(fid=f"{x}#{j}"))
    return pd.concat(partes, ignore_index=True) if partes else T.iloc[:0]


@dataclass
class Resultado:
    cfg: Cfg
    rmse_val: float
    por_dir: list = field(default_factory=list)  # [(fid_val, e2, mask_hls)] por sentido


def evaluar(cfg: Cfg, T: pd.DataFrame, dirs, semilla=0) -> Resultado | None:
    m = mascara(cfg.variante, T)
    sub = T[m]
    por, num, den = [], 0.0, 0
    for tr_t, va_t in dirs:
        tr, va = sub[sub.temporada == tr_t], sub[sub.temporada == va_t]
        if len(tr) < 3 or len(va) == 0:
            return None
        try:
            p = ajustar_predecir(cfg, tr, va, semilla)
        except ValueError:
            return None
        if not np.isfinite(p).all():
            return None
        e2 = (va.y.to_numpy() - p) ** 2
        fid = va.fid.to_numpy()
        nf = len(np.unique(fid))
        num += nf * np.sqrt(np.mean(mse_fecha(e2, fid)))
        den += nf
        por.append((fid, e2, va.tiene_hls.to_numpy(), p, va.index.to_numpy()))
    return Resultado(cfg, num / den, por)


def rmse_val_hls(r: Resultado) -> float:
    num, den = 0.0, 0
    for fid, e2, h, _, _ in r.por_dir:
        if h.any():
            nf = len(np.unique(fid[h]))
            num += nf * np.sqrt(np.mean(mse_fecha(e2[h], fid[h])))
            den += nf
    return num / den if den else np.nan


def ic_diferencias(res: list[Resultado], mejor: Resultado, n: int, rng, solo_hls=False) -> list[tuple]:
    """IC 95 % de RMSE(candidato) − RMSE(mejor) con remuestras por bloques móviles de las fechas de validación
    (las mismas para todos los candidatos, sin reentrenar)."""
    ndir = len(mejor.por_dir)
    sel = []
    for d in range(ndir):
        fid, _, h, _, _ = mejor.por_dir[d]
        f = np.unique(fid[h] if solo_hls else fid)
        sel.append([bloques_moviles(f, rng) for _ in range(n)])

    def boot(r: Resultado) -> np.ndarray:
        tot = np.zeros(n)
        den = 0
        for d in range(ndir):
            fid, e2, h, _, _ = r.por_dir[d]
            if solo_hls:
                fid, e2 = fid[h], e2[h]
            if len(fid) == 0:
                continue
            u, inv = np.unique(fid, return_inverse=True)
            mse = dict(zip(u, np.bincount(inv, e2) / np.bincount(inv)))
            nf = len(u)
            tot += nf * np.array([np.sqrt(np.mean([mse[x] for x in s])) for s in sel[d]])
            den += nf
        return tot / den if den else np.full(n, np.nan)

    b0 = boot(mejor)
    out = []
    for r in res:
        dif = boot(r) - b0
        out.append((float(np.nanpercentile(dif, 2.5)), float(np.nanpercentile(dif, 97.5))))
    return out


def elegir(res: list[Resultado], n_tie: int, rng, solo_hls=False, clave_rmse=None):
    """Gana el menor RMSE de validación; empate si el IC 95 % de la diferencia incluye 0; entre empatados, el más simple."""
    clave_rmse = clave_rmse or (lambda r: r.rmse_val)
    res = [r for r in res if np.isfinite(clave_rmse(r))]
    mejor = min(res, key=clave_rmse)
    ics = ic_diferencias(res, mejor, n_tie, rng, solo_hls)
    empatados = [r for r, (lo, hi) in zip(res, ics) if lo <= 0 <= hi or r is mejor]
    gan = min(empatados, key=lambda r: (r.cfg.simplicidad(), clave_rmse(r)))
    motivo = ("menor RMSE de validación" if gan is mejor else
              f"empate con el mejor ({mejor.cfg.familia}, {clave_rmse(mejor):.4f}) → el más simple")
    return gan, mejor, motivo, dict(zip([id(r) for r in res], ics))


def ks_efectivos(T: pd.DataFrame, dirs) -> list[int]:
    """k ∈ {3, 5, 10, 20}; dos k que dan el mismo número efectivo de componentes en ambos sentidos son el mismo modelo."""
    sub = T[T.tiene_hls]
    vistos, ks = set(), []
    for k in KS:
        ef = []
        for tr_t, _ in dirs:
            nu = sub[sub.temporada == tr_t].hls_item.nunique()
            ef.append(min(k, nu - 1))
        if min(ef) < 1:
            return []
        if tuple(ef) not in vistos:
            vistos.add(tuple(ef))
            ks.append(k)
    return ks


def adelante(T, dirs, variante, inicio, cand, ks, n_max=4, min_mejora=0.01):
    """Paso 6: selección hacia adelante con el modelo lineal, guiada por el RMSE de validación interna."""
    camino = []
    k = 0
    if variante == "B2":  # B2 = B1 + Prithvi: el bloque PCA entra desde el inicio; su k se elige aquí y se reajusta en el paso 7
        rs = [(evaluar(Cfg(variante, "lineal", (), tuple(inicio), kk), T, dirs), kk) for kk in ks]
        rs = [(r, kk) for r, kk in rs if r is not None]
        if not rs:
            return None, 0, camino
        r0, k = min(rs, key=lambda z: z[0].rmse_val)
    else:
        r0 = evaluar(Cfg(variante, "lineal", (), tuple(inicio), 0), T, dirs)
    if r0 is None:
        return None, 0, camino
    sel, mejor = list(inicio), r0.rmse_val
    camino.append({"paso": 0, "agrega": "+".join(inicio) + (f"+prithvi_k{k}" if k else ""), "rmse_val": mejor})
    n_ent = len(sel) + (1 if k else 0)
    while n_ent < n_max:
        pruebas = []
        for c in cand:
            if c in sel:
                continue
            r = evaluar(Cfg(variante, "lineal", (), tuple(sel + [c]), k), T, dirs)
            if r is not None:
                pruebas.append((r.rmse_val, c))
        if not pruebas:
            break
        rm, c = min(pruebas)
        if rm < mejor * (1 - min_mejora):
            sel.append(c)
            camino.append({"paso": len(camino), "agrega": c, "rmse_val": rm, "rmse_antes": mejor})
            mejor = rm
            n_ent += 1
        else:
            camino.append({"paso": len(camino), "agrega": f"(ninguna: la mejor, {c}, baja {100 * (1 - rm / mejor):.1f} %)",
                           "rmse_val": mejor})
            break
    return sel, k, camino


def seleccion_completa(T: pd.DataFrame, ind: str, dirs, n_tie: int, rng, arboles_rf=500, detalle=False,
                       variantes=VARIANTES):
    """Pasos 5–7 dentro de las temporadas de entrenamiento: variables (paso 6) y familia/hiperparámetros (paso 7) por
    variante; después, entre variantes, comparación en las mismas filas (las que tienen HLS)."""
    out = {"ganadores": {}, "filas_val": [], "caminos": [], "motivos": {}}
    cand = {"B1": candidatas(ind, T, True), "B1_sin_riego": candidatas(ind, T, False)}
    cand["B2"] = cand["B1"]
    ks = ks_efectivos(T, dirs)
    todos = {}
    for var in variantes:
        if var in ("B2", "B3") and not ks:
            continue
        if var == "B3":
            vars_, k0, camino = [], 0, []
        else:
            vars_, k0, camino = adelante(T, dirs, var, [ind.lower()], cand[var], ks)
            if vars_ is None:
                continue
        out["caminos"] += [{"variante": var, **c} for c in camino]
        fams = FAM_B3 if var == "B3" else FAMILIAS
        cfgs = [Cfg(var, f, tuple(sorted(p.items())), tuple(vars_), k)
                for f in fams for p in rejilla(f, arboles_rf) for k in (ks if var in ("B2", "B3") else [0])]
        res = [r for r in (evaluar(c, T, dirs) for c in cfgs) if r is not None]
        if not res:
            continue
        gan, mejor, motivo, ics = elegir(res, n_tie, rng)
        out["ganadores"][var] = gan
        out["motivos"][var] = motivo
        todos[var] = res
        if detalle:
            for r in res:
                lo, hi = ics[id(r)]
                out["filas_val"].append({"variante": var, "familia": r.cfg.familia, "params": json.dumps(r.cfg.p()),
                                         "variables": ",".join(r.cfg.vars), "k_prithvi": r.cfg.k,
                                         "rmse_val": r.rmse_val, "rmse_val_filas_hls": rmse_val_hls(r),
                                         **{f"rmse_val_{tr}a{va}": float(np.sqrt(np.mean(mse_fecha(e2, fid))))
                                            for (tr, va), (fid, e2, _, _, _) in zip(dirs, r.por_dir)},
                                         "dif_ic95_inf": lo, "dif_ic95_sup": hi,
                                         "empata_con_mejor": bool(lo <= 0 <= hi or r is mejor),
                                         "ganador_variante": r is gan})
    # entre variantes: B1 < B2 < B3, en las mismas filas de validación (con HLS)
    cand_v = [out["ganadores"][v] for v in ("B1", "B2", "B3") if v in out["ganadores"]]
    cand_v = [r for r in cand_v if np.isfinite(rmse_val_hls(r))]
    if len(cand_v) > 1:
        gan, mejor, motivo, _ = elegir(cand_v, n_tie, rng, solo_hls=True, clave_rmse=rmse_val_hls)
    else:
        gan, motivo = out["ganadores"]["B1"], "única variante con filas de validación"
    out["final"] = gan
    out["motivo_final"] = motivo
    out["todos"] = todos if detalle else None
    out["ks"] = ks
    return out


def resumen_cfg(c: Cfg) -> dict:
    return {"variante": c.variante, "familia": c.familia, "params": json.dumps(c.p()), "variables": ",".join(c.vars),
            "k_prithvi": c.k}


# ================================================================ tareas en paralelo (procesos)
def _semilla(*xs) -> np.random.Generator:
    return np.random.default_rng([SEMILLA, *xs])


COD = {"NDVI": 1, "NDRE": 2, "OSAVI": 3}


def tarea_principal(T, ind, prueba, n_tie, arboles_rf, cronologica=False):
    """Selección completa con las 2 temporadas de entrenamiento; recién después, una sola medición en la sellada."""
    t0 = time.time()
    entr = [t for t in (1, 2, 3) if t != prueba]
    dirs = [(1, 2)] if cronologica else [(entr[0], entr[1]), (entr[1], entr[0])]
    S = seleccion_completa(T, ind, dirs, n_tie, _semilla(COD[ind], prueba, 0 if not cronologica else 9), arboles_rf,
                           detalle=True)
    t_sel = time.time()
    ev_sel = time.time_ns()
    gan = S["final"]
    tr = T[(T.temporada.isin([1, 2] if cronologica else entr)) & mascara(gan.cfg.variante, T)]
    te_all = T[T.temporada == prueba]
    te = te_all[mascara(gan.cfg.variante, T)[T.temporada.to_numpy() == prueba]]
    p = ajustar_predecir(gan.cfg, tr, te)
    ev_prueba = time.time_ns()
    base = float(tr.y.mean())
    met = metricas(te.y, p, te.fid, base, te.sensor.to_numpy())
    pred = te[["temporada", "fecha", "fid", "das", "etapa", "sensor", "satelite", "unidad", "tratamiento", "y",
               "tiene_hls"]].assign(pred=p, base=base, indice=ind, temporada_prueba=prueba, **resumen_cfg(gan.cfg))
    # comparaciones que siempre se reportan (después de la medición de lo elegido), en las mismas filas
    comps = []
    if not cronologica:
        h = te_all[te_all.tiene_hls]
        g = S["ganadores"]
        cfgs = dict(g.items())
        if "B1" in g:
            cfgs["B1_mismas_filas"] = Cfg("B1_mismas_filas", g["B1"].cfg.familia, g["B1"].cfg.params, g["B1"].cfg.vars, 0)
        preds_h, preds_all = {}, {}
        for nombre, r in cfgs.items():
            c = r if isinstance(r, Cfg) else r.cfg
            trv = T[(T.temporada.isin(entr)) & mascara(c.variante, T)]
            if len(h):
                preds_h[nombre] = (ajustar_predecir(c, trv, h), float(trv.y.mean()))
            if c.variante in ("B1", "B1_sin_riego"):
                preds_all[nombre] = (ajustar_predecir(c, trv, te_all), float(trv.y.mean()))
        for filas, preds, tabla in (("con HLS", preds_h, h), ("todas", preds_all, te_all)):
            for nombre, (pp, b) in preds.items():
                comps.append({"indice": ind, "temporada_prueba": prueba, "filas": filas, "modelo": nombre,
                              **resumen_cfg(cfgs[nombre] if isinstance(cfgs[nombre], Cfg) else cfgs[nombre].cfg),
                              **metricas(tabla.y, pp, tabla.fid, b, tabla.sensor.to_numpy()),
                              "_pred": pp, "_y": tabla.y.to_numpy(), "_fid": tabla.fid.to_numpy()})
    return {"ind": ind, "prueba": prueba, "cronologica": cronologica, "S": S, "met": met, "pred": pred,
            "comps": comps, "ev_sel": ev_sel, "ev_prueba": ev_prueba, "t_sel_s": t_sel - t0, "base": base}


def tarea_bootstrap(T, ind, prueba, cfg: Cfg, rep: int) -> dict:
    """Bootstrap por bloques móviles de 2 fechas en cada temporada + ruido del dron, reentrenando lo elegido."""
    t0 = time.time()
    rng = _semilla(COD[ind], prueba, 1, rep)
    T = T.copy()
    T["y"] = respuesta(T, ind, curvas_dron(rng))
    entr = [t for t in (1, 2, 3) if t != prueba]
    B = remuestrear(T, (1, 2, 3), rng)
    m = mascara(cfg.variante, B)
    tr, te = B[m & B.temporada.isin(entr).to_numpy()], B[m & (B.temporada == prueba).to_numpy()]
    try:
        p = ajustar_predecir(cfg, tr, te, semilla=rep)
    except ValueError:
        return {"rep": rep, "fallo": 1, "t_s": time.time() - t0}
    b = float(tr.y.mean())
    met = metricas(te.y, p, te.fid, b)
    return {"rep": rep, "fallo": 0, "t_s": time.time() - t0, **{k: met[k] for k in ("rmse_fecha", "rmse", "mae", "r", "rmse_base_fecha", "rmse_base")}}


def tarea_estabilidad(T, ind, prueba, rep: int, n_tie: int, arboles_rf: int) -> dict:
    t0 = time.time()
    rng = _semilla(COD[ind], prueba, 2, rep)
    T = T.copy()
    T["y"] = respuesta(T, ind, curvas_dron(rng))
    entr = [t for t in (1, 2, 3) if t != prueba]
    B = remuestrear(T, entr, rng)
    # B1 sin riego no compite en la selección (solo se reporta), así que no cambia al ganador: se omite aquí
    S = seleccion_completa(B, ind, [(entr[0], entr[1]), (entr[1], entr[0])], n_tie, rng, arboles_rf,
                           variantes=("B1", "B2", "B3"))
    g = S["final"].cfg
    return {"indice": ind, "temporada_prueba": prueba, "rep": rep, **resumen_cfg(g),
            "ganador_B1": S["ganadores"]["B1"].cfg.familia if "B1" in S["ganadores"] else "", "t_s": time.time() - t0}


def paralelo(fn, args_list, n_jobs, etiqueta, cada=50):
    from joblib import Parallel, delayed
    out = []
    gen = Parallel(n_jobs=n_jobs, return_as="generator_unordered", batch_size=1)(delayed(fn)(*a) for a in args_list)
    for i, r in enumerate(gen, 1):
        out.append(r)
        if i % cada == 0 or i == len(args_list):
            latido(f"{etiqueta} {i}/{len(args_list)}")
            log(f"  {etiqueta}: {i}/{len(args_list)}")
    return out


# ================================================================ PASO 1: análisis exploratorio
def paso1_eda(out: Path, D0: pd.DataFrame, calidad: pd.DataFrame, hls_cal: pd.DataFrame | None, tablas: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    eda = out / "eda"
    eda.mkdir(exist_ok=True)
    curvas = curvas_dron()
    dron = pd.read_csv(DATOS / "dron_indices.csv")
    filas = []
    # fechas por temporada y sensor
    for (t, s), g in calidad.groupby(["temporada", "sensor"]):
        filas.append({"tabla": "fechas", "temporada": t, "sensor": s, "fechas_despejadas": g.fecha.nunique(),
                      "fechas_tras_calidad": int((g.motivo == "ok").sum())})
    if hls_cal is not None and len(hls_cal):
        hv = hls_cal[hls_cal.das.between(*VENTANA)]
        for (t, s), g in hv.groupby(["temporada", "coleccion"]):
            filas.append({"tabla": "fechas", "temporada": t, "sensor": s, "fechas_despejadas": g.fecha.nunique(),
                          "fechas_tras_calidad": int((g.motivo == "ok").sum())})
    F = pd.DataFrame(filas)
    fig, ax = plt.subplots(figsize=(7, 3.5))
    piv = F.pivot_table(index="temporada", columns="sensor", values="fechas_tras_calidad", aggfunc="sum").fillna(0)
    piv.plot.bar(ax=ax, rot=0)
    ax.set_ylabel("fechas útiles (días 25–115)")
    ax.set_title("Fechas útiles por temporada y sensor, tras el control de calidad")
    fig.tight_layout()
    fig.savefig(eda / "fechas_por_sensor.png", dpi=120)
    plt.close(fig)
    # series por franja junto al dron, y dispersión satélite vs dron
    D = agregar_clima_riego(D0.reset_index(drop=True))
    for ind in INDICES:
        c = ind.lower()
        fig, axs = plt.subplots(1, 3, figsize=(15, 4), sharey=True)
        for t, ax in zip((1, 2, 3), axs):
            for trat, col in zip(TRAT, ("tab:red", "tab:green", "tab:blue")):
                g = D[(D.temporada == t) & (D.sensor == "S2") & (D.tratamiento == trat)].sort_values("das")
                ax.plot(g.das, g[c], "o-", color=col, alpha=.8, label=f"S2 {trat}")
                bad = g[~g.ok]
                ax.plot(bad.das, bad[c], "x", color="k", ms=8)
                dd = np.arange(25, 116)
                ax.plot(dd, curvas[(t, trat, ind)](dd), "--", color=col, alpha=.6)
                dv = dron[(dron.temporada == t) & (dron.tratamiento == trat) & (dron.indice == ind)]
                ax.plot(dv.etapa.map(DIA_ETAPA), dv.valor, "s", color=col, ms=7, mfc="none")
            ax.set_title(f"GS{t}")
            ax.set_xlabel("días desde la siembra")
        axs[0].set_ylabel(ind)
        axs[0].legend(fontsize=7)
        fig.suptitle(f"{ind}: Sentinel-2 por franja (puntos; x = descartada), dron (cuadros) y su curva PCHIP (rayas)")
        fig.tight_layout()
        fig.savefig(eda / f"series_{c}.png", dpi=110)
        plt.close(fig)
        T = tablas[ind]
        fig, axs = plt.subplots(1, 3, figsize=(13, 4), sharex=True, sharey=True)
        for t, ax in zip((1, 2, 3), axs):
            for s, mk in (("S2", "o"), ("L", "^")):
                g = T[(T.temporada == t) & (T.sensor == s)]
                if len(g):
                    ax.scatter(g[c], g.y, marker=mk, alpha=.7, label=s)
                    if len(g) > 2:
                        filas.append({"tabla": "satelite_vs_dron", "indice": ind, "temporada": t, "sensor": s,
                                      "pares": len(g), "r": float(np.corrcoef(g[c], g.y)[0, 1])})
            ax.set_title(f"GS{t}")
            ax.set_xlabel(f"{ind} satélite")
        axs[0].set_ylabel(f"{ind} dron (referencia)")
        axs[0].legend()
        fig.tight_layout()
        fig.savefig(eda / f"dispersion_{c}.png", dpi=110)
        plt.close(fig)
    # lluvia, ETo y riego por temporada y tratamiento
    cl = pd.read_csv(DATOS / "clima_dia.csv", parse_dates=["fecha"]).set_index("fecha")
    rg = pd.read_csv(DATOS / "nataima_diario.csv")
    fig, axs = plt.subplots(1, 3, figsize=(15, 4), sharey=True)
    for t, ax in zip((1, 2, 3), axs):
        d0 = SIEMBRAS[t]
        dd = pd.date_range(d0, d0 + pd.Timedelta(days=129))
        ax.plot(np.arange(1, 131), cl.loc[dd, "lluvia"].cumsum(), "k-", label="lluvia CHIRPS")
        ax.plot(np.arange(1, 131), cl.loc[dd, "eto"].cumsum(), "k--", label="ETo FAO-56")
        for trat, col in zip(TRAT, ("tab:red", "tab:green", "tab:blue")):
            g = rg[(rg.temporada == t) & (rg.tratamiento == trat)].sort_values("das")
            ax.plot(g.das, g.riego_mm.cumsum(), color=col, label=f"riego {trat}")
            filas.append({"tabla": "agua", "temporada": t, "sensor": trat, "riego_total_mm": g.riego_mm.sum(),
                          "lluvia_chirps_mm": float(cl.loc[dd, "lluvia"].sum()), "eto_mm": float(cl.loc[dd, "eto"].sum())})
        ax.set_title(f"GS{t}")
        ax.set_xlabel("días desde la siembra")
    axs[0].set_ylabel("mm acumulados")
    axs[0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(eda / "agua_por_temporada.png", dpi=110)
    plt.close(fig)
    # correlación entre candidatas
    T = tablas["NDVI"]
    cand = candidatas("NDVI", T)
    C = T[cand + ["y"]].corr()
    C.to_csv(eda / "correlacion_candidatas_ndvi.csv")
    fig, ax = plt.subplots(figsize=(11, 9))
    im = ax.imshow(C, vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(len(C)), C.columns, rotation=90, fontsize=7)
    ax.set_yticks(range(len(C)), C.columns, fontsize=7)
    fig.colorbar(im)
    ax.set_title("Correlación entre candidatas (tabla NDVI; y = NDVI del dron)")
    fig.tight_layout()
    fig.savefig(eda / "correlacion_candidatas.png", dpi=110)
    plt.close(fig)
    # diferencias entre tratamientos: dron vs satélite, por temporada y etapa (respecto a la media del lote)
    dif = []
    S2 = D[(D.sensor == "S2") & D.ok]
    for ind in ("NDVI", "NDRE", "OSAVI"):
        for (t, et), g in S2.groupby(["temporada", "etapa"]):
            sat = g.groupby("tratamiento")[ind.lower()].mean()
            dv = dron[(dron.temporada == t) & (dron.etapa == et) & (dron.indice == ind)].set_index("tratamiento").valor
            for trat in sat.index:
                dif.append({"indice": ind, "temporada": t, "etapa": et, "tratamiento": trat,
                            "dif_satelite": sat[trat] - sat.mean(), "dif_dron": dv[trat] - dv.mean()})
    Dif = pd.DataFrame(dif)
    Dif.to_csv(eda / "diferencias_tratamientos.csv", index=False)
    fig, axs = plt.subplots(1, 3, figsize=(13, 4))
    for ind, ax in zip(("NDVI", "NDRE", "OSAVI"), axs):
        for t, mk in zip((1, 2, 3), ("o", "s", "^")):
            g = Dif[(Dif.indice == ind) & (Dif.temporada == t)]
            ax.scatter(g.dif_dron, g.dif_satelite, marker=mk, label=f"GS{t}")
            if len(g) > 2:
                filas.append({"tabla": "diferencias_tratamientos", "indice": ind, "temporada": t, "pares": len(g),
                              "r": float(np.corrcoef(g.dif_dron, g.dif_satelite)[0, 1])})
        ax.axhline(0, color="gray", lw=.5)
        ax.axvline(0, color="gray", lw=.5)
        ax.set_xlabel("tratamiento − media del lote (dron)")
        ax.set_ylabel("tratamiento − media del lote (Sentinel-2)")
        ax.set_title(ind)
    axs[0].legend()
    fig.tight_layout()
    fig.savefig(eda / "diferencias_tratamientos.png", dpi=110)
    plt.close(fig)
    pd.DataFrame(filas).to_csv(eda / "eda_resumen.csv", index=False)


# ================================================================ PASO 8: figuras
def figuras_prueba(out: Path, P: pd.DataFrame) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axs = plt.subplots(3, 3, figsize=(13, 12))
    for i, ind in enumerate(INDICES):
        for j, t in enumerate((1, 2, 3)):
            ax = axs[i, j]
            g = P[(P.indice == ind) & (P.temporada_prueba == t)]
            for s, mk in (("S2", "o"), ("L", "^")):
                gg = g[g.sensor == s]
                ax.scatter(gg.y, gg.pred, marker=mk, alpha=.7, label=s)
            if len(g):
                lo, hi = min(g.y.min(), g.pred.min()), max(g.y.max(), g.pred.max())
                ax.plot([lo, hi], [lo, hi], "k--", lw=.8)
                ax.axhline(g.base.iloc[0], color="gray", lw=.8, ls=":")
                ax.set_title(f"{ind} · prueba GS{t} · {g.variante.iloc[0]} {g.familia.iloc[0]}", fontsize=9)
            ax.set_xlabel("dron (referencia)")
            ax.set_ylabel("predicho")
    axs[0, 0].legend()
    fig.tight_layout()
    fig.savefig(out / "fig_predicho_vs_dron.png", dpi=110)
    plt.close(fig)
    fig, axs = plt.subplots(3, 3, figsize=(15, 11))
    for i, ind in enumerate(INDICES):
        for j, t in enumerate((1, 2, 3)):
            ax = axs[i, j]
            g = P[(P.indice == ind) & (P.temporada_prueba == t) & (P.sensor == "S2")]
            for trat, col in zip(TRAT, ("tab:red", "tab:green", "tab:blue")):
                gg = g[g.tratamiento == trat].sort_values("das")
                ax.plot(gg.das, gg.y, "--", color=col, alpha=.7)
                ax.plot(gg.das, gg.pred, "o-", color=col, label=trat)
            ax.set_title(f"{ind} · GS{t} (rayas: dron; puntos: predicho)", fontsize=9)
            ax.set_xlabel("días desde la siembra")
    axs[0, 0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(out / "fig_series_franja_prediccion.png", dpi=110)
    plt.close(fig)


# ================================================================ etapa "modelo": pasos 1–8
def ic(v) -> str:
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    return f"{np.percentile(v, 2.5):.3f} a {np.percentile(v, 97.5):.3f}" if len(v) else ""


def finitos(df: pd.DataFrame, nombre: str) -> None:
    num = df.select_dtypes("number")
    if len(num.columns) and not np.isfinite(num.to_numpy(dtype=float)[:, :]).all():
        malas = num.columns[~np.isfinite(num.to_numpy(dtype=float)).all(axis=0)].tolist()
        raise ErrorEstado("ERROR_NUMERICO", f"{nombre}: NaN o infinito en {malas}")


def etapa_modelo(out: Path, a, qa: bool) -> dict:
    n_jobs = a.jobs or os.cpu_count() or 1
    tiempos = {}
    paso = {"n": 0}

    def inicio(n, que):
        paso["n"] = n
        latido(f"paso {n}")
        log(f"===== Paso {n}: {que}")
        tiempos[f"paso{n}_inicio"] = time.time() - T0

    # ---- Prithvi: vectores de la etapa anterior
    emb_p = Path(a.prithvi) if a.prithvi else out / "prithvi_embeddings.csv"
    emb = None
    if emb_p.exists():
        emb = pd.read_csv(emb_p)
    elif qa:
        # QA sin la etapa Prithvi completa: vectores sintéticos SOLO para recorrer el camino de B2/B3 (no se reportan)
        q = pd.read_csv(DATOS / "fechas_despejadas_lote.csv")
        q = q[q.coleccion.str.startswith("hls") & q.despejado & q.das.between(*VENTANA)].copy()
        rng = np.random.default_rng(0)
        emb = q.rename(columns={"id": "item_id"})[["fecha", "coleccion", "item_id"]].reset_index(drop=True)
        emb = pd.concat([emb, pd.DataFrame(rng.normal(size=(len(emb), NPRI)), columns=PCOLS)], axis=1)
        log("QA: vectores Prithvi sintéticos (solo para probar el camino)")
    else:
        raise ErrorEstado("ERROR_PRITHVI", f"falta {emb_p}")
    hls_cal = pd.read_csv(out / "hls_calidad.csv", parse_dates=["fecha"]) if (out / "hls_calidad.csv").exists() else None

    # ---- datos (paso 2 tabular) — se arma antes del EDA porque el EDA los describe
    try:
        tablas, calidad, D0 = construir_tablas(emb)
    except ErrorEstado:
        raise
    except Exception as e:  # noqa: BLE001
        raise ErrorEstado("ERROR_DATOS", f"armando las tablas: {e}") from e

    inicio(1, "análisis exploratorio")
    paso1_eda(out, D0, calidad, hls_cal, tablas)
    subir_parcial(out)

    inicio(2, "calidad y preparación de datos")
    cal = calidad.copy()
    cal["fecha"] = pd.to_datetime(cal.fecha)
    if hls_cal is not None:
        hc = hls_cal.assign(sensor=hls_cal.coleccion.map({"hls2-s30": "HLS-S30", "hls2-l30": "HLS-L30"}))
        cal = pd.concat([cal, hc[["temporada", "fecha", "das", "sensor", "ndvi_lote", "motivo"]]], ignore_index=True)
    cal.sort_values(["temporada", "fecha", "sensor"]).to_csv(out / "calidad_fechas.csv", index=False)
    C = contrato(tablas)
    C.to_csv(out / "contrato_datos.csv", index=False)
    log("contrato de datos OK:\n" + C.to_string(index=False))
    NR = no_regresion(tablas)
    NR.to_csv(out / "no_regresion.csv", index=False)
    log("no regresión OK:\n" + NR.to_string(index=False))
    if not a.sin_red:
        V = verificar_osavi(D0)
        V.to_csv(out / "verificacion_osavi_rojo.csv", index=False)
        log("OSAVI: rojo despejado = rojo de la imagen:\n" + V.to_string(index=False))
    for ind, T in tablas.items():
        T.drop(columns=PCOLS).assign(prithvi_disponible=T.tiene_hls).to_csv(out / f"tabla_limpia_{ind.lower()}.csv",
                                                                             index=False)
    subir_parcial(out)

    inicio(3, "partición anidada por temporada")
    part = []
    for ind, T in tablas.items():
        for prueba in (1, 2, 3):
            entr = [t for t in (1, 2, 3) if t != prueba]
            for tr_t, va_t in ((entr[0], entr[1]), (entr[1], entr[0])):
                for filas, m in (("todas", np.ones(len(T), bool)), ("con HLS", T.tiene_hls.to_numpy())):
                    ftr = T[m & (T.temporada == tr_t).to_numpy()].fid.nunique()
                    fva = T[m & (T.temporada == va_t).to_numpy()].fid.nunique()
                    part.append({"indice": ind, "esquema": "anidado", "temporada_prueba": prueba, "entrena": tr_t,
                                 "valida": va_t, "filas": filas, "fechas_entrena": ftr, "fechas_valida": fva,
                                 "fechas_prueba": T[m & (T.temporada == prueba).to_numpy()].fid.nunique(),
                                 "aviso": "entrenamiento con < 5 fechas" if ftr < 5 else ""})
        for filas, m in (("todas", np.ones(len(T), bool)), ("con HLS", T.tiene_hls.to_numpy())):
            n = {t: T[m & (T.temporada == t).to_numpy()].fid.nunique() for t in (1, 2, 3)}
            part.append({"indice": ind, "esquema": "cronologico", "temporada_prueba": 3, "entrena": 1, "valida": 2,
                         "filas": filas, "fechas_entrena": n[1], "fechas_valida": n[2], "fechas_prueba": n[3],
                         "aviso": "entrenamiento con < 5 fechas" if n[1] < 5 else ""})
    pd.DataFrame(part).to_csv(out / "particiones.csv", index=False)

    inicio(4, "métricas (fijadas antes de ver resultados)")
    (out / "metricas_definicion.json").write_text(json.dumps({
        "principal": "RMSE con peso igual por fecha (la fecha es la unidad independiente); validación = promedio de los "
                     "dos sentidos ponderado por el número de fechas de validación",
        "reporte": ["RMSE sin ponderar", "MAE", "r de Pearson", "mejora % frente a la línea base (media del entrenamiento)"],
        "secundaria": "solo fechas S2 con las 3 franjas: % de fechas en que acierta la franja más baja; tau de Kendall medio",
        "empate": f"IC 95 % de la diferencia de RMSE de validación con {a.n_tie} remuestras por bloques móviles de fechas",
        "simplicidad": "B1 < B2 < B3; lineal < Huber < Ridge < Lasso < splines < RF < XGBoost (< MLP en B3); menos variables"},
        indent=1, ensure_ascii=False), encoding="utf-8")

    inicio(5, "estandarización (dentro del Pipeline, ajustada solo con el entrenamiento)")
    (out / "estandarizacion.txt").write_text(
        "StandardScaler y PCA de Prithvi (k ∈ {3, 5, 10, 20}, recortado a fechas HLS distintas − 1) van dentro de un "
        "Pipeline de scikit-learn (ColumnTransformer → modelo). Se ajustan en cada partición solo con el entrenamiento; "
        "el PCA se ajusta sobre los vectores distintos (una fecha = un vector).\n", encoding="utf-8")

    inicio(6, "selección de variables y paso 7, ajuste y selección de modelos (por temporada de prueba)")
    t = time.time()
    tareas = [(tablas[ind], ind, prueba, a.n_tie, a.arboles_rf, False) for ind in INDICES for prueba in (1, 2, 3)]
    tareas += [(tablas[ind], ind, 3, a.n_tie, a.arboles_rf, True) for ind in INDICES]
    R = paralelo(tarea_principal, tareas, n_jobs, "selección", cada=1)
    tiempos["seleccion_s"] = time.time() - t
    tiempos["seleccion_media_s"] = float(np.mean([r["t_sel_s"] for r in R]))
    R = sorted(R, key=lambda r: (r["cronologica"], INDICES.index(r["ind"]), r["prueba"]))
    principal = [r for r in R if not r["cronologica"]]
    # salidas de los pasos 6 y 7
    vi, sv, sel, imp = [], [], [], []
    for r in principal:
        S = r["S"]
        for f in S["filas_val"]:
            vi.append({"indice": r["ind"], "temporada_prueba": r["prueba"], **f})
        for c in S["caminos"]:
            sv.append({"indice": r["ind"], "temporada_prueba": r["prueba"], **c})
        g = S["final"]
        sel.append({"indice": r["ind"], "temporada_prueba": r["prueba"], **resumen_cfg(g.cfg),
                    "rmse_val": g.rmse_val, "rmse_val_filas_hls": rmse_val_hls(g), "motivo": S["motivo_final"],
                    **{f"ganador_{v}": f"{w.cfg.familia} [{','.join(w.cfg.vars)}{'+P' + str(w.cfg.k) if w.cfg.k else ''}]"
                       for v, w in S["ganadores"].items()},
                    **{f"motivo_{v}": m for v, m in S["motivos"].items()},
                    "evento_1": "selección terminada (sin ver la prueba)", "t_seleccion_ns": r["ev_sel"],
                    "evento_2": "prueba medida una vez", "t_prueba_ns": r["ev_prueba"],
                    "orden_correcto": r["ev_sel"] < r["ev_prueba"]})
    VI = pd.DataFrame(vi)
    VI.to_csv(out / "validacion_interna.csv", index=False)
    pd.DataFrame(sv).to_csv(out / "seleccion_variables.csv", index=False)
    SEL = pd.DataFrame(sel)
    SEL.to_csv(out / "seleccion.csv", index=False)
    log("selección:\n" + SEL[["indice", "temporada_prueba", "variante", "familia", "variables", "k_prithvi", "rmse_val",
                              "motivo"]].to_string(index=False))
    # controles del paso 6: Lasso (con todas las candidatas) e importancia por permutación en validación
    imp = controles_paso6(tablas, principal, a.n_perm)
    pd.DataFrame(imp).to_csv(out / "importancia.csv", index=False)
    subir_parcial(out)

    inicio(8, "evaluación")
    pred = pd.concat([r["pred"] for r in principal], ignore_index=True)
    pred.to_csv(out / "predicciones_prueba.csv", index=False)
    finitos(pred[["y", "pred", "base"]], "predicciones_prueba")
    pr = pd.DataFrame([{"indice": r["ind"], "temporada_prueba": r["prueba"], **resumen_cfg(r["S"]["final"].cfg), **r["met"]}
                       for r in principal])
    pr.to_csv(out / "prueba.csv", index=False)
    finitos(pr[["rmse_fecha", "rmse", "mae", "rmse_base_fecha"]], "prueba")
    log("prueba:\n" + pr[["indice", "temporada_prueba", "variante", "familia", "rmse_fecha", "rmse_base_fecha", "r",
                          "mejora_vs_base_pct", "n_fechas"]].to_string(index=False))
    # comparaciones entre variantes (B2 vs B1, B3 vs B1, con vs sin riego), con IC de la diferencia (bloques, sin reentrenar)
    cv = comparaciones(principal, a.n_boot)
    cv.to_csv(out / "comparacion_variantes.csv", index=False)
    # cronológica
    cr = pd.DataFrame([{"indice": r["ind"], "esquema": "entrena GS1 → valida GS2 → reentrena GS1+GS2 → prueba GS3",
                        **resumen_cfg(r["S"]["final"].cfg), "motivo": r["S"]["motivo_final"], **r["met"]}
                       for r in R if r["cronologica"]])
    cr.to_csv(out / "cronologica.csv", index=False)
    # errores por etapa y sensor
    ee = []
    for (ind, t, et, s), g in pred.groupby(["indice", "temporada_prueba", "etapa", "sensor"]):
        ee.append({"indice": ind, "temporada_prueba": t, "etapa": et, "sensor": s, "n_fechas": g.fid.nunique(),
                   "n_pares": len(g), "rmse_fecha": rmse_fecha(g.y, g.pred, g.fid), "mae": float(np.mean(np.abs(g.y - g.pred))),
                   "sesgo": float(np.mean(g.pred - g.y)), "rmse_base_fecha": rmse_fecha(g.y, g.base, g.fid)})
    pd.DataFrame(ee).to_csv(out / "errores_por_etapa_sensor.csv", index=False)
    figuras_prueba(out, pred)
    subir_parcial(out)
    # bootstrap por bloques + ruido del dron, reentrenando lo elegido
    t = time.time()
    tb = [(tablas[r["ind"]], r["ind"], r["prueba"], r["S"]["final"].cfg, k) for r in principal for k in range(a.n_boot)]
    res_b = paralelo(_boot_envuelto, [(x, ) for x in tb], n_jobs, "bootstrap", cada=50)
    B = pd.DataFrame(res_b)
    tiempos["bootstrap_s"] = time.time() - t
    tiempos["bootstrap_rep_s"] = float(B.t_s.mean())
    bic = []
    for (ind, prueba), g in B[B.fallo == 0].groupby(["indice", "temporada_prueba"]):
        base = pr[(pr.indice == ind) & (pr.temporada_prueba == prueba)].iloc[0]
        bic.append({"indice": ind, "temporada_prueba": prueba, "variante": base.variante, "familia": base.familia,
                    "repeticiones": len(g), "fallidas": int((B[(B.indice == ind) & (B.temporada_prueba == prueba)].fallo == 1).sum()),
                    "rmse_fecha": base.rmse_fecha, "rmse_fecha_IC95": ic(g.rmse_fecha),
                    "rmse": base.rmse, "rmse_IC95": ic(g.rmse), "mae": base.mae, "mae_IC95": ic(g.mae),
                    "r": base.r, "r_IC95": ic(g.r), "rmse_base_fecha": base.rmse_base_fecha,
                    "pct_supera_base": 100 * float(np.mean(g.rmse_fecha < g.rmse_base_fecha))})
    BIC = pd.DataFrame(bic)
    BIC.to_csv(out / "bootstrap_IC.csv", index=False)
    B.to_csv(out / "bootstrap_repeticiones.csv.gz", index=False)
    log("bootstrap:\n" + BIC.to_string(index=False))
    subir_parcial(out)
    # estabilidad de la selección
    t = time.time()
    te_ = [(tablas[ind], ind, prueba, k, a.n_tie, a.arboles_rf_estab) for ind in INDICES for prueba in (1, 2, 3)
           for k in range(a.n_estab)]
    E = pd.DataFrame(paralelo(tarea_estabilidad, te_, n_jobs, "estabilidad", cada=max(1, min(50, len(te_) // 20))))
    tiempos["estabilidad_s"] = time.time() - t
    tiempos["estabilidad_rep_s"] = float(E.t_s.mean())
    E.to_csv(out / "estabilidad_repeticiones.csv", index=False)
    es = E.groupby(["indice", "temporada_prueba", "variante", "familia"]).size().rename("veces").reset_index()
    es["pct"] = 100 * es.veces / es.groupby(["indice", "temporada_prueba"]).veces.transform("sum")
    es = es.merge(SEL[["indice", "temporada_prueba", "variante", "familia"]].assign(es_la_elegida=True),
                  how="left", on=["indice", "temporada_prueba", "variante", "familia"]).fillna({"es_la_elegida": False})
    es.sort_values(["indice", "temporada_prueba", "veces"], ascending=[True, True, False]).to_csv(
        out / "estabilidad_seleccion.csv", index=False)
    subir_parcial(out)
    # modelo de uso: la configuración más elegida, reentrenada con las 3 temporadas
    modelos_uso(out, tablas, principal, E, pr, a)
    tiempos["total_modelo_s"] = time.time() - T0
    (out / ("tiempos_qa.json" if qa else "tiempos.json")).write_text(json.dumps(tiempos, indent=1), encoding="utf-8")
    return {"tiempos": tiempos, "SEL": SEL, "pr": pr, "BIC": BIC, "cv": cv, "cr": cr, "es": es}


def _boot_envuelto(x):
    T, ind, prueba, cfg, k = x
    r = tarea_bootstrap(T, ind, prueba, cfg, k)
    r.update({"indice": ind, "temporada_prueba": prueba})
    return r


def controles_paso6(tablas, principal, n_perm) -> list[dict]:
    from sklearn.linear_model import Lasso
    filas = []
    for r in principal:
        ind, prueba = r["ind"], r["prueba"]
        T = tablas[ind]
        entr = [t for t in (1, 2, 3) if t != prueba]
        dirs = [(entr[0], entr[1]), (entr[1], entr[0])]
        rng = _semilla(COD[ind], prueba, 5)
        # Lasso con todas las candidatas de cada variante (alpha elegido en validación interna), ajustado con las 2 temporadas
        for var in ("B1", "B1_sin_riego", "B2"):
            if var not in r["S"]["ganadores"]:
                continue
            cand = candidatas(ind, T, var != "B1_sin_riego")
            k = r["S"]["ganadores"]["B2"].cfg.k if var == "B2" else 0
            res = [evaluar(Cfg(var, "lasso", (("alpha", float(al)),), tuple(cand), k), T, dirs) for al in np.logspace(-4, 0, 9)]
            res = [x for x in res if x is not None]
            if not res:
                continue
            best = min(res, key=lambda x: x.rmse_val)
            tr = T[T.temporada.isin(entr) & mascara(var, T)]
            m = construir(best.cfg).fit(tr, tr.y.to_numpy())
            coefs = m.named_steps["modelo"].coef_
            nombres = list(cand) + [f"prithvi_pc{i + 1}" for i in range(len(coefs) - len(cand))]
            for n_, c in zip(nombres, coefs):
                filas.append({"indice": ind, "temporada_prueba": prueba, "variante": var, "control": "lasso",
                              "alpha": best.cfg.p()["alpha"], "variable": n_, "valor": float(c), "anulada": bool(abs(c) < 1e-12),
                              "elegida_adelante": n_ in r["S"]["ganadores"][var].cfg.vars})
        # importancia por permutación en validación, del ganador de cada variante
        for var, g in r["S"]["ganadores"].items():
            c = g.cfg
            sub = T[mascara(var, T)]
            bloques = list(c.vars) + (["prithvi"] if c.k else [])
            for tr_t, va_t in dirs:
                tr, va = sub[sub.temporada == tr_t], sub[sub.temporada == va_t]
                if len(tr) < 3 or len(va) == 0:
                    continue
                try:
                    m = construir(c).fit(tr, tr.y.to_numpy())
                except ValueError:
                    continue
                r0 = rmse_fecha(va.y, m.predict(va), va.fid)
                for b in bloques:
                    cols = PCOLS if b == "prithvi" else [b]
                    sube = []
                    for _ in range(n_perm):
                        vp = va.copy()
                        perm = rng.permutation(len(vp))
                        vp[cols] = vp[cols].to_numpy()[perm]
                        sube.append(rmse_fecha(vp.y, m.predict(vp), vp.fid) - r0)
                    filas.append({"indice": ind, "temporada_prueba": prueba, "variante": var, "control": "permutacion",
                                  "familia": c.familia, "entrena": tr_t, "valida": va_t, "variable": b,
                                  "valor": float(np.mean(sube)), "valor_sd": float(np.std(sube))})
    return filas


def comparaciones(principal, n_boot) -> pd.DataFrame:
    filas = []
    pares = [("B2", "B1", "con HLS", "aporte de Prithvi (B1 entrenado con todas sus filas)"),
             ("B2", "B1_mismas_filas", "con HLS", "aporte de Prithvi (mismas filas de entrenamiento)"),
             ("B3", "B1", "con HLS", "Prithvi solo frente a B1"),
             ("B1", "B1_sin_riego", "todas", "aporte del riego"),
             ("B1", "B1_sin_riego", "con HLS", "aporte del riego (filas con HLS)")]
    for r in principal:
        comps = {(c["filas"], c["modelo"]): c for c in r["comps"]}
        for c in r["comps"]:
            filas.append({k: v for k, v in c.items() if not k.startswith("_")} | {"comparacion": "", "dif_rmse_fecha": np.nan})
        rng = _semilla(COD[r["ind"]], r["prueba"], 7)
        for a_, b_, fl, txt in pares:
            if (fl, a_) not in comps or (fl, b_) not in comps:
                continue
            A, Bc = comps[(fl, a_)], comps[(fl, b_)]
            fid = A["_fid"]
            u = np.array(sorted(np.unique(fid)))
            mA = pd.Series((A["_y"] - A["_pred"]) ** 2).groupby(fid).mean()
            mB = pd.Series((Bc["_y"] - Bc["_pred"]) ** 2).groupby(fid).mean()
            difs = []
            for _ in range(n_boot):
                s = bloques_moviles(u, rng)
                difs.append(np.sqrt(mA[s].mean()) - np.sqrt(mB[s].mean()))
            filas.append({"indice": r["ind"], "temporada_prueba": r["prueba"], "filas": fl, "comparacion": txt,
                          "modelo": f"{a_} − {b_}", "dif_rmse_fecha": A["rmse_fecha"] - Bc["rmse_fecha"],
                          "dif_IC95": ic(difs), "n_fechas": len(u)})
    return pd.DataFrame(filas)


def modelos_uso(out: Path, tablas, principal, E, pr, a) -> None:
    import joblib
    import sklearn
    import xgboost
    dest = out / "models" / "corrector_b"
    dest.mkdir(parents=True, exist_ok=True)
    versiones = {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                 "scikit-learn": sklearn.__version__, "xgboost": xgboost.__version__, "joblib": joblib.__version__}
    entradas = {p.name: sha256(p) for p in sorted(DATOS.iterdir()) if p.is_file()}
    emb_p = out / "prithvi_embeddings.csv"
    if emb_p.exists():
        entradas[emb_p.name] = sha256(emb_p)
    for ind in INDICES:
        rs = [r for r in principal if r["ind"] == ind]
        conteo = pd.Series([r["S"]["final"].cfg.clave() for r in rs]).value_counts()
        cfgs = {r["S"]["final"].cfg.clave(): r["S"]["final"].cfg for r in rs}
        est = E[E.indice == ind].groupby(["variante", "familia"]).size()
        top = conteo[conteo == conteo.max()].index.tolist()
        elegida = min(top, key=lambda k: (-est.get((cfgs[k].variante, cfgs[k].familia), 0), cfgs[k].simplicidad()))
        cfg = cfgs[elegida]
        T = tablas[ind]
        tr = T[mascara(cfg.variante, T)]
        m = construir(cfg).fit(tr, tr.y.to_numpy())
        joblib.dump(m, dest / f"{ind.lower()}.joblib")
        pp = pr[pr.indice == ind]
        meta = {"indice": ind, **resumen_cfg(cfg), "params": cfg.p(), "variables": list(cfg.vars),
                "regla": "la configuración elegida en más temporadas de prueba; empate → la más estable; luego la más simple",
                "veces_elegida": int(conteo.max()), "de": len(rs), "entrenado_con": "GS1 + GS2 + GS3",
                "filas_entrenamiento": int(len(tr)), "fechas_entrenamiento": int(tr.fid.nunique()),
                "entradas_requeridas": list(cfg.vars) + (["prithvi_1024 (HLS del mismo día ±1)"] if cfg.k else []),
                "error_esperado": {"descripcion": "RMSE de prueba (peso igual por fecha) de la validación cruzada anidada",
                                   **{f"GS{int(x.temporada_prueba)}": round(float(x.rmse_fecha), 4) for x in pp.itertuples()},
                                   "media": round(float(pp.rmse_fecha.mean()), 4)},
                "versiones": versiones, "sha256_entradas": entradas,
                "git_commit": os.environ.get("SIRA_GIT_COMMIT", ""), "semilla": SEMILLA}
        (dest / f"{ind.lower()}_meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


# ================================================================ QA: tiempos y proyección
def eficiencia_paralela(n_jobs: int) -> float:
    from joblib import Parallel, delayed

    def carga(_):
        from sklearn.ensemble import RandomForestRegressor
        rng = np.random.default_rng(0)
        X = rng.normal(size=(100, 4))
        t = time.time()
        for _ in range(3):
            RandomForestRegressor(200, random_state=0, n_jobs=1).fit(X, X[:, 0])
        return time.time() - t

    t1 = carga(0)
    t = time.time()
    Parallel(n_jobs=n_jobs)(delayed(carga)(i) for i in range(n_jobs))
    tp = time.time() - t
    return float(min(1.0, t1 / tp))  # fracción del ideal (n_jobs veces más rápido)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out")
    ap.add_argument("--qa", action="store_true")
    ap.add_argument("--etapa", choices=["prithvi", "modelo"], default=None)
    ap.add_argument("--prithvi", default=None, help="prithvi_embeddings.csv ya calculado")
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--n-estab", type=int, default=200)
    ap.add_argument("--n-tie", type=int, default=200)
    ap.add_argument("--n-perm", type=int, default=30)
    ap.add_argument("--arboles-rf", type=int, default=500)
    ap.add_argument("--arboles-rf-estab", type=int, default=500)
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--sin-red", action="store_true", help="omite la verificación del rojo contra la imagen original")
    ap.add_argument("--sin-prithvi-qa", action="store_true", help="QA sin la prueba de Prithvi en 1 fecha")
    ap.add_argument("--tope-min", type=float, default=90.0, help="la QA para si la proyección supera este total")
    ap.add_argument("--tope-entrenamiento-min", type=float, default=45.0,
                    help="la QA para si el entrenamiento proyectado no cabe en su tope (50 min) con margen")
    ap.add_argument("--min-previos", type=float, default=25.0, help="arranque + instalación + QA (min)")
    ap.add_argument("--prithvi-items", type=int, default=0, help="QA: n.º de imágenes HLS a bajar (para proyectar)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SIRA_LATIDO", str(out / "latido"))
    latido("inicio")
    estado, paso_n = "ERROR_PASO0", 0
    try:
        if a.qa:
            n_jobs = a.jobs or os.cpu_count() or 1
            info_p = None
            if not a.sin_prithvi_qa:
                log("QA: Prithvi en 1 fecha")
                info_p = etapa_prithvi(out, qa=True)
            full = {"n_boot": a.n_boot, "n_estab": a.n_estab}  # repeticiones de la corrida completa
            a.n_boot, a.n_estab, a.n_tie, a.n_perm = 4, 1, 20, 3
            res = etapa_modelo(out, a, qa=True)
            ti = res["tiempos"]
            ef = eficiencia_paralela(n_jobs)
            ef_n = max(1.0, n_jobs * ef)
            t_pri = 0.0
            if info_p:
                t_pri = (info_p["carga_modelo_s"] + info_p["items_hls"] * info_p["descarga_media_s"]
                         + info_p["objetivos_total"] * info_p["prithvi_medio_s"])
            proy = {"n_jobs": n_jobs, "eficiencia_paralela": ef, "nucleos_efectivos": ef_n,
                    "prithvi_1_fecha_s": info_p["prithvi_medio_s"] if info_p else None,
                    "descarga_hls_media_s": info_p["descarga_media_s"] if info_p else None,
                    "prithvi_total_s": t_pri,
                    "seleccion_completa_media_s": ti["seleccion_media_s"],
                    "seleccion_principal_s": ti["seleccion_s"],
                    "bootstrap_1_rep_s": ti["bootstrap_rep_s"],
                    "bootstrap_s": ti["bootstrap_rep_s"] * 9 * full["n_boot"] / ef_n,
                    "estabilidad_1_rep_s": ti["estabilidad_rep_s"],
                    "estabilidad_s": ti["estabilidad_rep_s"] * 9 * full["n_estab"] / ef_n,
                    "resto_s": 180.0, "reps": full}
            proy["entrenamiento_min"] = (proy["seleccion_principal_s"] + proy["bootstrap_s"] + proy["estabilidad_s"]
                                         + proy["resto_s"]) / 60
            proy["total_min"] = proy["entrenamiento_min"] + proy["prithvi_total_s"] / 60 + a.min_previos
            (out / "proyeccion.json").write_text(json.dumps(proy, indent=1), encoding="utf-8")
            log("proyección:\n" + json.dumps(proy, indent=1))
            if proy["total_min"] > a.tope_min or proy["entrenamiento_min"] > a.tope_entrenamiento_min:
                estado = "ESTIMADO_EXCEDE"
                log(f"ESTIMADO_EXCEDE: total {proy['total_min']:.0f} min (tope {a.tope_min:.0f}); "
                    f"entrenamiento {proy['entrenamiento_min']:.0f} min (tope {a.tope_entrenamiento_min:.0f})")
            else:
                estado = "QA_OK"
                print("QA OK", flush=True)
        elif a.etapa == "prithvi":
            paso_n = 2
            etapa_prithvi(out, qa=False)
            estado = "OK"
        elif a.etapa == "modelo":
            etapa_modelo(out, a, qa=False)
            estado = "OK"
        else:
            ap.error("falta --qa o --etapa")
    except ErrorEstado as e:
        estado = e.estado
        log(f"{estado}: {e}\n{traceback.format_exc()}")
    except Exception:  # noqa: BLE001
        n = 0
        try:
            n = int(Path(os.environ["SIRA_LATIDO"]).read_text().split("paso ")[1].split()[0])
        except Exception:  # noqa: BLE001
            n = paso_n
        estado = f"ERROR_PASO{n}"
        log(f"{estado}:\n{traceback.format_exc()}")
    (out / "estado_job.txt").write_text(estado + "\n", encoding="utf-8")
    log(f"estado final: {estado}")
    subir_parcial(out)
    return 0 if estado in ("OK", "QA_OK") else 3


if __name__ == "__main__":
    sys.exit(main())
