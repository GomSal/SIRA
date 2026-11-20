"""Fig. 2: lluvia diaria (barras azules, eje izq. 0–120 mm) y AtWD (línea roja, eje der. −50 a 10 mm).

Calibración por panel:
- eje x: etiquetas de días (1, 8, 15, … cada 7) detectadas en la imagen;
- eje y: fila de la serie en 0 mm (puntos azules de la base) y línea discontinua gris de 100 mm.
  El eje derecho está alineado con el izquierdo: AtWD = lluvia_equivalente / 2 − 50.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .comun import FIGS, EjeLineal, borde_izquierdo_plot, cargar_rgb, corridas, eje_x_por_etiquetas

# Paneles a, b, c = temporadas GS1, GS2, GS3. Rango de filas donde buscar cada panel.
PANELES = {1: (0, 790), 2: (790, 1590), 3: (1590, 2376)}
COL_PLOT = (150, 1500)


def mascaras(a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    azul = (b - r > 40) & (b >= g) & (b > 100)
    rojo = (r - g > 80) & (r > 150)
    return azul, rojo


def fila_base(azul: np.ndarray, f0: int, f1: int) -> float:
    """Fila de 0 mm: la de más píxeles azules (la serie punteada en cero) dentro del panel."""
    filas = azul[f0:f1, COL_PLOT[0]:COL_PLOT[1]].sum(1)
    pico = int(np.argmax(filas))
    vec = filas[max(pico - 3, 0):pico + 4]
    idx = np.arange(max(pico - 3, 0), pico + 4)[: len(vec)]
    return f0 + float(np.average(idx, weights=vec))


def fila_100mm(a: np.ndarray, base: float) -> float:
    """Línea discontinua gris oscura de 100 mm: buscar ~400–560 px por encima de la base."""
    lum, sat = a.mean(-1), a.max(-1) - a.min(-1)
    gris = (lum < 170) & (sat < 25)
    f0, f1 = int(base - 560), int(base - 400)
    filas = gris[f0:f1, 400:1300].sum(1)
    pico = int(np.argmax(filas))
    idx = np.arange(pico - 2, pico + 3)
    return f0 + float(np.average(idx, weights=filas[idx]))


def borde_derecho_plot(a: np.ndarray, base: float) -> int:
    """Última columna del fondo gris del área de trazado, justo encima de la base."""
    lum = a[int(base) - 30].mean(-1)
    return int(np.where((lum < 250) & (lum > 200))[0].max())


def digitalizar_panel(a: np.ndarray, temporada: int) -> tuple[pd.DataFrame, dict]:
    azul, rojo = mascaras(a)
    # "Tinta": cualquier píxel que no sea fondo (barra, línea roja encima, mezcla de ambas)
    tinta = (a.mean(-1) < 200) | ((a.max(-1) - a.min(-1)) > 40)
    f0, f1 = PANELES[temporada]
    base = fila_base(azul, f0, f1)
    y100 = fila_100mm(a, base)
    ey = EjeLineal.por_dos_puntos(base, 0.0, y100, 100.0)  # mm de lluvia por fila
    izq = borde_izquierdo_plot(a, int(base) - 30)
    ex, res_x = eje_x_por_etiquetas(a, int(base) + 8, int(base) + 48, izq, 1250, paso=7)

    borde_der = borde_derecho_plot(a, base)
    dia_max = int(np.floor(ex.valor(borde_der) - 0.5))
    filas = []
    fila_atwd_previa: float | None = None
    for dia in range(1, dia_max + 1):
        xc = ex.pixel(dia)
        cols = slice(int(round(xc)) - 1, int(round(xc)) + 2)
        # Lluvia: subir desde la base mientras haya azul. La línea roja puede tapar la barra en
        # tramos largos (caídas bruscas del AtWD), así que el rojo se cruza sin límite; los píxeles
        # que no son ni azules ni rojos (bordes suavizados) se toleran hasta 6 seguidos.
        col_az = azul[:, cols].any(1)
        col_tinta = tinta[:, cols].any(1)
        y = int(round(base))
        tope = y
        hueco = 0
        while y > base - 125 * abs(1 / ey.b):
            if col_az[y]:
                tope, hueco = y, 0
            elif col_tinta[y]:
                hueco = 0
            else:
                hueco += 1
                if hueco > 3:
                    break
            y -= 1
        alto_px = base - tope
        lluvia = max(0.0, float(ey.valor(tope))) if alto_px > 3.5 else 0.0
        # AtWD: tramo rojo de la columna más cercano al del día anterior (continuidad). Evita tomar la
        # muestra roja de la leyenda, que está en la misma zona de varias columnas.
        tramos = [(f0 + (s + e - 1) / 2) for s, e in corridas(rojo[f0:f1, cols].any(1))]
        if tramos:
            ref = fila_atwd_previa if fila_atwd_previa is not None else float(np.median(tramos))
            fila_atwd_previa = min(tramos, key=lambda f: abs(f - ref))
            atwd = float(ey.valor(fila_atwd_previa) / 2 - 50)
        else:
            atwd = np.nan
        filas.append({"temporada": temporada, "das": dia, "lluvia_mm": round(lluvia, 1),
                      "atwd_mm": round(atwd, 2) if not np.isnan(atwd) else np.nan})
    meta = {"temporada": temporada, "fila_0mm": base, "fila_100mm": y100, "px_por_mm": 1 / -ey.b,
            "px_por_dia": 1 / ex.b, "x_dia1": ex.pixel(1), "residuo_etiquetas_dias_max": float(np.abs(res_x).max()),
            "n_etiquetas": int(len(res_x))}
    return pd.DataFrame(filas), meta


def digitalizar() -> tuple[pd.DataFrame, pd.DataFrame]:
    a = cargar_rgb(FIGS / "fig2.png")
    dfs, metas = zip(*(digitalizar_panel(a, t) for t in (1, 2, 3)))
    return pd.concat(dfs, ignore_index=True), pd.DataFrame(metas)
