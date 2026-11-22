"""Utilidades comunes para digitalizar figuras rasterizadas del artículo (Martínez-Vega et al. 2025)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf

RAIZ = Path(__file__).resolve().parents[3]
FIGS = RAIZ / "data" / "raw" / "figs"


def cargar_rgb(ruta: Path) -> np.ndarray:
    """Imagen como arreglo int (alto, ancho, 3)."""
    pm = pymupdf.Pixmap(str(ruta))
    a = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.h, pm.w, pm.n)[:, :, :3]
    return a.astype(int)


def corridas(v: np.ndarray) -> list[tuple[int, int]]:
    """Intervalos [inicio, fin) donde el vector booleano es verdadero."""
    d = np.diff(np.r_[0, v.astype(int), 0])
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def mascara_texto(a: np.ndarray) -> np.ndarray:
    lum, sat = a.mean(-1), a.max(-1) - a.min(-1)
    return (lum < 110) & (sat < 40)


@dataclass(frozen=True)
class EjeLineal:
    """valor = a + b * pixel."""
    a: float
    b: float

    def valor(self, px: float | np.ndarray) -> float | np.ndarray:
        return self.a + self.b * px

    def pixel(self, v: float | np.ndarray) -> float | np.ndarray:
        return (v - self.a) / self.b

    @classmethod
    def por_dos_puntos(cls, px1: float, v1: float, px2: float, v2: float) -> "EjeLineal":
        b = (v2 - v1) / (px2 - px1)
        return cls(v1 - b * px1, b)


def borde_izquierdo_plot(a: np.ndarray, fila: int) -> int:
    """Primera columna del fondo gris del área de trazado en esa fila."""
    lum = a[fila].mean(-1)
    return int(np.where((lum < 250) & (lum > 200))[0].min())


def eje_x_por_etiquetas(a: np.ndarray, fila_ini: int, fila_fin: int, col_ini: int, col_fin: int,
                        paso: int, primera_etiqueta: int = 1, ancho_min: int = 28,
                        ancho_max: int = 42) -> tuple[EjeLineal, np.ndarray]:
    """Calibra el eje de días con las etiquetas de dos dígitos (p. ej. 22, 29, 36…).

    `col_ini` debe ser el borde izquierdo del área de trazado: el grupo de texto más a la izquierda a
    partir de ahí es la etiqueta `primera_etiqueta` (el "1"). El valor de cada etiqueta de dos dígitos se
    asigna por su distancia a esa, de modo que una etiqueta partida o pegada a otra no desordena la
    secuencia. Devuelve el eje (día = a + b·x) y el residuo de cada etiqueta usada, en días.
    """
    banda = mascara_texto(a[fila_ini:fila_fin, col_ini:col_fin]).any(0)
    grupos: list[list[int]] = []
    for s, e in corridas(banda):
        if grupos and s - grupos[-1][1] < 6:
            grupos[-1][1] = e
        else:
            grupos.append([s, e])
    todos = np.array([col_ini + (s + e - 1) / 2 for s, e in grupos])
    centros = np.array([col_ini + (s + e - 1) / 2 for s, e in grupos if ancho_min <= e - s <= ancho_max])
    sep = np.median(np.diff(centros))
    k = np.round((centros - todos[0]) / sep)
    dias = primera_etiqueta + paso * k
    usar = np.ones(len(centros), bool)
    for _ in range(3):  # descarta etiquetas partidas (p. ej. "1" separado de "06")
        b, a0 = np.polyfit(centros[usar], dias[usar], 1)
        res = dias - (a0 + b * centros)
        usar = np.abs(res) < 0.4
    eje = EjeLineal(a0, b)
    return eje, res[usar]
