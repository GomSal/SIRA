"""Sentinel-1 RTC (Planetary Computer, sin cuenta) sobre las 3 franjas físicas del lote de Nataima.

Para cada fecha y franja física (O = oeste, C = centro, E = este): retrodispersión media VV y VH
(promedio en lineal, luego dB), con píxeles de 10 m que caen ≥ 90 % dentro de la franja.
Se guarda también la órbita (descendente 69 / ascendente 150): la geometría cambia el nivel, por eso los
análisis comparan cada fecha contra la misma órbita.
Salida: data/interim/radar_s1_franjas.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sira.verdad.franjas import poligonos  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
FIS = {"MIRI": "O", "AWD+MIRI": "C", "CD": "E"}  # nombre de franja en el plano (2021) → posición física

cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
items = list(cat.search(collections=["sentinel-1-rtc"], intersects={"type": "Point", "coordinates": [-74.9621, 4.1895]},
                        datetime="2021-04-01/2022-12-31").items())
filas = []
for it in sorted(items, key=lambda i: i.datetime):
    vals = {}
    for pol in ("vv", "vh"):
        for intento in range(4):
            try:
                with rasterio.open(it.assets[pol].href) as src:
                    fr = poligonos(crs=str(src.crs))["franjas"]
                    lote = unary_union(list(fr.values()))
                    w = from_bounds(*lote.buffer(30).bounds, transform=src.transform)
                    a = src.read(1, window=w, boundless=True, fill_value=0).astype(float)
                    tr = src.window_transform(w)
                break
            except Exception as e:  # noqa: BLE001
                if intento == 3:
                    raise
                print("reintento", it.id, str(e)[:60], file=sys.stderr)
        for nombre, p in fr.items():
            # píxel ≥ 90 % dentro: se aproxima con el centro dentro de la franja encogida medio píxel
            m = ~geometry_mask([p.buffer(-abs(tr.a) * 0.45)], a.shape, tr)
            v = a[m & (a > 0)]
            vals[(FIS[nombre], pol)] = (10 * np.log10(v.mean()) if len(v) else np.nan, int(len(v)))
    for f in ("O", "C", "E"):
        filas.append({"fecha": it.datetime.date(), "plataforma": it.properties.get("platform"),
                      "orbita": f"{it.properties.get('sat:orbit_state')}_{it.properties.get('sat:relative_orbit')}",
                      "franja_fisica": f, "vv_db": round(vals[(f, 'vv')][0], 2), "vh_db": round(vals[(f, 'vh')][0], 2),
                      "n_pixeles": vals[(f, 'vv')][1]})
df = pd.DataFrame(filas)
df["vh_vv_db"] = (df.vh_db - df.vv_db).round(2)
df.to_csv(RAIZ / "data" / "interim" / "radar_s1_franjas.csv", index=False)
print(df.groupby("orbita").agg(fechas=("fecha", "nunique"), vv_medio=("vv_db", "mean"), pix=("n_pixeles", "mean")).to_string())
