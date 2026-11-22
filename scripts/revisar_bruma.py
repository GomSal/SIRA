"""GS1: ¿las caídas bruscas de NDVI (7-jul y 11-ago de 2021) son bruma o nubes finas no detectadas?

Para cada fecha Sentinel-2 despejada en GS1 se mide, en el lote y en un anillo de 300 m alrededor:
  - NDVI, reflectancia azul (B02; la bruma y las nubes finas la suben), aerosoles (AOT) y probabilidad de nube (MSK_CLDPRB),
y se dibuja el color verdadero de cada fecha. Una caída de NDVI que también aparece en el anillo, con azul y AOT
altos, es atmósfera, no cultivo.
Salida: reports/dron/revision_gs1.csv y reports/control/gs{TEMP}_color_verdadero.png
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
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sira.verdad.franjas import poligonos  # noqa: E402

RAIZ = Path(__file__).resolve().parents[1]
f = pd.read_csv(RAIZ / "data/interim/fechas_despejadas_lote.csv")
TEMP = int(sys.argv[1]) if len(sys.argv) > 1 else 1
f = f[(f.coleccion == "sentinel-2-l2a") & (f.temporada == TEMP) & f.despejado]
cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
col = cat.get_collection("sentinel-2-l2a")


def leer(it, banda, b):
    with rasterio.open(it.assets[banda].href) as src:
        w = from_bounds(*b, transform=src.transform)
        return src.read(1, window=w, boundless=True, fill_value=0, out_shape=(60, 60)).astype(float), src.crs


filas, imgs = [], []
for r in f.itertuples():
    it = col.get_item(r.id)
    off = -1000.0 if str(it.properties.get("s2:processing_baseline", "0")) >= "04.00" else 0.0
    with rasterio.open(it.assets["B04"].href) as src:
        crs = str(src.crs)
    lote = unary_union(list(poligonos(crs=crs)["franjas"].values()))
    cx, cy = lote.centroid.x, lote.centroid.y
    b = (cx - 300, cy - 300, cx + 300, cy + 300)
    band = {k: (leer(it, k, b)[0] + off) / 10000 for k in ("B02", "B03", "B04", "B08")}
    aot = leer(it, "AOT", b)[0] / 1000
    cld = leer(it, "MSK_CLDPRB", b)[0] if "MSK_CLDPRB" in it.assets else np.full((60, 60), np.nan)
    tr = rasterio.transform.from_bounds(*b, 60, 60)
    m = ~geometry_mask([lote], (60, 60), tr, all_touched=True)
    ndvi = (band["B08"] - band["B04"]) / (band["B08"] + band["B04"] + 1e-6)
    filas.append({"temporada": TEMP, "fecha": r.fecha, "das": int(r.das), "ndvi_lote": ndvi[m].mean(), "ndvi_anillo": ndvi[~m].mean(),
                  "azul_lote": band["B02"][m].mean(), "azul_anillo": band["B02"][~m].mean(),
                  "aot": aot.mean(), "prob_nube_media": np.nanmean(cld), "id": r.id})
    imgs.append((r.fecha, int(r.das), np.dstack([band["B04"], band["B03"], band["B02"]]), m))
R = pd.DataFrame(filas).round(3)
R.to_csv(RAIZ / f"reports/dron/revision_gs{TEMP}.csv", index=False)
print(R.drop(columns="id").to_string(index=False))

fig, axs = plt.subplots(1, len(imgs), figsize=(3 * len(imgs), 3.4))
for ax, (fe, das, rgb, m) in zip(axs, imgs):
    ax.imshow(np.clip(rgb / 0.25, 0, 1))
    ax.contour(m, levels=[0.5], colors="yellow", linewidths=0.8)
    ax.set_title(f"{fe}\ndía {das}", fontsize=8); ax.set_xticks([]); ax.set_yticks([])
fig.suptitle(f"GS{TEMP}, Sentinel-2 color verdadero (misma escala). Amarillo = lote", fontsize=9)
fig.tight_layout(); fig.savefig(RAIZ / f"reports/control/gs{TEMP}_color_verdadero.png", dpi=100)
