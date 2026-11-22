"""Extrae NDVI y NDMI de todas las fechas Sentinel-2 L2A y Landsat C2 L2 (1984–2026) en una ventana alrededor de Nataima.

Rejilla común de 30 m (EPSG:32618) para poder comparar sensores en el estudio de años: Sentinel-2 se promedia a 30 m,
Landsat queda en su resolución nativa. Misma calidad que el Modelo B (cloud/ronda1_b/job_b.py):
  Sentinel-2: SCL ∈ {4 vegetación, 5 suelo, 6 agua}; offset −1000 desde el baseline 04.00.
  Landsat: QA_PIXEL bits 0–4 = 0 (relleno, nube dilatada, cirro, nube, sombra) y bit 6 (despejado) o 7 (agua).
  Además: reflectancias > 0 (quita el bandeado de Landsat 7 SLC-off y bordes sin dato).
Índices: NDVI = (NIR − rojo)/(NIR + rojo) con B08 en S2 y nir08 en Landsat; NDMI = (B8A − B11)/(B8A + B11) en S2 y
(nir08 − swir16)/(nir08 + swir16) en Landsat. Un pixel de 30 m de S2 es válido si sus 9 subpixeles son válidos.
Uso: python extraer_ventana.py [--media-m 1500] [--hilos 12] [--qa]
Salida: data/interim/modelo_a/ventana_<lado>km.npz (ndvi, ndmi [fecha × pixel], fechas, sensor, plataforma, id)
"""
import argparse
import concurrent.futures as cf
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import planetary_computer as pc
import pystac_client
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT

RAIZ = Path(__file__).resolve().parents[2]
LON, LAT = -74.9621, 4.1895
CRS = "EPSG:32618"
ap = argparse.ArgumentParser()
ap.add_argument("--media-m", type=int, default=1500)
ap.add_argument("--hilos", type=int, default=12)
ap.add_argument("--qa", action="store_true")
a = ap.parse_args()
OUT = RAIZ / "data/interim/modelo_a"
OUT.mkdir(parents=True, exist_ok=True)
x0, y0 = Transformer.from_crs("EPSG:4326", CRS, always_xy=True).transform(LON, LAT)
x0, y0 = round(x0 / 30) * 30, round(y0 / 30) * 30
N = 2 * a.media_m // 30
TRF = rasterio.transform.from_origin(x0 - a.media_m, y0 + a.media_m, 30, 30)
TRF10 = rasterio.transform.from_origin(x0 - a.media_m, y0 + a.media_m, 10, 10)

cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
pt = {"type": "Point", "coordinates": [LON, LAT]}
items = []
for col in ("sentinel-2-l2a", "landsat-c2-l2"):
    items += list(cat.search(collections=[col], intersects=pt, datetime="1982-01-01/2026-11-18").items())
# Sentinel-2: un gránulo por toma (18NWK preferido; 18NVK si falta)
s2 = [i for i in items if i.collection_id == "sentinel-2-l2a"]
s2.sort(key=lambda i: (i.datetime.date(), i.properties.get("s2:mgrs_tile") != "18NWK"))
vistos, s2u = set(), []
for i in s2:
    if i.datetime.date() not in vistos:
        vistos.add(i.datetime.date())
        s2u.append(i)
ls = [i for i in items if i.collection_id == "landsat-c2-l2"]
items = s2u + ls
if a.qa:
    items = s2u[-3:] + ls[-2:] + [i for i in ls if i.properties["platform"] == "landsat-5"][:2]
print(f"{len(s2u)} fechas S2 · {len(ls)} escenas Landsat · rejilla {N}×{N} de 30 m", flush=True)


def leer(href, rejilla, res, remuestreo):
    trf, n = (TRF, N) if res == 30 else (TRF10, 3 * N)
    for k in range(4):
        try:
            with rasterio.open(href) as src, WarpedVRT(src, crs=CRS, transform=trf, width=n, height=n,
                                                      resampling=remuestreo, nodata=0) as v:
                return v.read(1).astype(np.float32)
        except Exception:  # noqa: BLE001
            if k == 3:
                raise
            time.sleep(5 * (k + 1))


def procesar(it):
    p = it.properties
    if it.collection_id == "sentinel-2-l2a":
        off = -1000.0 if str(p.get("s2:processing_baseline", "00.00")) >= "04.00" else 0.0
        r = {b: leer(it.assets[b].href, None, 10, Resampling.nearest) for b in ("B04", "B08", "B8A", "B11", "SCL")}
        ok = np.isin(r.pop("SCL"), [4, 5, 6])
        R = {b: (v + off) / 1e4 for b, v in r.items()}
        ok &= (r["B04"] > 0) & (r["B08"] > 0) & (r["B11"] > 0) & (R["B04"] > 0) & (R["B08"] > 0) & (R["B11"] > 0)
        ndvi = (R["B08"] - R["B04"]) / (R["B08"] + R["B04"])
        ndmi = (R["B8A"] - R["B11"]) / (R["B8A"] + R["B11"])
        agg = lambda z: z.reshape(N, 3, N, 3).mean(axis=(1, 3))  # noqa: E731  10 m → 30 m
        okk = ok.reshape(N, 3, N, 3).all(axis=(1, 3))
        ndvi, ndmi = np.where(okk, agg(np.where(ok, ndvi, 0)), np.nan), np.where(okk, agg(np.where(ok, ndmi, 0)), np.nan)
        sensor, plat = "S2", p.get("platform")
    else:
        r = {b: leer(it.assets[b].href, None, 30, Resampling.nearest) for b in ("red", "nir08", "swir16", "qa_pixel")}
        q = r.pop("qa_pixel").astype(np.int64)
        R = {b: v * 0.0000275 - 0.2 for b, v in r.items()}
        ok = ((q & 0b11111) == 0) & (((q >> 6) & 1) | ((q >> 7) & 1)).astype(bool) & (q > 0)
        ok &= (r["red"] > 0) & (r["nir08"] > 0) & (r["swir16"] > 0) & (R["red"] > 0) & (R["nir08"] > 0) & (R["swir16"] > 0)
        ndvi = np.where(ok, (R["nir08"] - R["red"]) / (R["nir08"] + R["red"]), np.nan)
        ndmi = np.where(ok, (R["nir08"] - R["swir16"]) / (R["nir08"] + R["swir16"]), np.nan)
        sensor, plat = "L", p.get("platform")
    return dict(id=it.id, fecha=it.datetime.strftime("%Y-%m-%d"), sensor=sensor, plataforma=plat,
                nube_escena=p.get("eo:cloud_cover"), ndvi=ndvi.astype(np.float32).ravel(),
                ndmi=ndmi.astype(np.float32).ravel())


t0 = time.time()
res, fallos = [], []
with cf.ThreadPoolExecutor(a.hilos) as ex:
    fut = {ex.submit(procesar, it): it for it in items}
    for k, f in enumerate(cf.as_completed(fut), 1):
        it = fut[f]
        try:
            res.append(f.result())
        except Exception as e:  # noqa: BLE001
            fallos.append({"id": it.id, "error": re.sub(r"\?\S*", "?<token>", str(e))[:200]})
        if k % 50 == 0 or k == len(items):
            print(f"{k}/{len(items)} · {time.time() - t0:.0f} s · fallos {len(fallos)}", flush=True)
res.sort(key=lambda d: (d["fecha"], d["sensor"], d["id"]))
meta = pd.DataFrame([{k: v for k, v in d.items() if k not in ("ndvi", "ndmi")} for d in res])
meta["pixeles_validos"] = [int(np.isfinite(d["ndvi"]).sum()) for d in res]
nombre = f"ventana_{2 * a.media_m / 1000:g}km{'_qa' if a.qa else ''}"
np.savez_compressed(OUT / f"{nombre}.npz", ndvi=np.stack([d["ndvi"] for d in res]), ndmi=np.stack([d["ndmi"] for d in res]),
                    n=N, x0=x0 - a.media_m, y0=y0 + a.media_m)
meta.to_csv(OUT / f"{nombre}_fechas.csv", index=False)
pd.DataFrame(fallos, columns=["id", "error"]).to_csv(OUT / f"{nombre}_fallos.csv", index=False)
print(f"listo: {len(res)} imágenes, {len(fallos)} fallos, {time.time() - t0:.0f} s", flush=True)
