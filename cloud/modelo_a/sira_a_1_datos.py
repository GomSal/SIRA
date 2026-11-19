# %% [markdown]
# # SIRA · Modelo predictor (A) · Notebook 1: datos
#
# Baja y limpia todo lo que necesita el entrenamiento del modelo predictor (A). **Corre en CPU con internet.**
#
# | Bloque | Fuente | Qué guarda |
# |---|---|---|
# | Imágenes | Sentinel-2 L2A + Landsat 7/8/9 C2 L2 (Planetary Computer), nov-2012 → nov-2026 | NDVI, NDMI y azul por pixel de 10 m en una ventana de 5 × 5 km alrededor de Nataima, con la máscara de nubes ya aplicada |
# | HLS | HLS v2 S30/L30 (Planetary Computer) | recorte de 224 × 224 px de 30 m para Prithvi (relleno de huecos y resumen) |
# | Clima observado | NASA POWER (ETo FAO-56) + CHIRPS (ClimateSERV), 2001 → 2026 | `clima.csv` |
# | Pronóstico | GEOS-S2S-3 NRT (NASA GMAO, portal NCCS), 5 miembros, celda del lote | `geos_nrt.csv` |
# | Climatología del modelo | GEOS-S2S-3 Drift (hindcasts 2001–2020) | `geos_drift.csv` |
# | ONI | NOAA CPC | `oni.csv` |
#
# Salida: `/kaggle/working/datos_a/` (en local: `./salida_a1/`). Con `SIRA_QA=1` corre una prueba corta.

# %%
import os
import subprocess
import sys

QA = os.environ.get("SIRA_QA", "0") == "1"
EN_KAGGLE = os.path.exists("/kaggle/working")
if EN_KAGGLE:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "planetary-computer==1.0.0", "pystac-client==0.9.1",
                    "rasterio", "h5py", "fsspec", "aiohttp", "requests"], check=True)

# %%
# Tiempos máximos de red: sin esto, una conexión que se queda colgada detiene el notebook para siempre.
os.environ.update({"GDAL_HTTP_TIMEOUT": "60", "GDAL_HTTP_CONNECTTIMEOUT": "20", "GDAL_HTTP_MAX_RETRY": "3",
                   "GDAL_HTTP_RETRY_DELAY": "5", "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
                   "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff"})
import socket  # noqa: E402

socket.setdefaulttimeout(120)
import concurrent.futures as cf
import datetime as dt
import io
import json
import re
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("/kaggle/working/datos_a") if EN_KAGGLE else Path(os.environ.get("SIRA_OUT_A1", "salida_a1"))
OUT.mkdir(parents=True, exist_ok=True)
LOG = open(OUT / "log_datos.txt", "a", encoding="utf-8")
T0 = time.time()


def log(m):
    s = f"[{dt.datetime.utcnow():%H:%M:%S} +{time.time() - T0:6.0f}s] {m}"
    print(s, flush=True)
    LOG.write(s + "\n")
    LOG.flush()


# Reanudar: si una versión anterior de este notebook dejó resultados y se agregaron como entrada (Add Input →
# Notebook Output), se copian aquí y cada etapa ya terminada se salta. Lo mismo en local si la carpeta ya existe.
if EN_KAGGLE:
    import glob
    import shutil
    for prev in glob.glob("/kaggle/input/**/datos_a", recursive=True):
        for f in Path(prev).rglob("*"):
            dst = OUT / f.relative_to(prev)
            if f.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
        log(f"reanudar: copiado lo de {prev}")


def hecho(*nombres):
    return all((OUT / n).exists() for n in nombres)


LON, LAT = -74.9621, 4.1895
CRS = "EPSG:32618"
MEDIA_M = 300 if QA else 2500          # ventana de 5 × 5 km (0,6 km en QA)
INI, FIN = "2012-11-01", "2026-11-18"  # 10 años antes de la 1.ª validación (nov-2022) hasta hoy
HILOS = 16
BAJAR_GEOS = os.environ.get("SIRA_GEOS", "0") == "1"  # en Kaggle no: desde allá el portal de la NASA se atasca
log(f"inicio · QA={QA} · Kaggle={EN_KAGGLE} · ventana {2 * MEDIA_M} m · {INI} → {FIN}")

# %% [markdown]
# ## 1. Imágenes Sentinel-2 y Landsat → NDVI, NDMI y azul por pixel de 10 m
# Calidad (igual que el Modelo B, `cloud/ronda1_b/job_b.py`):
# - Sentinel-2: clase SCL ∈ {4 vegetación, 5 suelo, 6 agua}; offset −1000 desde el baseline 04.00.
# - Landsat: QA_PIXEL bits 0–4 = 0 y bit 6 (despejado) o 7 (agua).
# - Reflectancias > 0: quita el bandeado de Landsat 7 SLC-off y los bordes sin dato.
#
# Landsat (30 m) se lleva a la rejilla de 10 m de Sentinel-2 por vecino más cercano. La bruma y la calibración
# Landsat → Sentinel-2 se hacen en el notebook 2, con los datos ya juntos.

# %%
import planetary_computer as pc
import pystac_client
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

x0, y0 = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(LON, LAT)
x0, y0 = round(x0 / 30) * 30, round(y0 / 30) * 30
N = 2 * MEDIA_M // 10
TRF = rasterio.transform.from_origin(x0 - MEDIA_M, y0 + MEDIA_M, 10, 10)


def catalogo():
    return pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")  # se firma cada URL justo al leerla (los permisos vencen)


cat = catalogo()
pt = {"type": "Point", "coordinates": [LON, LAT]}
s2 = list(cat.search(collections=["sentinel-2-l2a"], intersects=pt, datetime=f"{INI}/{FIN}").items())
s2.sort(key=lambda i: (i.datetime.date(), i.properties.get("s2:mgrs_tile") != "18NWK"))
vistos, s2u = set(), []
for i in s2:
    if i.datetime.date() not in vistos:
        vistos.add(i.datetime.date())
        s2u.append(i)
ls = [i for i in cat.search(collections=["landsat-c2-l2"], intersects=pt, datetime=f"{INI}/{FIN}").items()
      if i.properties.get("platform") in ("landsat-7", "landsat-8", "landsat-9")]
items = s2u + ls
if QA and os.environ.get("SIRA_QA_TODAS", "0") != "1":  # QA corta; con SIRA_QA_TODAS=1, todas las fechas en 0,6 km
    items = s2u[-6:] + ls[-3:] + [i for i in s2u if i.datetime.year == 2021][:3]
log(f"imágenes: {len(s2u)} fechas Sentinel-2 · {len(ls)} escenas Landsat 7/8/9 · rejilla {N}×{N} de 10 m")


def leer(href, remuestreo=Resampling.nearest):
    for k in range(5):
        try:
            with rasterio.open(pc.sign(href)) as src, WarpedVRT(src, crs=CRS, transform=TRF, width=N, height=N,
                                                      resampling=remuestreo, nodata=0) as v:
                return v.read(1).astype(np.float32)
        except Exception:  # noqa: BLE001
            if k == 4:
                raise
            time.sleep(5 * (k + 1))


def procesar(it):
    p = it.properties
    if it.collection_id == "sentinel-2-l2a":
        off = -1000.0 if str(p.get("s2:processing_baseline", "00.00")) >= "04.00" else 0.0
        r = {b: leer(it.assets[b].href) for b in ("B02", "B04", "B08", "B8A", "B11", "SCL")}
        ok = np.isin(r["SCL"], [4, 5, 6])
        R = {b: (r[b] + off) / 1e4 for b in ("B02", "B04", "B08", "B8A", "B11")}
        for b in R:
            ok &= (r[b] > 0) & (R[b] > 0)
        ndvi = (R["B08"] - R["B04"]) / (R["B08"] + R["B04"])
        ndmi = (R["B8A"] - R["B11"]) / (R["B8A"] + R["B11"])
        azul = R["B02"]
        sensor = "S2"
    else:
        r = {b: leer(it.assets[b].href) for b in ("blue", "red", "nir08", "swir16", "qa_pixel")}
        q = r["qa_pixel"].astype(np.int64)
        ok = ((q & 0b11111) == 0) & ((((q >> 6) & 1) | ((q >> 7) & 1)) == 1) & (q > 0)
        R = {b: r[b] * 0.0000275 - 0.2 for b in ("blue", "red", "nir08", "swir16")}
        for b in R:
            ok &= (r[b] > 0) & (R[b] > 0)
        ndvi = (R["nir08"] - R["red"]) / (R["nir08"] + R["red"])
        ndmi = (R["nir08"] - R["swir16"]) / (R["nir08"] + R["swir16"])
        azul = R["blue"]
        sensor = "L"
    ok &= np.isfinite(ndvi) & np.isfinite(ndmi) & (np.abs(ndvi) < 1) & (np.abs(ndmi) < 1)
    f = lambda z: np.where(ok, z, np.nan).astype(np.float16).ravel()  # noqa: E731
    return dict(id=it.id, fecha=it.datetime.strftime("%Y-%m-%d"), sensor=sensor, plataforma=p.get("platform"),
                nube_escena=p.get("eo:cloud_cover"), ndvi=f(ndvi), ndmi=f(ndmi), azul=f(azul))


if not (hecho("cubo.npz", "cubo_fechas.csv")):
    res, fallos = [], []
    with cf.ThreadPoolExecutor(HILOS) as ex:
        fut = {ex.submit(procesar, it): it for it in items}
        for k, fu in enumerate(cf.as_completed(fut), 1):
            try:
                res.append(fu.result())
            except Exception as e:  # noqa: BLE001
                fallos.append({"id": fut[fu].id, "error": re.sub(r"\?\S*", "?<token>", str(e))[:300]})
            if k % 50 == 0 or k == len(items):
                log(f"imágenes {k}/{len(items)} · fallos {len(fallos)}")
    res.sort(key=lambda d: (d["fecha"], d["sensor"], d["id"]))
    meta = pd.DataFrame([{k: v for k, v in d.items() if k not in ("ndvi", "ndmi", "azul")} for d in res])
    meta["pixeles_validos"] = [int(np.isfinite(d["ndvi"].astype(np.float32)).sum()) for d in res]
    np.savez_compressed(OUT / "cubo.npz", ndvi=np.stack([d["ndvi"] for d in res]), ndmi=np.stack([d["ndmi"] for d in res]),
                        azul=np.stack([d["azul"] for d in res]), n=N, x0=x0 - MEDIA_M, y0=y0 + MEDIA_M, res=10)
    meta.to_csv(OUT / "cubo_fechas.csv", index=False)
    pd.DataFrame(fallos, columns=["id", "error"]).to_csv(OUT / "cubo_fallos.csv", index=False)
    del res
    if len(fallos) > 0.05 * len(items):
        raise RuntimeError(f"demasiadas imágenes fallidas: {len(fallos)} de {len(items)}")
    log(f"imágenes listas: {len(meta)} · fallos {len(fallos)}")
else:
    meta = pd.read_csv(OUT / "cubo_fechas.csv")
    fallos = pd.read_csv(OUT / "cubo_fallos.csv").to_dict("records") if hecho("cubo_fallos.csv") else []
    log(f"imágenes: ya estaban ({len(meta)}), se reutilizan")


# %% [markdown]
# ## 2. HLS para Prithvi (recorte de 224 × 224 px de 30 m centrado en el lote)

# %%
HLS_BANDAS = {"hls2-s30": ["B02", "B03", "B04", "B8A", "B11", "B12"], "hls2-l30": ["B02", "B03", "B04", "B05", "B06", "B07"]}
LADO = 224
(OUT / "hls").mkdir(exist_ok=True)
hx, hy = round(x0 / 30) * 30, round(y0 / 30) * 30
TRF_HLS = rasterio.transform.from_origin(hx - LADO // 2 * 30, hy + LADO // 2 * 30, 30, 30)


def bajar_hls(it):
    arr = []
    for b in HLS_BANDAS[it.collection_id] + ["Fmask"]:
        for k in range(5):
            try:
                with rasterio.open(pc.sign(it.assets[b].href)) as src, WarpedVRT(
                        src, crs=CRS, transform=TRF_HLS, width=LADO, height=LADO, resampling=Resampling.nearest,
                        nodata=(255 if b == "Fmask" else -9999)) as v:
                    arr.append(v.read(1))
                break
            except Exception:  # noqa: BLE001
                if k == 4:
                    raise
                time.sleep(5 * (k + 1))
    bandas, fm = np.stack(arr[:6]).astype(np.int16), arr[6].astype(np.uint8)
    limpio = (fm != 255) & ((fm & 0b1110) == 0) & (bandas[0] != -9999)
    if limpio.mean() < 0.05:
        return None
    np.savez_compressed(OUT / "hls" / f"{it.id}.npz", b=bandas, f=fm)
    return {"id": it.id, "coleccion": it.collection_id, "fecha": it.datetime.strftime("%Y-%m-%d"),
            "frac_despejado": float(limpio.mean())}


hls_items = []
for c in ("hls2-s30", "hls2-l30"):
    hls_items += list(cat.search(collections=[c], intersects=pt, datetime=f"{INI}/{FIN}").items())
hls_items.sort(key=lambda i: (i.datetime.date(), i.collection_id))
if QA:
    hls_items = hls_items[-4:] if os.environ.get("SIRA_QA_TODAS", "0") != "1" else hls_items[-60:]
if not (hecho("hls_fechas.csv", "hls_rejilla.json")):
    hls_meta, hls_fallos = [], []
    with cf.ThreadPoolExecutor(HILOS) as ex:
        fut = {ex.submit(bajar_hls, it): it for it in hls_items}
        for k, fu in enumerate(cf.as_completed(fut), 1):
            try:
                r = fu.result()
                if r:
                    hls_meta.append(r)
            except Exception as e:  # noqa: BLE001
                hls_fallos.append({"id": fut[fu].id, "error": re.sub(r"\?\S*", "?<token>", str(e))[:300]})
            if k % 100 == 0 or k == len(hls_items):
                log(f"HLS {k}/{len(hls_items)} · guardados {len(hls_meta)} · fallos {len(hls_fallos)}")
    pd.DataFrame(hls_meta).sort_values("fecha").to_csv(OUT / "hls_fechas.csv", index=False)
    pd.DataFrame(hls_fallos, columns=["id", "error"]).to_csv(OUT / "hls_fallos.csv", index=False)
    json.dump({"transform": list(TRF_HLS)[:6], "lado": LADO, "crs": CRS}, open(OUT / "hls_rejilla.json", "w", encoding="utf-8"))
else:
    hls_meta = pd.read_csv(OUT / "hls_fechas.csv").to_dict("records")
    log(f"HLS: ya estaba ({len(hls_meta)} recortes), se reutiliza")


# %% [markdown]
# ## 3. Clima observado: NASA POWER (ETo FAO-56) y CHIRPS (ClimateSERV)

# %%
ELEV_M = 323.0


def eto_fao56(tmax, tmin, rh, u2, rs, doy, lat_deg=LAT, z=ELEV_M):
    """Igual que cloud/ronda1_b/job_b.py (Modelo B)."""
    tmean = (tmax + tmin) / 2
    P = 101.3 * ((293 - 0.0065 * z) / 293) ** 5.26
    gamma = 0.000665 * P
    es_f = lambda t: 0.6108 * np.exp(17.27 * t / (t + 237.3))  # noqa: E731
    es = (es_f(tmax) + es_f(tmin)) / 2
    ea = rh / 100 * es
    delta = 4098 * es_f(tmean) / (tmean + 237.3) ** 2
    phi = np.radians(lat_deg)
    dr = 1 + 0.033 * np.cos(2 * np.pi * doy / 365)
    dec = 0.409 * np.sin(2 * np.pi * doy / 365 - 1.39)
    ws = np.arccos(-np.tan(phi) * np.tan(dec))
    Ra = 24 * 60 / np.pi * 0.0820 * dr * (ws * np.sin(phi) * np.sin(dec) + np.cos(phi) * np.cos(dec) * np.sin(ws))
    Rso = (0.75 + 2e-5 * z) * Ra
    Rnl = 4.903e-9 * (((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4) / 2) * (0.34 - 0.14 * np.sqrt(ea)) * \
        (1.35 * np.clip(rs / Rso, 0.25, 1.0) - 0.35)
    Rn = 0.77 * rs - Rnl
    return (0.408 * delta * Rn + gamma * 900 / (tmean + 273) * u2 * (es - ea)) / (delta + gamma * (1 + 0.34 * u2)), Ra


def get_json(url, t=300):
    for k in range(6):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=t).read())
        except Exception:  # noqa: BLE001
            if k == 5:
                raise
            time.sleep(10 * (k + 1))


if not (hecho("clima.csv")):
    par = "T2M_MAX,T2M_MIN,RH2M,WS2M,ALLSKY_SFC_SW_DWN,PRECTOTCORR"
    p = get_json(f"https://power.larc.nasa.gov/api/temporal/daily/point?parameters={par}&community=AG&longitude={LON}"
                 f"&latitude={LAT}&start=20010101&end={FIN.replace('-', '')}&format=JSON")["properties"]["parameter"]
    cl = pd.DataFrame({k: pd.Series(v) for k, v in p.items()})
    cl.index = pd.to_datetime(cl.index, format="%Y%m%d")
    cl = cl.replace(-999.0, np.nan)
    cl["eto"], cl["Ra"] = eto_fao56(cl.T2M_MAX, cl.T2M_MIN, cl.RH2M, cl.WS2M, cl.ALLSKY_SFC_SW_DWN, cl.index.day_of_year.to_numpy())
    d = 0.025
    geom = json.dumps({"type": "Polygon", "coordinates": [[[LON - d, LAT - d], [LON + d, LAT - d], [LON + d, LAT + d],
                                                            [LON - d, LAT + d], [LON - d, LAT - d]]]})
    base = "https://climateserv.servirglobal.net/api/"
    lluvia = {}
    for a0 in range(2001, 2027, 6):
        a1 = min(a0 + 5, 2026)
        q = urllib.parse.urlencode({"datatype": 0, "begintime": f"01/01/{a0}",
                                    "endtime": f"12/31/{a1}" if a1 < 2026 else pd.Timestamp(FIN).strftime("%m/%d/%Y"),
                                    "intervaltype": 0, "operationtype": 5, "geometry": geom})
        rid = get_json(base + "submitDataRequest/?" + q)[0]
        for _ in range(120):  # máximo 10 min por tramo
            prog = get_json(base + f"getDataRequestProgress/?id={rid}")[0]
            if prog in (100, 100.0, -1):
                break
            time.sleep(5)
        for x in get_json(base + f"getDataFromRequest/?id={rid}", t=900)["data"]:
            fx = pd.Timestamp(x["date"]) if "date" in x else pd.Timestamp(x["year"], x["month"], x["day"])
            lluvia[fx] = x["value"]["avg"]
        log(f"CHIRPS {a0}–{a1} listo")
    cl["lluvia"] = pd.Series(lluvia).sort_index().reindex(cl.index)
    cl.loc[cl.lluvia < -1, "lluvia"] = np.nan
    cl.index.name = "fecha"
    cl.to_csv(OUT / "clima.csv")
    log(f"clima: POWER {cl.index.min().date()}→{cl.index.max().date()} · CHIRPS hasta {cl.lluvia.last_valid_index().date()}")
else:
    cl = pd.read_csv(OUT / "clima.csv", parse_dates=["fecha"]).set_index("fecha")
    log("clima: ya estaba, se reutiliza")


# %% [markdown]
# ## 4. GEOS-S2S-3: pronósticos reales (NRT) y climatología de los hindcasts (Drift), en la celda del lote

# %%
import fsspec
import h5py

GEOS = "https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3"
import aiohttp  # noqa: E402

fs = fsspec.filesystem("https", client_kwargs={"timeout": aiohttp.ClientTimeout(total=180, sock_read=60)})


def listar(url, patron):
    html = urllib.request.urlopen(url, timeout=120).read().decode("utf-8", "ignore")
    return sorted(set(re.findall(patron, html)))


def capa(url, con_ref=False):
    """Lee SOLO la cabecera HDF5 de un archivo: dónde empieza cada día en bytes y qué celda es la del lote.
    Los archivos son float32 sin comprimir con un bloque por día (1, 361, 720), o contiguos (Drift)."""
    for k in range(5):
        try:
            with fs.open(url, block_size=2 ** 16, cache_type="bytes") as f, h5py.File(f, "r") as h:
                var = [q for q in h if h[q].ndim == 3][0]
                d = h[var]
                nt, ny, nx = d.shape
                assert d.dtype.str == "<f4" and d.compression is None, (d.dtype, d.compression)
                if d.chunks is None:
                    base, paso = d.id.get_offset(), ny * nx * 4
                else:
                    assert tuple(d.chunks) == (1, ny, nx), d.chunks
                    base = d.id.get_chunk_info(0).byte_offset
                    paso = d.id.get_chunk_info(1).byte_offset - base
                lat, lon = h["lat"][:], h["lon"][:]
                i, j = int(np.argmin(np.abs(lat - LAT))), int(np.argmin(np.abs(lon - LON)))
                u = h["time"].attrs["units"]
                u = u.decode() if isinstance(u, bytes) else u
                t0 = dt.datetime.strptime(u.split("since ")[1][:19], "%Y-%m-%d %H:%M:%S")
                t1 = float(h["time"][0])
                ref = [float(x) for x in d[:3, i, j]] if con_ref else None  # leer con h5py es lento (~40 s)
                return {"var": var, "base": base, "paso": paso, "i": i, "j": j, "nx": nx, "nt": nt,
                        "t0": t0 + dt.timedelta(minutes=t1), "lat": float(lat[i]), "lon": float(lon[j]), "ref": ref}
        except Exception:  # noqa: BLE001
            if k == 4:
                raise
            time.sleep(5 * (k + 1))


def leer_rangos(url, pos):
    """Una sola petición HTTP con varios rangos de 4 bytes (uno por día): segundos en vez de minutos."""
    for k in range(6):
        try:
            req = urllib.request.Request(url, headers={"Range": "bytes=" + ",".join(f"{q}-{q + 3}" for q in pos)})
            with urllib.request.urlopen(req, timeout=120) as r:
                cuerpo, ct, st = r.read(), r.headers.get("Content-Type", ""), r.status
            if st == 206 and "multipart" in ct:
                vals = {int(m.group(1)): np.frombuffer(m.group(2), "<f4")[0]
                        for m in re.finditer(rb"Content-Range: bytes (\d+)-\d+/\d+\r\n\r\n(.{4})", cuerpo, flags=re.S)}
                return np.array([vals[q] for q in pos], float)
            if st == 206 and len(pos) == 1:
                return np.frombuffer(cuerpo[:4], "<f4").astype(float)
            raise RuntimeError(f"respuesta inesperada {st} {ct}")
        except Exception:  # noqa: BLE001
            if k == 5:
                raise
            time.sleep(5 * (k + 1))


CAPAS = {}


def posiciones(c, n):
    return [c["base"] + k * c["paso"] + (c["i"] * c["nx"] + c["j"]) * 4 for k in range(min(n, c["nt"]))]


def serie(url, clave, dias):
    """Serie diaria de la celda del lote: cabecera propia de cada archivo (los de 2025 y 2026 tienen distinta
    estructura interna) + una sola petición con los rangos de cada día."""
    c = capa(url)  # posiciones exactas desde la cabecera de ESTE archivo
    v = leer_rangos(url, posiciones(c, dias))
    return c, v


def verificar(url, clave):
    """Lectura por rangos frente a h5py en un archivo de cada tipo; se detiene si no coincide."""
    c = capa(url, con_ref=True)
    CAPAS[clave] = c
    v = leer_rangos(url, posiciones(c, 3))
    assert np.allclose(v, c["ref"], rtol=1e-6), (url, v, c["ref"])
    log(f"lectura por rangos verificada contra h5py: {url.split('/')[-1]} · celda {c['lat']}, {c['lon']}")


# GEOS-S2S no detiene el notebook: si algo falla, queda en el log y lo demás se guarda igual.
inits, G, fallos_g = [], pd.DataFrame(), []
try:
    if not BAJAR_GEOS:
        raise RuntimeError("GEOS-S2S se baja aparte (scripts/modelo_a/bajar_geos.py) y el notebook 2 lo toma del repositorio")
    VARS_NRT = {"pr_sfc": "PRECTOTCORR", "t2mmax": "T2MMAX", "t2mmin": "T2MMIN"}
    inits = [("NRT/SubC/backup", x) for x in listar(f"{GEOS}/NRT/SubC/backup/", r'href="(20\d{6})/"')] + \
            [("NRT/SubC", x) for x in listar(f"{GEOS}/NRT/SubC/", r'href="(20\d{6})/"')]
    if QA:
        inits = inits[-2:] if os.environ.get("SIRA_QA_TODAS", "0") != "1" else inits[:2] + [x for x in inits if x[1].startswith("202607")][:3] + inits[-6:]
    tareas = []
    for carpeta, ini in inits:
        for a in listar(f"{GEOS}/{carpeta}/{ini}/", r'href="([a-z0-9_]+_GMAOGEOS_\d{8}_ens\d\d\.nc4)"'):
            var = a.split("_GMAOGEOS")[0]
            if var in VARS_NRT and (not QA or os.environ.get("SIRA_QA_TODAS", "0") == "1" or a.endswith("ens01.nc4")):
                tareas.append((carpeta, ini, a, var))
    log(f"GEOS-S2S NRT: {len(inits)} inicios ({inits[0][1]} → {inits[-1][1]}) · {len(tareas)} archivos")
    PARC = OUT / "geos_nrt_parcial.csv"  # avance guardado cada 50 archivos: si se corta, se retoma desde aquí
    hechos = set(pd.read_csv(PARC).archivo) if PARC.exists() else set()
    for var in VARS_NRT:
        ej = next(t_ for t_ in tareas if t_[3] == var)
        verificar(f"{GEOS}/{ej[0]}/{ej[1]}/{ej[2]}", var)


    def tarea_nrt(t):
        carpeta, ini, a, var = t
        c, v = serie(f"{GEOS}/{carpeta}/{ini}/{a}", var, 45)
        miembro = int(a.split("_ens")[1][:2])
        f0 = dt.datetime.strptime(ini, "%Y%m%d")  # día k del pronóstico = inicio + k (media diaria)
        return [{"archivo": a, "inicio": ini, "miembro": miembro, "variable": var,
                 "fecha": (f0 + dt.timedelta(days=k)).strftime("%Y-%m-%d"), "valor": x,
                 "lat_celda": c["lat"], "lon_celda": c["lon"]} for k, x in enumerate(v)]


    pend = [t_ for t_ in tareas if t_[2] not in hechos]
    log(f"GEOS NRT: {len(hechos)} archivos ya leídos antes, faltan {len(pend)}")
    filas, fallos_g = [], []
    with cf.ThreadPoolExecutor(12) as ex:
        fut = {ex.submit(tarea_nrt, t_): t_ for t_ in pend}
        for k, fu in enumerate(cf.as_completed(fut), 1):
            try:
                filas += fu.result()
            except Exception as e:  # noqa: BLE001
                fallos_g.append({"archivo": "/".join(fut[fu][:3]), "error": str(e)[:300]})
            if k % 50 == 0 or k == len(pend):
                if filas:
                    pd.DataFrame(filas).to_csv(PARC, mode="a", header=not PARC.exists(), index=False)
                filas = []
                log(f"GEOS NRT {k}/{len(pend)} · fallos {len(fallos_g)} · avance guardado")
    G = pd.read_csv(PARC) if PARC.exists() else pd.DataFrame()
    G.to_csv(OUT / "geos_nrt.csv", index=False)
    MESES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    if not hecho("geos_drift.csv"):
        tareas_d = []
        for m in (MESES[:1] if QA else MESES):
            for a in listar(f"{GEOS}/Drift/for_subc/{m}/", r'href="(\d{4}\.(?:PRECTOTCORR|T2M)\.drift\.\d\d\.nc4)"'):
                tareas_d.append((m, a))
        if QA and os.environ.get("SIRA_QA_TODAS", "0") != "1":
            tareas_d = tareas_d[:2]
        for var in ("PRECTOTCORR", "T2M"):
            ej = next(t_ for t_ in tareas_d if f".{var}." in t_[1])
            verificar(f"{GEOS}/Drift/for_subc/{ej[0]}/{ej[1]}", "drift_" + var)

        def tarea_drift(t):
            m, a = t
            ini, var = a.split(".")[0], a.split(".")[1]
            c, v = serie(f"{GEOS}/Drift/for_subc/{m}/{a}", "drift_" + var, 28)  # 28 días: cabe en cualquier mes
            return [{"inicio_mmdd": ini, "variable": var, "archivo": a,
                     "fecha": (c["t0"] + dt.timedelta(days=k)).strftime("%Y-%m-%d"), "valor": x} for k, x in enumerate(v)]

        filas = []
        with cf.ThreadPoolExecutor(8) as ex:
            fut = {ex.submit(tarea_drift, t_): t_ for t_ in tareas_d}
            for k, fu in enumerate(cf.as_completed(fut), 1):
                try:
                    filas += fu.result()
                except Exception as e:  # noqa: BLE001
                    fallos_g.append({"archivo": "/".join(fut[fu]), "error": str(e)[:300]})
                if k % 50 == 0 or k == len(tareas_d):
                    log(f"GEOS Drift {k}/{len(tareas_d)} · fallos {len(fallos_g)}")
        pd.DataFrame(filas).to_csv(OUT / "geos_drift.csv", index=False)
    pd.DataFrame(fallos_g, columns=["archivo", "error"]).to_csv(OUT / "geos_fallos.csv", index=False)
except Exception as e:  # noqa: BLE001
    log(f"AVISO GEOS-S2S incompleto: {e} · {traceback.format_exc()[-1500:]}")


# %% [markdown]
# ## 5. ONI (NOAA CPC) y resumen

# %%
txt = urllib.request.urlopen("https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt", timeout=120).read().decode()
oni = pd.read_csv(io.StringIO(txt), sep=r"\s+")
oni.to_csv(OUT / "oni.csv", index=False)
resumen = {"qa": QA, "ventana_m": 2 * MEDIA_M, "rejilla": N, "imagenes": int(len(meta)),
           "imagenes_por_sensor": meta.groupby("plataforma").size().to_dict(), "fallos_imagenes": len(fallos),
           "hls": len(hls_meta), "geos_inicios": len(inits), "geos_filas": int(len(G)), "fallos_geos": len(fallos_g),
           "chirps_hasta": str(cl.lluvia.last_valid_index().date()), "minutos": round((time.time() - T0) / 60, 1)}
json.dump(resumen, open(OUT / "resumen_datos.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
log("DATOS OK " + json.dumps(resumen, ensure_ascii=False))
