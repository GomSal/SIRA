"""Actualiza los datos de entrada del semáforo con lo publicado después de la última corrida, sin rehacer nada.

1. clima  · NASA POWER (ETo FAO-56 y lluvia PRECTOTCORR) y CHIRPS (ClimateSERV), desde `--desde_clima` hasta hoy.
            Reemplaza esas filas de clima.csv. Control: en los días que ya estaban, compara lo nuevo con lo guardado
            (POWER revisa sus datos recientes; se informa la diferencia) y la ETo recalculada con la guardada.
2. imagenes · Sentinel-2 L2A y Landsat 7/8/9 (Planetary Computer) posteriores a la última fecha del cubo, con el
            mismo código del notebook 1 (cloud/modelo_a/sira_a_1_datos.py: máscara, offset, índices, rejilla de 10 m).
            Control: vuelve a procesar escenas que ya estaban en el cubo y exige que salgan idénticas.
            El cubo se reescribe arreglo por arreglo (sin cargar los tres a la vez) y el anterior queda en cubo_anterior.npz.

GEOS-S2S se actualiza aparte con scripts/modelo_a/bajar_geos.py (retoma desde geos_nrt_parcial.csv).

    python scripts/operacion/actualizar_datos.py [--solo clima|imagenes] [--hasta 2026-11-22] [--seco]
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

os.environ.update({"GDAL_HTTP_TIMEOUT": "60", "GDAL_HTTP_CONNECTTIMEOUT": "20", "GDAL_HTTP_MAX_RETRY": "3",
                   "GDAL_HTTP_RETRY_DELAY": "5", "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
                   "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff"})
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
CUBO = RAIZ / "data/interim/modelo_a/kaggle_nb1/datos_a"
LON, LAT, ELEV_M, CRS = -74.9621, 4.1895, 323.0, "EPSG:32618"

ap = argparse.ArgumentParser()
ap.add_argument("--solo", choices=["clima", "imagenes"])
ap.add_argument("--hasta", default=str(dt.date.today()))
ap.add_argument("--desde_clima", default="2026-08-01", help="primer día que se vuelve a pedir a POWER y CHIRPS")
ap.add_argument("--control", type=int, default=2, help="escenas ya guardadas que se reprocesan como control")
ap.add_argument("--hilos", type=int, default=8)
ap.add_argument("--seco", action="store_true", help="no escribe nada")
A = ap.parse_args()
T0 = time.time()


def log(m):
    print(f"[{time.time() - T0:6.0f} s] {m}", flush=True)


def get_json(url, t=300):
    for k in range(6):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=t).read())
        except Exception:  # noqa: BLE001
            if k == 5:
                raise
            time.sleep(10 * (k + 1))


def eto_fao56(tmax, tmin, rh, u2, rs, doy, lat_deg=LAT, z=ELEV_M):
    """Copia exacta del notebook 1 (y del Modelo B, cloud/ronda1_b/job_b.py)."""
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


# ---------------------------------------------------------------- 1. clima
def actualizar_clima():
    viejo = pd.read_csv(CUBO / "clima.csv", parse_dates=["fecha"]).set_index("fecha")
    d0, d1 = pd.Timestamp(A.desde_clima), pd.Timestamp(A.hasta)
    par = "T2M_MAX,T2M_MIN,RH2M,WS2M,ALLSKY_SFC_SW_DWN,PRECTOTCORR"
    p = get_json(f"https://power.larc.nasa.gov/api/temporal/daily/point?parameters={par}&community=AG&longitude={LON}"
                 f"&latitude={LAT}&start={d0:%Y%m%d}&end={d1:%Y%m%d}&format=JSON")["properties"]["parameter"]
    cl = pd.DataFrame({k: pd.Series(v) for k, v in p.items()})
    cl.index = pd.to_datetime(cl.index, format="%Y%m%d")
    cl = cl.replace(-999.0, np.nan)
    cl["eto"], cl["Ra"] = eto_fao56(cl.T2M_MAX, cl.T2M_MIN, cl.RH2M, cl.WS2M, cl.ALLSKY_SFC_SW_DWN,
                                    cl.index.day_of_year.to_numpy())
    d = 0.025
    geom = json.dumps({"type": "Polygon", "coordinates": [[[LON - d, LAT - d], [LON + d, LAT - d], [LON + d, LAT + d],
                                                            [LON - d, LAT + d], [LON - d, LAT - d]]]})
    base = "https://climateserv.servirglobal.net/api/"
    q = urllib.parse.urlencode({"datatype": 0, "begintime": f"{d0:%m/%d/%Y}", "endtime": f"{d1:%m/%d/%Y}",
                                "intervaltype": 0, "operationtype": 5, "geometry": geom})
    rid = get_json(base + "submitDataRequest/?" + q)[0]
    for _ in range(120):
        if get_json(base + f"getDataRequestProgress/?id={rid}")[0] in (100, 100.0, -1):
            break
        time.sleep(5)
    lluvia = {}
    for x in get_json(base + f"getDataFromRequest/?id={rid}", t=900)["data"]:
        fx = pd.Timestamp(x["date"]) if "date" in x else pd.Timestamp(x["year"], x["month"], x["day"])
        lluvia[fx] = x["value"]["avg"]
    cl["lluvia"] = pd.Series(lluvia, dtype=float).sort_index().reindex(cl.index)
    cl.loc[cl.lluvia < -1, "lluvia"] = np.nan
    cl = cl.reindex(pd.date_range(d0, d1, name="fecha"))[viejo.columns]
    # Ra no depende de datos: se completa en los días sin POWER, como en el notebook
    cl["Ra"] = eto_fao56(30.0, 20.0, 70.0, 1.0, 20.0, cl.index.day_of_year.to_numpy())[1]

    # control en los días que ya estaban
    comun = cl.index.intersection(viejo.index)
    for c_ in ("PRECTOTCORR", "T2M_MAX", "eto", "lluvia"):
        a, b = viejo.loc[comun, c_], cl.loc[comun, c_]
        ambos = a.notna() & b.notna()
        log(f"control {c_}: {int(ambos.sum())} días en ambos · diferencia máx {float((a - b)[ambos].abs().max() or 0):.3f} · "
            f"nuevos con dato {int((a.isna() & b.notna()).sum())} · perdidos {int((a.notna() & b.isna()).sum())}")
    e_ok = viejo.loc[comun, "eto"].notna() & cl.loc[comun, "eto"].notna()
    recalc = eto_fao56(viejo.loc[comun, "T2M_MAX"], viejo.loc[comun, "T2M_MIN"], viejo.loc[comun, "RH2M"],
                       viejo.loc[comun, "WS2M"], viejo.loc[comun, "ALLSKY_SFC_SW_DWN"],
                       comun.day_of_year.to_numpy())[0]
    assert np.allclose(recalc[e_ok], viejo.loc[comun, "eto"][e_ok]), "la ETo de este script no es la del notebook 1"

    nuevo = pd.concat([viejo.loc[viejo.index < d0], cl])
    ult = lambda c_: nuevo[c_].last_valid_index().date()  # noqa: E731
    log(f"clima: POWER hasta {ult('PRECTOTCORR')} · ETo hasta {ult('eto')} · CHIRPS hasta {ult('lluvia')} · "
        f"filas {len(viejo)} → {len(nuevo)}")
    if not A.seco:
        nuevo.to_csv(CUBO / "clima.csv")
    return nuevo


# ---------------------------------------------------------------- 2. imágenes
def actualizar_imagenes():
    import planetary_computer as pc
    import pystac_client
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    meta = pd.read_csv(CUBO / "cubo_fechas.csv")
    z = np.load(CUBO / "cubo.npz")
    N, X0, Y0 = int(z["n"]), float(z["x0"]), float(z["y0"])
    TRF = rasterio.transform.from_origin(X0, Y0, 10, 10)
    ultima = meta.fecha.max()
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1")
    pt = {"type": "Point", "coordinates": [LON, LAT]}
    rango = f"{(pd.Timestamp(ultima) - pd.Timedelta(days=20)).date()}/{A.hasta}"
    s2 = list(cat.search(collections=["sentinel-2-l2a"], intersects=pt, datetime=rango).items())
    s2.sort(key=lambda i: (i.datetime.date(), i.properties.get("s2:mgrs_tile") != "18NWK"))
    vistos, s2u = set(), []
    for i in s2:
        if i.datetime.date() not in vistos:
            vistos.add(i.datetime.date())
            s2u.append(i)
    ls = [i for i in cat.search(collections=["landsat-c2-l2"], intersects=pt, datetime=rango).items()
          if i.properties.get("platform") in ("landsat-7", "landsat-8", "landsat-9")]
    items = s2u + ls
    ids = set(meta.id)
    fechas_s2 = set(meta.fecha[meta.sensor == "S2"])
    nuevos = [i for i in items if i.id not in ids and not (i.collection_id == "sentinel-2-l2a"
                                                             and i.datetime.strftime("%Y-%m-%d") in fechas_s2)]
    control = sorted([i for i in items if i.id in ids], key=lambda i: i.datetime)[-A.control:]
    log(f"imágenes: cubo hasta {ultima} ({len(meta)} escenas) · en el catálogo desde {rango}: {len(items)} · "
        f"nuevas {len(nuevos)} · control {len(control)}")

    def leer(href):
        for k in range(5):
            try:
                with rasterio.open(pc.sign(href)) as src, WarpedVRT(src, crs=CRS, transform=TRF, width=N, height=N,
                                                          resampling=Resampling.nearest, nodata=0) as v:
                    return v.read(1).astype(np.float32)
            except Exception:  # noqa: BLE001
                if k == 4:
                    raise
                time.sleep(5 * (k + 1))

    def procesar(it):  # copia exacta del notebook 1
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
        f = lambda z_: np.where(ok, z_, np.nan).astype(np.float16).ravel()  # noqa: E731
        return dict(id=it.id, fecha=it.datetime.strftime("%Y-%m-%d"), sensor=sensor, plataforma=p.get("platform"),
                    nube_escena=p.get("eo:cloud_cover"), ndvi=f(ndvi), ndmi=f(ndmi), azul=f(azul))

    with cf.ThreadPoolExecutor(A.hilos) as ex:
        res_c = list(ex.map(procesar, control))
        res = list(ex.map(procesar, nuevos))
    # control: lo reprocesado es idéntico a lo guardado
    fila = {i: k for k, i in enumerate(meta.id)}
    for nombre in ("ndvi", "ndmi", "azul"):
        arr = z[nombre]
        for d in res_c:
            assert np.array_equal(arr[fila[d["id"]]], d[nombre], equal_nan=True), f"control falló: {d['id']} {nombre}"
        del arr
    log(f"control: {len(res_c)} escenas ya guardadas salen idénticas ({', '.join(d['fecha'] for d in res_c)})")
    if not res:
        log("imágenes: no hay escenas nuevas")
        return meta
    res.sort(key=lambda d: (d["fecha"], d["sensor"], d["id"]))
    mnew = pd.DataFrame([{k: v for k, v in d.items() if k not in ("ndvi", "ndmi", "azul")} for d in res])
    mnew["pixeles_validos"] = [int(np.isfinite(d["ndvi"].astype(np.float32)).sum()) for d in res]
    log("nuevas: " + " · ".join(f"{r.fecha} {r.sensor} {r.pixeles_validos / (N * N):.0%} despejado"
                                for r in mnew.itertuples()))
    if A.seco:
        return meta
    tmp = CUBO / "cubo_nuevo.npz"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:  # un arreglo a la vez: no caben los tres en memoria
        for nombre in ("ndvi", "ndmi", "azul"):
            arr = np.concatenate([z[nombre], np.stack([d[nombre] for d in res])])
            with zf.open(f"{nombre}.npy", "w", force_zip64=True) as f:
                np.lib.format.write_array(f, arr)
            log(f"cubo: {nombre} {arr.shape}")
            del arr
        for nombre in ("n", "x0", "y0", "res"):
            with zf.open(f"{nombre}.npy", "w") as f:
                np.lib.format.write_array(f, z[nombre])
    z.close()
    zn = np.load(tmp)  # control del archivo escrito
    assert zn["ndvi"].shape == (len(meta) + len(res), N * N) and int(zn["n"]) == N and float(zn["x0"]) == X0
    shutil.move(CUBO / "cubo.npz", CUBO / "cubo_anterior.npz")
    shutil.move(tmp, CUBO / "cubo.npz")
    meta.to_csv(CUBO / "cubo_fechas_anterior.csv", index=False)
    meta = pd.concat([meta, mnew], ignore_index=True)
    meta.to_csv(CUBO / "cubo_fechas.csv", index=False)
    log(f"cubo: {len(meta)} escenas, hasta {meta.fecha.max()} (anterior en cubo_anterior.npz)")
    return meta


if A.solo in (None, "clima"):
    actualizar_clima()
if A.solo in (None, "imagenes"):
    actualizar_imagenes()
log("listo")
