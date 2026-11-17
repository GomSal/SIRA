"""Mide cuántos píxeles HLS/Sentinel-2 y fichas de Prithvi caben en cada franja del lote de Nataima.

Esquinas tomadas del plano georreferenciado (Fig. 1 de Martínez-Vega et al. 2025, MAGNA-SIRGAS
Colombia Bogotá, EPSG:3116) en píxeles de la imagen extraída del PDF.
"""
import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon, box
from shapely.ops import transform

# Calibración de ejes de la figura (líneas de la cuadrícula cada 50 m)
XPX, XM = np.array([412, 883, 1360]), np.array([901750, 901800, 901850])
YPX, YM = np.array([155, 648, 1142, 1636]), np.array([955150, 955100, 955050, 955000])
ax, bx = np.polyfit(XPX, XM, 1)
ay, by = np.polyfit(YPX, YM, 1)


def px2m(p):
    return np.array([ax * p[0] + bx, ay * p[1] + by])


W = px2m((113, 1197))   # esquina oeste (MIRI rep 4)
N = px2m((820, 75))     # esquina norte (MIRI rep 1)
S = px2m((660, 1597))   # esquina sur (CD rep 4)
L, A = N - W, S - W     # lado largo (a lo largo de la franja) y ancho (a través de las 3 franjas)

TRAT = ["MIRI", "AWD+MIRI", "CD"]


def quad(a0, a1, l0=0.0, l1=1.0):
    pts = [W + A * a0 + L * l0, W + A * a1 + L * l0, W + A * a1 + L * l1, W + A * a0 + L * l1]
    return Polygon(pts)


to_utm = Transformer.from_crs("EPSG:3116", "EPSG:32618", always_xy=True).transform
to_ll = Transformer.from_crs("EPSG:3116", "EPSG:4326", always_xy=True).transform

field = quad(0, 1)
lon, lat = to_ll(*field.centroid.coords[0])
print(f"Centro del lote: {lat:.5f}, {lon:.5f}  (paper: 4.19131, -74.96089)")
print(f"Lado largo {np.linalg.norm(L):.1f} m, ancho total {np.linalg.norm(A):.1f} m, "
      f"área {field.area:.0f} m², ancho por franja {np.linalg.norm(A)/3:.1f} m, "
      f"rumbo {np.degrees(np.arctan2(L[0], L[1])):.0f}°")

strips = {t: transform(to_utm, quad(i / 3, (i + 1) / 3)) for i, t in enumerate(TRAT)}
field_utm = transform(to_utm, field)
minx, miny, maxx, maxy = field_utm.bounds
print(f"Caja UTM del lote: {maxx-minx:.0f} × {maxy-miny:.0f} m")


def pixels(res):
    """Rejilla alineada a múltiplos de res en UTM 18N (rejilla MGRS de HLS y Sentinel-2)."""
    x0, y0 = np.floor(minx / res) * res - res, np.floor(miny / res) * res - res
    for x in np.arange(x0, maxx + res, res):
        for y in np.arange(y0, maxy + res, res):
            yield box(x, y, x + res, y + res)


print("\nPíxeles por franja (fracción del píxel dentro de la franja):")
print(f"{'res':>5} {'franja':>9} {'100%':>5} {'>=75%':>6} {'>=50%':>6} {'pureza máx':>11}")
for res, uso in [(10, "S2 B2-B4,B8"), (20, "S2 B8A,B11,B12 (SWIR)"), (30, "HLS / Prithvi")]:
    pix = list(pixels(res))
    for t, poly in strips.items():
        fr = np.array([p.intersection(poly).area / p.area for p in pix])
        print(f"{res:>4}m {t:>9} {np.sum(fr > 0.999):>5} {np.sum(fr >= .75):>6} {np.sum(fr >= .5):>6} "
              f"{fr.max():>10.0%}   ({uso})")

print("\nFichas de Prithvi (16×16 píxeles):")
for res in (30, 10):
    tok = 16 * res
    best = None
    for ox in np.arange(0, tok, 5):
        for oy in np.arange(0, tok, 5):
            n = len({(int((x - ox) // tok), int((y - oy) // tok))
                     for x, y in field_utm.exterior.coords})
            best = n if best is None else min(best, n)
    print(f"  píxel {res} m → ficha {tok} m: el lote completo cabe en {best} ficha(s) "
          f"con la mejor alineación; caja del lote {maxx-minx:.0f}×{maxy-miny:.0f} m")
