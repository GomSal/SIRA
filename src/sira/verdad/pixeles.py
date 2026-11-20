"""Qué fracción de cada píxel cae en cada franja (MIRI, AWD+MIRI, CD), para cada cuadrícula satelital.

La cuadrícula se toma de una escena real de cada producto (Planetary Computer), así coincide exactamente
con la de las imágenes: Sentinel-2 L2A (10 m), HLS (30 m) y Landsat C2 L2 (30 m, corrida 15 m respecto a HLS).

Con esta tabla, la humedad "verdadera" de un píxel en una fecha es el promedio de la humedad de las franjas
que toca, ponderado por el área que cubre de cada una:
    vwc_pixel = Σ_f frac_f · vwc_f / Σ_f frac_f
Se etiquetan solo los píxeles con ≥ 50 % de su área dentro del lote (los demás mezclan árboles, caminos).

Salida: data/processed/pixeles_franjas_<producto>.csv
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import planetary_computer as pc
import pystac_client
import rasterio
from shapely.geometry import box
from shapely.ops import unary_union

from .franjas import poligonos

RAIZ = Path(__file__).resolve().parents[3]
LON, LAT = -74.9621, 4.1895
PRODUCTOS = {"s2_10m": ("sentinel-2-l2a", "B04"), "hls_30m": ("hls2-s30", "B04"), "landsat_30m": ("landsat-c2-l2", "red")}


def tabla(coleccion: str, banda: str) -> pd.DataFrame:
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    it = next(cat.search(collections=[coleccion], intersects={"type": "Point", "coordinates": [LON, LAT]},
                         datetime="2022-01-01/2022-03-31", max_items=1).items())
    with rasterio.open(it.assets[banda].href) as src:
        tr, crs = src.transform, src.crs
    fr = poligonos(crs=str(crs))["franjas"]
    lote = unary_union(list(fr.values()))
    res = abs(tr.a)
    minx, miny, maxx, maxy = lote.bounds
    c0 = int(np.floor((minx - tr.c) / res)) - 1
    c1 = int(np.ceil((maxx - tr.c) / res)) + 1
    r0 = int(np.floor((tr.f - maxy) / res)) - 1
    r1 = int(np.ceil((tr.f - miny) / res)) + 1
    filas = []
    for r in range(r0, r1):
        for c in range(c0, c1):
            x0, y1 = tr.c + c * res, tr.f - r * res
            px = box(x0, y1 - res, x0 + res, y1)
            fr_area = {t: px.intersection(p).area / px.area for t, p in fr.items()}
            dentro = sum(fr_area.values())
            if dentro <= 0:
                continue
            filas.append({"pixel_id": f"{crs.to_epsg()}_{int(x0)}_{int(y1)}", "fila": r, "col": c,
                          "x_centro": x0 + res / 2, "y_centro": y1 - res / 2, "epsg": crs.to_epsg(),
                          **{f"frac_{t}": round(v, 3) for t, v in fr_area.items()},
                          "frac_lote": round(dentro, 3), "etiquetable": dentro >= 0.5})
    return pd.DataFrame(filas)


def main() -> None:
    for nombre, (col, banda) in PRODUCTOS.items():
        t = tabla(col, banda)
        t.to_csv(RAIZ / "data" / "processed" / f"pixeles_franjas_{nombre}.csv", index=False)
        puros = {s: int((t[f"frac_{s}"] > 0.999).sum()) for s in ("MIRI", "AWD+MIRI", "CD")}
        print(f"{nombre}: {len(t)} píxeles tocan el lote, {int(t.etiquetable.sum())} etiquetables (≥50 % dentro); "
              f"puros por franja {puros}")


if __name__ == "__main__":
    main()
