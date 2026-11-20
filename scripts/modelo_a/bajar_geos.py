"""Baja GEOS-S2S (NRT y Drift) en la celda de Nataima, fuera de Kaggle. Mismo código que la sección 4 del
notebook 1. Salida: data/processed/modelo_a/geos/ (geos_nrt.csv, geos_drift.csv), que el notebook 2 toma del repo."""
import os
os.environ["SIRA_GEOS"] = "1"
os.environ.setdefault("SIRA_OUT_A1", "data/processed/modelo_a/geos")
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
# | ONI | NOAA CPC | `oni.csv` |
#
# **GEOS-S2S no se baja aquí:** no hace falta para entrenar, y desde Kaggle el portal de la NASA se atasca. Se baja
# aparte (`scripts/modelo_a/bajar_geos.py`) y el notebook 2 lo toma del repositorio solo para la evaluación final.
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

# Este bloque se salta en Kaggle (`BAJAR_GEOS = False`). Es el mismo código que usa `scripts/modelo_a/bajar_geos.py`.

# %%
import fsspec
import h5py

GEOS = "https://portal.nccs.nasa.gov/datashare/gmao/geos-s2s-3"
import aiohttp  # noqa: E402

fs = fsspec.filesystem("https", client_kwargs={"timeout": aiohttp.ClientTimeout(total=180, sock_read=60)})


def listar(url, patron):
    html = urllib.request.urlopen(url, timeout=120).read().decode("utf-8", "ignore")
    return sorted(set(re.findall(patron, html)))


class ArchivoRemoto(io.RawIOBase):
    """Archivo por HTTP con peticiones de rango síncronas (sin fsspec ni hilos internos: h5py + fsspec en varios hilos
    se bloquea). La cabecera (primeros 256 KB) llega en una sola petición; el resto, por bloques de 64 KB."""

    B = 2 ** 16

    def __init__(self, url, cabeza=2 ** 18):
        self.url, self.pos, self.bloques = url, 0, {}
        datos, self.tam = self._rango(0, cabeza - 1)
        for k in range(0, len(datos), self.B):
            self.bloques[k // self.B] = datos[k:k + self.B]

    def _rango(self, a, b):
        for k in range(6):
            try:
                req = urllib.request.Request(self.url, headers={"Range": f"bytes={a}-{b}"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    cr = r.headers.get("Content-Range", "")
                    return r.read(), int(cr.split("/")[-1]) if "/" in cr else None
            except Exception:  # noqa: BLE001
                if k == 5:
                    raise
                time.sleep(3 * (k + 1))

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else self.pos + off if whence == 1 else self.tam + off
        return self.pos

    def readinto(self, buf):
        n = min(len(buf), max(0, self.tam - self.pos))
        if n == 0:
            return 0
        b0, b1 = self.pos // self.B, (self.pos + n - 1) // self.B
        faltan = [b for b in range(b0, b1 + 1) if b not in self.bloques]
        if faltan:
            datos, _ = self._rango(faltan[0] * self.B, min((faltan[-1] + 1) * self.B, self.tam) - 1)
            for k, b in enumerate(range(faltan[0], faltan[-1] + 1)):
                self.bloques[b] = datos[k * self.B:(k + 1) * self.B]
        junto = b"".join(self.bloques[b] for b in range(b0, b1 + 1))
        ini = self.pos - b0 * self.B
        buf[:n] = junto[ini:ini + n]
        self.pos += n
        return n


H5_LOCK = __import__("threading").Lock()  # h5py no es seguro entre hilos: una cabecera a la vez (es rápido)


def capa(url, con_ref=False):
    """Lee SOLO la cabecera HDF5 de un archivo: dónde empieza cada día en bytes y qué celda es la del lote.
    Los archivos son float32 sin comprimir con un bloque por día (1, 361, 720), o contiguos (Drift)."""
    for k in range(5):
        try:
            f = ArchivoRemoto(url)  # la red va fuera del candado; h5py solo lee de memoria
            with H5_LOCK, h5py.File(f, "r") as h:
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


