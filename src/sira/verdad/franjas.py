"""Polígonos de las 3 franjas (y sus 4 réplicas) del ensayo de Nataima.

Origen: plano georreferenciado de la Fig. 1 del artículo (cuadrícula en metros, MAGNA-SIRGAS Colombia
Bogotá, EPSG:3116). Se digitalizaron 3 esquinas del lote en píxeles de la figura y se pasaron a metros con
la cuadrícula de 50 m del plano. El lote es un rectángulo girado ≈33°, dividido en 3 franjas a lo ancho
(de oeste a este: MIRI, AWD+MIRI, CD) y cada franja en 4 réplicas a lo largo (Rep 1 al norte).

`desplazamiento_m` permite corregir la posición si al superponer sobre una imagen real el lote aparece
corrido (el plano y las coordenadas citadas en el texto difieren ≈200 m).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon, mapping
from shapely.ops import transform

RAIZ = Path(__file__).resolve().parents[3]

# calibración de ejes de la Fig. 1 (líneas de cuadrícula cada 50 m)
_XPX, _XM = np.array([412, 883, 1360]), np.array([901750, 901800, 901850])
_YPX, _YM = np.array([155, 648, 1142, 1636]), np.array([955150, 955100, 955050, 955000])
_AX, _BX = np.polyfit(_XPX, _XM, 1)
_AY, _BY = np.polyfit(_YPX, _YM, 1)


def _m(px: tuple[float, float]) -> np.ndarray:
    return np.array([_AX * px[0] + _BX, _AY * px[1] + _BY])


ESQ_OESTE, ESQ_NORTE, ESQ_SUR = _m((113, 1197)), _m((820, 75)), _m((660, 1597))
TRATAMIENTOS = ("MIRI", "AWD+MIRI", "CD")  # de oeste a este


def poligonos(desplazamiento_m: tuple[float, float] = (0.0, 0.0), crs: str = "EPSG:4326") -> dict:
    """Polígonos {'franjas': {trat: Polygon}, 'replicas': {(trat, rep): Polygon}} en el CRS pedido."""
    W = ESQ_OESTE + np.array(desplazamiento_m)
    L, A = ESQ_NORTE - ESQ_OESTE, ESQ_SUR - ESQ_OESTE

    def quad(a0: float, a1: float, l0: float = 0.0, l1: float = 1.0) -> Polygon:
        return Polygon([W + A * a0 + L * l0, W + A * a1 + L * l0, W + A * a1 + L * l1, W + A * a0 + L * l1])

    tr = Transformer.from_crs("EPSG:3116", crs, always_xy=True).transform
    franjas = {t: transform(tr, quad(i / 3, (i + 1) / 3)) for i, t in enumerate(TRATAMIENTOS)}
    replicas = {(t, r): transform(tr, quad(i / 3, (i + 1) / 3, (4 - r) / 4, (5 - r) / 4))
                for i, t in enumerate(TRATAMIENTOS) for r in (1, 2, 3, 4)}
    return {"franjas": franjas, "replicas": replicas}


def exportar_geojson(desplazamiento_m: tuple[float, float] = (0.0, 0.0)) -> Path:
    p = poligonos(desplazamiento_m)
    feats = [{"type": "Feature", "properties": {"tipo": "franja", "tratamiento": t}, "geometry": mapping(g)}
             for t, g in p["franjas"].items()]
    feats += [{"type": "Feature", "properties": {"tipo": "replica", "tratamiento": t, "replica": r},
               "geometry": mapping(g)} for (t, r), g in p["replicas"].items()]
    out = RAIZ / "data" / "processed" / "franjas_nataima.geojson"
    out.write_text(json.dumps({"type": "FeatureCollection", "features": feats,
                               "properties": {"desplazamiento_m": list(desplazamiento_m)}}, indent=1),
                   encoding="utf-8")
    return out


if __name__ == "__main__":
    print(exportar_geojson())
