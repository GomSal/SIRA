"""Arma los datos fijos de la app SIRA (Lovable) a partir del semáforo ya corrido.

Salida en app/datos/:
- zonas.json: Tolima como zona habilitada (contorno de geoBoundaries ADM1, OSM), sus municipios con su centro
  (geoBoundaries ADM2, DANE) para el buscador, y la zona piloto donde hoy hay semáforo (límites de semaforo_c).
- semaforo.json: el semáforo por pixel de 10 m en arreglos por columna (centro lat/lon, días desde siembra,
  días hasta rojo y, por horizonte, color, déficit pintado, humedad equivalente y marca del NDMI).

Lee reports/semaforo_c/ (resumen.json, pixeles.csv.gz), la rejilla del cubo de A (n, x0, y0) y
data/referencia/tolima/ (adm1.geojson, adm2.geojson).

    python scripts/operacion/exportar_app.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

RAIZ = Path(__file__).resolve().parents[2]
SEM = RAIZ / "reports/semaforo_c"
CUBO = RAIZ / "data/interim/modelo_a/kaggle_nb1/datos_a/cubo.npz"
REF = RAIZ / "data/referencia/tolima"
OUT = RAIZ / "app/datos"
OUT.mkdir(parents=True, exist_ok=True)
HORIZONTES = (0, 7, 15, 30)


def anillo_mayor(g):
    """Anillo exterior más largo de un Polygon o MultiPolygon, como [[lon, lat], …]."""
    if g["type"] == "Polygon":
        return g["coordinates"][0]
    return max((p[0] for p in g["coordinates"]), key=len)


def dentro(lon, lat, anillo):
    a = np.asarray(anillo)
    x, y = a[:, 0], a[:, 1]
    xj, yj = np.roll(x, 1), np.roll(y, 1)
    cruza = (y > lat) != (yj > lat)
    xi = (xj - x) * (lat - y) / np.where(yj - y == 0, 1e-12, yj - y) + x
    return bool(np.sum(cruza & (lon < xi)) % 2)


# ---------------------------------------------------------------- zonas
adm1 = json.loads((REF / "adm1.geojson").read_text(encoding="utf-8"))
tol = next(f for f in adm1["features"] if f["properties"]["shapeName"] == "Tolima")
anillo = [[round(x, 5), round(y, 5)] for x, y in anillo_mayor(tol["geometry"])]

adm2 = json.loads((REF / "adm2.geojson").read_text(encoding="utf-8"))
municipios = []
for f in adm2["features"]:
    r = np.asarray(anillo_mayor(f["geometry"]))
    lon, lat = r[:-1, 0].mean(), r[:-1, 1].mean()
    if dentro(lon, lat, anillo):
        municipios.append({"nombre": f["properties"]["shapeName"], "lat": round(lat, 4), "lng": round(lon, 4)})
municipios.sort(key=lambda m: m["nombre"])

R = json.loads((SEM / "resumen.json").read_text(encoding="utf-8"))
(lat_s, lon_o), (lat_n, lon_e) = R["limites_latlon"]
zonas = {
    "version": 1,
    "nota": "Tolima: contorno simplificado de geoBoundaries (OSM, ADM1); municipios de geoBoundaries ADM2 (DANE).",
    "vistaInicial": {"lat": 4.05, "lng": -75.2, "zoom": 8},
    "mensajeFueraDeZona": "Por ahora SIRA solo funciona en el Tolima. Pronto llegaremos a más regiones.",
    "zonas": {"type": "FeatureCollection", "features": [{
        "type": "Feature", "id": "tolima",
        "properties": {"nombre": "Tolima", "tipo": "departamento", "codigoDane": "73", "habilitada": True,
                       "color": "#1F6B4A"},
        "geometry": {"type": "Polygon", "coordinates": [anillo]}}]},
    "zonaPiloto": {"nombre": "Zona piloto Nataima (El Espinal)", "limites_latlon": R["limites_latlon"],
                   "zona_ha": R["zona_ha"]},
    "municipios": municipios,
}
(OUT / "zonas.json").write_text(json.dumps(zonas, ensure_ascii=False), encoding="utf-8")
assert dentro((lon_o + lon_e) / 2, (lat_s + lat_n) / 2, anillo), "la zona piloto debe quedar dentro del Tolima"

# ---------------------------------------------------------------- semáforo por pixel
z = np.load(CUBO)
NG, X0, Y0 = int(z["n"]), float(z["x0"]), float(z["y0"])
P = pd.read_csv(SEM / "pixeles.csv.gz")
n_agricolas = len(P)
P = P[(P[[f"clase_h{h}" for h in HORIZONTES]] >= 0).any(axis=1)].reset_index(drop=True)  # solo los que están en cultivo
fila, col = np.divmod(P.pixel.to_numpy(), NG)
x, y = X0 + 10 * col + 5, Y0 - 10 * fila - 5  # centro del pixel en UTM 18N
lon, lat = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True).transform(x, y)
assert lat_s <= lat.min() and lat.max() <= lat_n and lon_o <= lon.min() and lon.max() <= lon_e


def r(v, d):
    return [None if pd.isna(a) else round(float(a), d) for a in v]


sem = {
    "hoy": R["hoy"], "geos_inicio": R["geos_inicio"], "miembros_geos": R["miembros_geos"],
    "modelo_b": R["modelo_b"], "limites_latlon": R["limites_latlon"], "celda_m": 10,
    "horizontes": [{"h": h["h"], "fecha": h["fecha"], "lluvia_mm": round(h["lluvia_mm_hasta_h"], 1),
                    "eto_mm": round(h["eto_mm_hasta_h"], 1)} for h in R["horizontes"]],
    "umbrales": {"rojo_dr_mm": 4.34, "verde_dr_mm": 0.0, "rojo_humedad_pct": 27.1, "verde_humedad_pct": 30.0,
                 "mm_por_punto": 1.5},
    "pixeles": {
        "n": len(P), "lat": r(lat, 6), "lon": r(lon, 6), "das_hoy": r(P.das_hoy, 0),
        "dias_hasta_rojo": P.dias_hasta_baja.astype(int).tolist(),
        **{f"clase_h{h}": P[f"clase_h{h}"].astype(int).tolist() for h in HORIZONTES},
        **{f"deficit_mm_h{h}": r(P[f"deficit_mm_h{h}"], 2) for h in HORIZONTES},
        **{f"humedad_pct_h{h}": r(P[f"humedad_pct_h{h}"], 1) for h in HORIZONTES},
        **{f"marca_h{h}": P[f"marca_h{h}"].astype(int).tolist() for h in HORIZONTES},
    },
}
(OUT / "semaforo.json").write_text(json.dumps(sem, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"zonas.json: Tolima {len(anillo)} vértices, {len(municipios)} municipios | "
      f"semaforo.json: {len(P)} pixeles en cultivo de {n_agricolas} agrícolas, hoy {R['hoy']}, "
      f"{(OUT / 'semaforo.json').stat().st_size / 1e6:.2f} MB")
