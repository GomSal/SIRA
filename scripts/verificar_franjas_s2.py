"""Superpone los polígonos de las franjas sobre imágenes Sentinel-2 reales (Planetary Computer, sin cuenta).

Elige fechas con el lote despejado (banda SCL) y dibuja color verdadero y NDVI en una ventana de ~1,2 km.
Uso: python scripts/verificar_franjas_s2.py [dx_m dy_m]   (desplazamiento opcional del plano, en metros)
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.windows import from_bounds
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sira.verdad.franjas import poligonos  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
dx, dy = (float(sys.argv[1]), float(sys.argv[2])) if len(sys.argv) > 2 else (0.0, 0.0)
LON, LAT, MEDIO = -74.9621, 4.1895, 600  # centro aproximado del lote y media ventana (m)

cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
items = sorted(cat.search(collections=["sentinel-2-l2a"], intersects={"type": "Point", "coordinates": [LON, LAT]},
                          datetime="2021-01-01/2022-12-31", query={"eo:cloud_cover": {"lt": 60}}).items(),
               key=lambda i: i.properties["eo:cloud_cover"])


def leer(item, banda, bounds):
    with rasterio.open(item.assets[banda].href) as src:
        win = from_bounds(*bounds, transform=src.transform)
        return src.read(1, window=win, boundless=True, fill_value=0).astype(float), src.crs


limpias = []
for it in items:
    with rasterio.open(it.assets["SCL"].href) as src:
        x, y = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True).transform(LON, LAT)
        b = (x - MEDIO, y - MEDIO, x + MEDIO, y + MEDIO)
        scl = src.read(1, window=from_bounds(*b, transform=src.transform), boundless=True, fill_value=0)
    c = scl.shape[0] // 2
    centro = scl[c - 8:c + 8, c - 8:c + 8]  # ~±160 m (SCL a 20 m)
    if np.isin(centro, [4, 5, 6]).mean() > 0.95:  # vegetación, suelo desnudo, agua
        limpias.append((it, b, src.crs))
    if len(limpias) == 4:
        break

pol = poligonos((dx, dy), crs=str(limpias[0][2]))
fig, axs = plt.subplots(2, len(limpias), figsize=(5 * len(limpias), 10))
for k, (it, b, crs) in enumerate(limpias):
    r, _ = leer(it, "B04", b); g, _ = leer(it, "B03", b); bl, _ = leer(it, "B02", b); n, _ = leer(it, "B08", b)
    rgb = np.dstack([r, g, bl]) / 10000
    rgb = np.clip(rgb / np.percentile(rgb, 98), 0, 1)
    ndvi = (n - r) / (n + r + 1e-6)
    for fila, img, cm in ((0, rgb, None), (1, ndvi, "RdYlGn")):
        ax = axs[fila, k]
        ax.imshow(img, extent=(b[0], b[2], b[1], b[3]), cmap=cm, vmin=None if cm is None else 0, vmax=None if cm is None else 0.9)
        for t, p in pol["franjas"].items():
            xs, ys = p.exterior.xy
            ax.plot(xs, ys, color={"MIRI": "orange", "AWD+MIRI": "lime", "CD": "cyan"}[t], lw=1.2)
        ax.set_title(f"{it.datetime.date()}  {'color verdadero' if fila == 0 else 'NDVI'}", fontsize=9)
        ax.set_xticks([]); ax.set_yticks([])
fig.suptitle(f"Franjas del plano (Fig. 1) sobre Sentinel-2 · desplazamiento ({dx:+.0f}, {dy:+.0f}) m", fontsize=11)
fig.tight_layout()
out = RAIZ / "reports" / "control" / f"franjas_sobre_s2_{dx:+.0f}_{dy:+.0f}.png"
fig.savefig(out, dpi=100)
print(out, [str(i.datetime.date()) for i, _, _ in limpias])
