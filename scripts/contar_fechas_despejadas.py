"""Cuenta, para cada satélite, las fechas en que el lote de Nataima está despejado (píxel por píxel).

Fuente: Microsoft Planetary Computer (sin cuenta). Máscaras de calidad de cada producto:
- Sentinel-2 L2A, banda SCL: despejado = 4 vegetación, 5 suelo, 6 agua.
- Landsat C2 L2, banda QA_PIXEL: despejado = bit 6 (claro) o bit 7 (agua), sin relleno (bit 0), nube
  dilatada (1), cirro (2), nube (3) ni sombra (4). Landsat 7 además puede tener franjas sin dato (relleno).
- HLS v2 (S30 y L30), banda Fmask: despejado = sin nube (bit 1), adyacente (bit 2) ni sombra (bit 3).
Una fecha cuenta como despejada si ≥ 90 % de los píxeles del lote lo están.

Salida: data/interim/fechas_despejadas_lote.csv y resumen en pantalla.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import planetary_computer as pc
import pystac_client
import rasterio
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sira.verdad.franjas import poligonos  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
LON, LAT = -74.9621, 4.1895
SIEMBRAS = {1: pd.Timestamp("2021-05-27"), 2: pd.Timestamp("2021-12-04"), 3: pd.Timestamp("2022-07-09")}
COLECCIONES = {"sentinel-2-l2a": "SCL", "landsat-c2-l2": "qa_pixel", "hls2-s30": "Fmask", "hls2-l30": "Fmask"}


def despejado(col: str, q: np.ndarray) -> np.ndarray:
    q = q.astype(np.int64)
    if col == "sentinel-2-l2a":
        return np.isin(q, [4, 5, 6])
    if col == "landsat-c2-l2":
        malo = (q & (1 << 0)) | (q & (1 << 1)) | (q & (1 << 2)) | (q & (1 << 3)) | (q & (1 << 4))
        bueno = (q & (1 << 6)) | (q & (1 << 7))
        return (malo == 0) & (bueno > 0)
    malo = (q & (1 << 1)) | (q & (1 << 2)) | (q & (1 << 3))  # HLS Fmask
    return (malo == 0) & (q != 255)


def temporada(f: pd.Timestamp) -> tuple[int | None, int | None]:
    for t, d0 in SIEMBRAS.items():
        das = (f - d0).days + 1
        if 1 <= das <= 125:
            return t, das
    return None, None


def con_reintentos(fn, intentos=6, espera=20):
    import time
    for k in range(intentos):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001  (cortes de red)
            if k == intentos - 1:
                raise
            print(f"reintento {k + 1}: {str(e)[:80]}", file=sys.stderr)
            time.sleep(espera)


cat = con_reintentos(lambda: pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1",
                                                        modifier=pc.sign_inplace))
filas = []
PARCIAL = RAIZ / "data" / "interim" / "_fechas_despejadas_parcial.csv"
for col, banda in COLECCIONES.items():
    items = con_reintentos(lambda: list(cat.search(collections=[col], intersects={"type": "Point", "coordinates": [LON, LAT]},
                                                   datetime="2021-01-01/2022-12-31").items()))
    for it in items:
        def leer_q(it=it):
            with rasterio.open(pc.sign(it.assets[banda].href)) as src:
                lote = unary_union(list(poligonos(crs=str(src.crs))["franjas"].values()))
                b = lote.buffer(40).bounds
                w = from_bounds(*b, transform=src.transform)
                q = src.read(1, window=w, boundless=True, fill_value=src.nodata or 0)
                m = ~geometry_mask([lote], q.shape, src.window_transform(w), all_touched=True)
            return q, m
        try:
            q, m = con_reintentos(leer_q, intentos=4, espera=10)
            frac = float(despejado(col, q)[m].mean()) if m.any() else np.nan
        except Exception as e:  # noqa: BLE001
            frac = np.nan
            print("error", it.id, str(e)[:80], file=sys.stderr)
        f = pd.Timestamp(it.datetime.date())
        t, das = temporada(f)
        filas.append({"coleccion": col, "id": it.id, "fecha": f.date(),
                      "satelite": it.properties.get("platform", ""), "frac_despejado": round(frac, 3),
                      "temporada": t, "das": das})
    pd.DataFrame(filas).to_csv(PARCIAL, index=False)
    print(col, "listo", len(items), file=sys.stderr)

df = pd.DataFrame(filas)
# una fila por colección y fecha: si dos escenas (dos tiles) cubren el lote el mismo día, la más despejada
df = df.sort_values("frac_despejado", ascending=False).drop_duplicates(["coleccion", "fecha"]).sort_values(["coleccion", "fecha"])
df["despejado"] = df.frac_despejado >= 0.9
df.to_csv(RAIZ / "data" / "interim" / "fechas_despejadas_lote.csv", index=False)
res = df.groupby("coleccion").agg(fechas=("fecha", "size"), despejadas=("despejado", "sum"),
                                  despejadas_en_temporada=("despejado", lambda s: int((s & df.loc[s.index, "temporada"].notna()).sum())))
print(res.to_string())
print(df[df.despejado & df.temporada.notna()].groupby(["coleccion", "temporada"]).size().unstack(fill_value=0).to_string())
