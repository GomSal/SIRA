"""¿Está bien ubicado el lote? Prueba fenológica con Sentinel-2.

Si los polígonos caen sobre el ensayo, dentro de ellos el NDVI debe ser bajo cerca de cada siembra (suelo
preparado o inundado) y alto a mitad de temporada, al ritmo de las 3 fechas de siembra estimadas; y debe
seguir ese ritmo mejor que los alrededores. Se dibuja el NDVI de fechas despejadas en una ventana de 500 m
y la serie del NDVI medio dentro del lote frente a los días después de siembra.
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import planetary_computer as pc
import pystac_client
import rasterio
from pyproj import Transformer
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sira.verdad.franjas import poligonos  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
LON, LAT, MEDIO = -74.9621, 4.1895, 250
SIEMBRAS = {1: pd.Timestamp("2021-05-27"), 2: pd.Timestamp("2021-12-04"), 3: pd.Timestamp("2022-07-09")}

cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
items = cat.search(collections=["sentinel-2-l2a"], intersects={"type": "Point", "coordinates": [LON, LAT]},
                   datetime="2021-04-01/2022-12-31", query={"eo:cloud_cover": {"lt": 80}}).items()
vistas, serie = {}, []
for it in sorted(items, key=lambda i: i.datetime):
    fecha = pd.Timestamp(it.datetime.date())
    if fecha in vistas:
        continue
    with rasterio.open(it.assets["SCL"].href) as src:
        crs = src.crs
        x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(LON, LAT)
        b = (x - MEDIO, y - MEDIO, x + MEDIO, y + MEDIO)
        scl = src.read(1, window=from_bounds(*b, transform=src.transform), boundless=True, fill_value=0)
    c = scl.shape[0] // 2
    if np.isin(scl[c - 5:c + 5, c - 5:c + 5], [4, 5, 6]).mean() < 0.95:
        continue
    bandas = {}
    for k in ("B04", "B08"):
        with rasterio.open(it.assets[k].href) as src:
            w = from_bounds(*b, transform=src.transform)
            bandas[k] = src.read(1, window=w, boundless=True, fill_value=0).astype(float)
            tr = src.window_transform(w)
    ndvi = (bandas["B08"] - bandas["B04"]) / (bandas["B08"] + bandas["B04"] + 1e-6)
    lote = poligonos(crs=str(crs))
    mask = ~geometry_mask(list(lote["franjas"].values()), ndvi.shape, tr)
    anillo = (~mask) & (np.hypot(*np.meshgrid(np.arange(ndvi.shape[1]) - ndvi.shape[1] / 2,
                                                np.arange(ndvi.shape[0]) - ndvi.shape[0] / 2)) < 20)
    vistas[fecha] = (ndvi, b, lote)
    serie.append({"fecha": fecha, "ndvi_lote": float(ndvi[mask].mean()), "ndvi_alrededor": float(ndvi[anillo].mean())})

s = pd.DataFrame(serie)
for t, d0 in SIEMBRAS.items():
    s[f"das_gs{t}"] = (s.fecha - d0).dt.days + 1
s.to_csv(RAIZ / "data" / "interim" / "ndvi_lote_s2.csv", index=False)

fig, ax = plt.subplots(figsize=(13, 4))
ax.plot(s.fecha, s.ndvi_lote, "o-", ms=4, label="NDVI dentro del lote (polígonos)")
ax.plot(s.fecha, s.ndvi_alrededor, "s--", ms=3, alpha=0.6, label="NDVI alrededor (≤200 m)")
for t, d0 in SIEMBRAS.items():
    ax.axvspan(d0, d0 + pd.Timedelta(days=125), color="green", alpha=0.08)
    ax.axvline(d0, color="green", lw=1)
    ax.text(d0, 0.95, f" siembra GS{t}", fontsize=8, color="green")
ax.set_ylim(0, 1); ax.set_ylabel("NDVI"); ax.legend(fontsize=8)
ax.set_title("Sentinel-2, fechas con el lote despejado: ¿sube y baja el NDVI del lote al ritmo de las siembras?", fontsize=10)
fig.tight_layout(); fig.savefig(RAIZ / "reports" / "control" / "ndvi_lote_serie.png", dpi=100)

# mosaico: siembra (±15 d) frente a mitad de temporada (60–80 das) en cada siembra
sel = []
for t, d0 in SIEMBRAS.items():
    for lo, hi in ((-10, 15), (55, 85)):
        cand = [f for f in vistas if lo <= (f - d0).days + 1 <= hi]
        if cand:
            sel.append((t, cand[0]))
fig, axs = plt.subplots(1, len(sel), figsize=(3.2 * len(sel), 3.6))
for ax, (t, f) in zip(np.atleast_1d(axs), sel):
    ndvi, b, lote = vistas[f]
    ax.imshow(ndvi, extent=(b[0], b[2], b[1], b[3]), cmap="RdYlGn", vmin=0, vmax=0.9)
    for p in lote["franjas"].values():
        xs, ys = p.exterior.xy
        ax.plot(xs, ys, color="black", lw=0.8)
    ax.set_title(f"GS{t} día {(f - SIEMBRAS[t]).days + 1}\n{f.date()}", fontsize=8)
    ax.set_xticks([]); ax.set_yticks([])
fig.tight_layout(); fig.savefig(RAIZ / "reports" / "control" / "ndvi_lote_mosaico.png", dpi=100)
print(len(s), "fechas despejadas sobre el lote")
