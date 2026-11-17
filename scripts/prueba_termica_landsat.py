"""¿Sigue la temperatura de superficie (Landsat) el estado del agua en Nataima?

Para cada fecha Landsat despejada sobre el lote (2021–2022):
  - LST: banda térmica de superficie de Landsat C2 L2 (ST_B10 en L8/L9, ST_B6 en L7), en °C
    (DN × 0,00341802 + 149,0 − 273,15). Se mide a 100 m (L8/L9) o 60 m (L7) y se entrega remuestreada a 30 m.
  - NDVI de la misma escena.
  - TVDI (Sandholt et al., 2002): en la ventana de 2 km, borde seco = LST máxima por clase de NDVI (ajuste
    lineal), borde húmedo = LST mínima; TVDI = (LST − LST_húmedo) / (LST_seco(NDVI) − LST_húmedo). 0 = sin
    estrés, 1 = máximo estrés.
  - LST relativa: LST del lote − mediana de la LST de la vegetación de la ventana (quita el efecto del día).
  - ΔT: LST del lote − temperatura máxima del aire (NASA POWER).
Se compara con la humedad promedio del lote (las 3 franjas; no depende de la rotación de tratamientos) y con la
humedad mínima entre franjas (¿alguna franja cerca del estrés?), del mismo día y de los 3 y 5 días previos.
Salida: reports/termica_landsat/{datos.csv, correlaciones.csv, grafica.png}
"""
import json
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
from scipy.stats import spearmanr
from shapely.geometry import shape
from shapely.ops import transform, unary_union

RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports" / "termica_landsat"
OUT.mkdir(parents=True, exist_ok=True)
LON, LAT, MEDIO = -74.9621, 4.1895, 1000
SIEMBRAS = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}

gj = json.loads((RAIZ / "data/processed/franjas_nataima.geojson").read_text())
lote_ll = unary_union([shape(f["geometry"]) for f in gj["features"] if f["properties"]["tipo"] == "franja"])
fechas = pd.read_csv(RAIZ / "data/interim/fechas_despejadas_lote.csv")
fechas = fechas[(fechas.coleccion == "landsat-c2-l2") & fechas.despejado]
hum = pd.read_csv(RAIZ / "data/processed/humedad_continua.csv")
pw = json.loads((RAIZ / "data/raw/clima/power_eto.json").read_text())["properties"]["parameter"]
tmax = pd.Series(pw["T2M_MAX"]); tmax.index = pd.to_datetime(tmax.index, format="%Y%m%d")
cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)


def leer(it, nombre, b, shape_=None):
    with rasterio.open(it.assets[nombre].href) as src:
        w = from_bounds(*b, transform=src.transform)
        a = src.read(1, window=w, boundless=True, fill_value=0, out_shape=shape_).astype(float)
        return a, src.window_transform(w) if shape_ is None else None, src.crs


filas = []
for r in fechas.itertuples():
    try:
        it = cat.get_collection("landsat-c2-l2").get_item(r.id)
        nombre_st = "lwir11" if "lwir11" in it.assets else "lwir"
        with rasterio.open(it.assets["red"].href) as ref:
            crs = ref.crs
            x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(LON, LAT)
            b = (x - MEDIO, y - MEDIO, x + MEDIO, y + MEDIO)
            w = from_bounds(*b, transform=ref.transform).round_offsets().round_lengths()
            b = rasterio.windows.bounds(w, ref.transform)
            tr = ref.window_transform(w)
            shp = (int(w.height), int(w.width))
        A = {}
        for k in ("red", "nir08", nombre_st, "qa_pixel"):
            with rasterio.open(it.assets[k].href) as src:
                A[k] = src.read(1, window=from_bounds(*b, transform=src.transform), out_shape=shp,
                                boundless=True, fill_value=0).astype(float)
        q = A["qa_pixel"].astype(np.int64)
        limpio = ((q & 0b11111) == 0) & (((q & (1 << 6)) | (q & (1 << 7))) > 0) & (A[nombre_st] > 0)
        red = A["red"] * 0.0000275 - 0.2
        nir = A["nir08"] * 0.0000275 - 0.2
        ndvi = (nir - red) / (nir + red + 1e-6)
        lst = A[nombre_st] * 0.00341802 + 149.0 - 273.15
        lote = transform(Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform, lote_ll)
        m_lote = (~geometry_mask([lote], shp, tr, all_touched=False)) & limpio
        if m_lote.sum() == 0:
            continue
        v = limpio & (ndvi > 0.1) & (ndvi < 0.95)
        # TVDI: bordes por clases de NDVI de 0,02
        bins = np.arange(0.1, 0.95, 0.02)
        idx = np.digitize(ndvi[v], bins)
        tmax_b, ndvi_b = [], []
        for k in np.unique(idx):
            s = lst[v][idx == k]
            if len(s) >= 20:
                tmax_b.append(np.percentile(s, 99)); ndvi_b.append(ndvi[v][idx == k].mean())
        bs, as_ = np.polyfit(ndvi_b, tmax_b, 1)
        t_hum = np.percentile(lst[v], 1)
        seco = as_ + bs * ndvi
        tvdi = np.clip((lst - t_hum) / (seco - t_hum), 0, 1.5)
        f = pd.Timestamp(r.fecha)
        veg = v & (ndvi > 0.5)
        filas.append({"fecha": r.fecha, "satelite": it.properties.get("platform", ""), "temporada": r.temporada,
                      "das": r.das, "lst_lote": float(lst[m_lote].mean()), "ndvi_lote": float(ndvi[m_lote].mean()),
                      "tvdi_lote": float(tvdi[m_lote].mean()),
                      "lst_rel": float(lst[m_lote].mean() - np.median(lst[veg])) if veg.any() else np.nan,
                      "dT_aire": float(lst[m_lote].mean() - tmax.get(f, np.nan)),
                      "pendiente_borde_seco": float(bs), "n_pix_lote": int(m_lote.sum())})
        print(r.fecha, "ok", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(r.fecha, "omitida", str(e)[:60], file=sys.stderr)

d = pd.DataFrame(filas)
for k in (0, 3, 5):
    prom, minimo, bajo = [], [], []
    for r in d.itertuples():
        if pd.isna(r.temporada):
            prom.append(np.nan); minimo.append(np.nan); bajo.append(np.nan); continue
        h = hum[(hum.temporada == r.temporada) & hum.das.between(r.das - k, r.das)]
        g = h.groupby("tratamiento").vwc_pct.mean()
        ok = len(g) == 3
        prom.append(g.mean() if ok else np.nan); minimo.append(g.min() if ok else np.nan)
        bajo.append(int((g < 27.1).sum()) if ok else np.nan)
    d[f"vwc_lote_{k}d"], d[f"vwc_min_{k}d"], d[f"franjas_bajo_umbral_{k}d"] = prom, minimo, bajo
d.to_csv(OUT / "datos.csv", index=False)

res = []
m = d.dropna(subset=["vwc_lote_0d"])
for var in ("lst_lote", "tvdi_lote", "lst_rel", "dT_aire", "ndvi_lote"):
    for k in (0, 3, 5):
        for obj in ("vwc_lote", "vwc_min"):
            mm = m.dropna(subset=[var, f"{obj}_{k}d"])
            rho, p = spearmanr(mm[var], mm[f"{obj}_{k}d"])
            res.append({"medida": var, "humedad": f"{'promedio' if obj == 'vwc_lote' else 'mínima'} de franjas, "
                        f"{'mismo día' if k == 0 else f'{k} días previos'}", "spearman": round(rho, 2),
                        "p_valor": round(p, 3), "n_fechas": len(mm)})
R = pd.DataFrame(res)
R.to_csv(OUT / "correlaciones.csv", index=False)
print(R.to_string(index=False))
print("\nfechas en temporada con humedad:", len(m), "| total fechas Landsat con LST:", len(d))
print(m[["fecha", "satelite", "temporada", "das", "lst_lote", "dT_aire", "tvdi_lote", "lst_rel", "vwc_lote_0d", "vwc_min_0d", "franjas_bajo_umbral_0d"]].round(2).to_string(index=False))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, axs = plt.subplots(1, 3, figsize=(15, 4.2))
for ax, var, nom in zip(axs, ("tvdi_lote", "lst_rel", "dT_aire"),
                        ("TVDI del lote", "LST del lote − mediana vegetación (°C)", "LST del lote − T máx aire (°C)")):
    for t, g in m.groupby("temporada"):
        ax.scatter(g.vwc_lote_0d, g[var], label=f"GS{int(t)}", s=40)
    ax.set_xlabel("humedad promedio del lote (%)"); ax.set_ylabel(nom); ax.axvline(27.1, ls="--", c="gray", lw=0.8)
axs[0].legend()
fig.suptitle("Landsat térmico frente a la humedad del suelo en Nataima (fechas despejadas dentro de las siembras)")
fig.tight_layout(); fig.savefig(OUT / "grafica.png", dpi=100)
