"""¿Siguen el NDVI y el NDRE (Sentinel-2) la humedad del suelo medida en Nataima?

NDRE = (B8A − B05) / (B8A + B05), las dos bandas a 20 m (B05 = borde rojo, 705 nm).
NDVI = (B08 − B04) / (B08 + B04) a 10 m.
Solo fechas despejadas sobre el lote dentro del rango de la curva de humedad, píxeles ≥90 % dentro de una
franja. Se compara contra la humedad del mismo día y contra el promedio de los k días previos.
Salida: reports/correlacion_ndvi_ndre.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import planetary_computer as pc
import pystac_client
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from scipy.stats import spearmanr

RAIZ = Path(__file__).resolve().parents[1]
SIEMBRAS = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}
FRANJAS = ("MIRI", "AWD+MIRI", "CD")

f = pd.read_csv(RAIZ / "data/interim/fechas_despejadas_lote.csv")
f = f[f.despejado & (f.coleccion == "sentinel-2-l2a") & f.das.between(28, 115)]
pix = pd.read_csv(RAIZ / "data/processed/pixeles_franjas_s2_10m.csv")
pix["franja"] = pix[[f"frac_{x}" for x in FRANJAS]].idxmax(axis=1).str.replace("frac_", "")
pix["pureza"] = pix[[f"frac_{x}" for x in FRANJAS]].max(axis=1)
pix = pix[pix.pureza >= 0.9]
hum = pd.read_csv(RAIZ / "data/processed/humedad_continua.csv")
cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)

filas = []
for r in f.itertuples():
    it = cat.get_collection("sentinel-2-l2a").get_item(r.id)
    off = -1000.0 if str(it.properties.get("s2:processing_baseline", "0")) >= "04.00" else 0.0
    xs, ys = pix.x_centro.to_numpy(), pix.y_centro.to_numpy()
    b = (xs.min() - 30, ys.min() - 30, xs.max() + 30, ys.max() + 30)
    with rasterio.open(it.assets["B04"].href) as ref:
        w = from_bounds(*b, transform=ref.transform).round_offsets().round_lengths()
        wtr, shape = ref.window_transform(w), (int(w.height), int(w.width))
        bounds = rasterio.windows.bounds(w, ref.transform)
    B = {}
    for k in ("B04", "B08", "B05", "B8A"):
        with rasterio.open(it.assets[k].href) as src:
            B[k] = (src.read(1, window=from_bounds(*bounds, transform=src.transform), out_shape=shape,
                             boundless=True, resampling=Resampling.nearest).astype(float) + off) / 10000
    ndvi = (B["B08"] - B["B04"]) / (B["B08"] + B["B04"])
    ndre = (B["B8A"] - B["B05"]) / (B["B8A"] + B["B05"])
    cc, rr = ~wtr * (xs, ys)
    cc, rr = np.floor(cc).astype(int), np.floor(rr).astype(int)
    t = int(r.temporada)
    das = int(r.das)
    for i in range(len(pix)):
        filas.append({"temporada": t, "fecha": r.fecha, "das": das, "franja": pix.franja.iloc[i],
                      "ndvi": ndvi[rr[i], cc[i]], "ndre": ndre[rr[i], cc[i]]})
    print(r.fecha, "listo", file=sys.stderr)

d = pd.DataFrame(filas).groupby(["temporada", "fecha", "das", "franja"], as_index=False)[["ndvi", "ndre"]].mean()
for k in (0, 3, 5, 7, 10):
    d[f"vwc_{k}d"] = [hum[(hum.temporada == x.temporada) & (hum.tratamiento == x.franja) &
                          hum.das.between(x.das - k, x.das)].vwc_pct.mean() for x in d.itertuples()]
d.to_csv(RAIZ / "reports/correlacion_ndvi_ndre_datos.csv", index=False)

res = []
for k in (0, 3, 5, 7, 10):
    m = d.dropna(subset=[f"vwc_{k}d"])
    for ind in ("ndvi", "ndre"):
        rho, p = spearmanr(m[ind], m[f"vwc_{k}d"])
        res.append({"indice": ind.upper(), "humedad": "mismo día" if k == 0 else f"promedio {k} días previos",
                    "spearman": round(rho, 2), "p_valor": round(p, 4), "n_franja_fecha": len(m)})
m = d.dropna(subset=["vwc_0d"])
for ind in ("ndvi", "ndre"):
    for t, g in m.groupby("temporada"):
        rho, p = spearmanr(g[ind], g.vwc_0d)
        res.append({"indice": ind.upper(), "humedad": f"mismo día, solo GS{t}", "spearman": round(rho, 2),
                    "p_valor": round(p, 4), "n_franja_fecha": len(g)})
    ok = tot = 0
    for _, g in m.groupby(["temporada", "fecha"]):
        if len(g) == 3:
            tot += 1
            ok += spearmanr(g[ind], g.vwc_0d)[0] > 0
    res.append({"indice": ind.upper(), "humedad": f"ordena las 3 franjas como la humedad: {ok}/{tot} fechas",
                "spearman": np.nan, "p_valor": np.nan, "n_franja_fecha": tot})
R = pd.DataFrame(res)
R.to_csv(RAIZ / "reports/correlacion_ndvi_ndre.csv", index=False)
print(R.to_string(index=False))
