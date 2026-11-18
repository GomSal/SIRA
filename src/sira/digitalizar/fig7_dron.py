"""Fig. 7 del artículo: índices del dron (NDVI, NDRE, OSAVI) por temporada × etapa × tratamiento.

Filas: NDVI (eje 0,5–1,0), NDRE y OSAVI (eje 0–1,0). Columnas: GS1, GS2, GS3. En cada panel hay 9 barras:
3 etapas (vegetativa, reproductiva, maduración) × 3 tratamientos (CD azul, MIRI verde, AWD+MIRI naranja).
El valor es el tope de la barra (el color de la barra, sin la barra de error), calibrado con las etiquetas del eje y.
Salida: data/processed/dron_indices.csv
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .comun import FIGS, RAIZ, EjeLineal, cargar_rgb, corridas, mascara_texto

COLORES = {"CD": (31, 78, 120), "MIRI": (58, 86, 37), "AWD+MIRI": (198, 92, 18)}
FILAS = {"NDVI": ((80, 420), np.arange(1.0, 0.45, -0.1)),
         "NDRE": ((480, 820), np.arange(1.0, -0.05, -0.2)),
         "OSAVI": ((890, 1220), np.arange(1.0, -0.05, -0.2))}
# columnas de las etiquetas del eje y y de las barras en cada panel (píxeles de la imagen original)
PANELES = {1: ((35, 105), (105, 690)), 2: ((720, 790), (790, 1370)), 3: ((1370, 1440), (1440, 2010))}
ETAPAS = ("vegetativa", "reproductiva", "maduracion")


def eje_y(a: np.ndarray, filas: tuple[int, int], cols: tuple[int, int], valores: np.ndarray) -> EjeLineal:
    banda = mascara_texto(a[filas[0]:filas[1], cols[0]:cols[1]]).any(1)
    centros = np.array([filas[0] + (s + e - 1) / 2 for s, e in corridas(banda) if 15 <= e - s <= 30])
    assert len(centros) == len(valores), (filas, cols, len(centros))
    b, a0 = np.polyfit(centros, valores, 1)
    return EjeLineal(a0, b)


def barras(a: np.ndarray, filas: tuple[int, int], cols: tuple[int, int], ey: EjeLineal) -> list[tuple[str, float, float]]:
    sub = a[filas[0]:filas[1], cols[0]:cols[1]].astype(int)
    out = []
    for trat, c in COLORES.items():
        m = np.abs(sub - np.array(c)).sum(-1) < 60
        colm = m.sum(0) > 20  # columnas que tienen un tramo largo de ese color
        for s, e in corridas(colm):
            if e - s < 15:
                continue
            centro = slice(s + (e - s) // 4, e - (e - s) // 4)
            filas_bar = np.where(m[:, centro].mean(1) > 0.5)[0]
            tope = filas[0] + filas_bar.min()
            out.append((trat, cols[0] + (s + e) / 2, float(ey.valor(tope - 0.5))))
    return sorted(out, key=lambda x: x[1])


def digitalizar() -> pd.DataFrame:
    a = cargar_rgb(FIGS / "fig7.png")
    filas = []
    for indice, (rango, valores) in FILAS.items():
        for t, (col_eje, col_bar) in PANELES.items():
            ey = eje_y(a, rango, col_eje, valores)
            bs = barras(a, rango, col_bar, ey)
            assert len(bs) == 9, (indice, t, len(bs))
            for k, (trat, x, v) in enumerate(bs):
                filas.append({"temporada": t, "etapa": ETAPAS[k // 3], "tratamiento": trat, "indice": indice,
                              "valor": round(v, 3)})
    return pd.DataFrame(filas)


if __name__ == "__main__":
    d = digitalizar()
    d.to_csv(RAIZ / "data" / "processed" / "dron_indices.csv", index=False)
    print(d.pivot_table(index=["indice", "temporada", "etapa"], columns="tratamiento", values="valor")
          [["CD", "MIRI", "AWD+MIRI"]].round(3).to_string())
