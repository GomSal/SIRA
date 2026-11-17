"""Fig. 4: riego acumulado por tratamiento (líneas CD, MIRI, AWD+MIRI) y lluvia acumulada (relleno azul).

Las líneas son finas, semitransparentes y la compresión JPEG les quita color, sobre todo cuando pasan
por el relleno azul. Por eso cada serie se reconstruye como el camino más probable (Viterbi) a través
de la imagen, columna por columna de píxel:
- puntaje de cada píxel: oscuro (luminancia < 160) y con el tono de la serie suma; oscuro con otro
  tono suma poco (dos líneas superpuestas); claro resta (línea tapada);
- el camino es continuo y el acumulado nunca baja: entre columnas vecinas sube como mucho 8 px y baja
  como mucho 1 px (bordes suavizados);
- empieza en 0 mm y termina en el total de la Tabla 2 del artículo (único ancla externa).
Las sumas por etapa impresas en la propia figura NO se usan para digitalizar: quedan para validar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .comun import FIGS, EjeLineal, cargar_rgb, corridas, eje_x_por_etiquetas, mascara_texto

PANELES = {1: (0, 830), 2: (830, 1700), 3: (1700, 2481)}
SERIES = ("CD", "MIRI", "AWD+MIRI")
# Totales de riego (mm) por temporada, Tabla 2 del artículo: anclan el final de cada camino.
TOTAL_TABLA2 = {1: (1010.6, 517.9, 367.3), 2: (404.7, 316.8, 210.5), 3: (820.0, 376.0, 291.5)}
RELLENO = np.array((87, 193, 233))
SUBE_MAX_PX, BAJA_MAX_PX = 8, 1


def eje_y(a: np.ndarray, f0: int, f1: int) -> tuple[EjeLineal, np.ndarray]:
    """Etiquetas 0, 200, …, 1200 del eje izquierdo (columnas 95–180): centro vertical de cada número."""
    banda = mascara_texto(a[f0:f1, 95:180]).any(1)
    grupos = [(f0 + s, f0 + e) for s, e in corridas(banda) if 18 <= e - s <= 32]
    centros = np.sort([(s + e - 1) / 2 for s, e in grupos])[::-1][:7]
    valores = np.arange(len(centros)) * 200.0
    b, a0 = np.polyfit(centros, valores, 1)
    eje = EjeLineal(a0, b)
    return eje, valores - eje.valor(centros)


def tonos_pixel(a: np.ndarray) -> np.ndarray:
    """0 = CD (azul pizarra), 1 = MIRI (verde), 2 = AWD+MIRI (naranja), −1 indeterminado."""
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    t = np.full(r.shape, -1)
    t[(b > g) & (b - r > 20)] = 0
    t[(g > r) & (g >= b - 3)] = 1
    t[r > np.maximum(g, b) + 5] = 2
    return t


# Fondos posibles: gris claro, gris de etapa, relleno de lluvia sobre gris claro y sobre gris de etapa.
FONDOS = np.array([(240, 240, 240), (226, 226, 226), (87, 193, 233), (76, 182, 222)], float)


def es_linea(sub: np.ndarray) -> np.ndarray:
    """Píxel de línea: oscuro y lejos (en RGB) de todos los fondos. En la etapa reproductiva el relleno
    azul tiene luminancia ≈160, así que un umbral fijo de luminancia no basta."""
    dist = np.min(np.linalg.norm(sub[..., None, :] - FONDOS, axis=-1), axis=-1)
    return (sub.mean(-1) < 165) & (dist > 45)


def puntaje(sub: np.ndarray, k: int) -> np.ndarray:
    oscuro = es_linea(sub)
    t = tonos_pixel(sub)
    s = np.where(oscuro, np.where(t == k, 1.0, np.where(t == -1, 0.4, 0.1)), -0.6)
    return s


def viterbi(score: np.ndarray, fila_ini: int, fila_fin: int) -> np.ndarray:
    """Camino de máximo puntaje; filas crecen hacia abajo (subir el acumulado = fila menor)."""
    n_f, n_c = score.shape
    NEG = -1e9
    acum = np.full(n_f, NEG)
    acum[max(fila_ini - 3, 0):fila_ini + 4] = score[max(fila_ini - 3, 0):fila_ini + 4, 0]
    previo = np.zeros((n_c, n_f), dtype=np.int16)
    desplaz = range(-BAJA_MAX_PX, SUBE_MAX_PX + 1)  # +d = la fila de antes estaba d más abajo
    for c in range(1, n_c):
        mejor = np.full(n_f, NEG)
        arg = np.zeros(n_f, dtype=np.int16)
        for d in desplaz:
            cand = np.full(n_f, NEG)
            if d >= 0:
                cand[: n_f - d] = acum[d:] - 0.03 * abs(d)
            else:
                cand[-d:] = acum[: n_f + d] - 0.03 * abs(d)
            m = cand > mejor
            mejor[m], arg[m] = cand[m], d
        acum = mejor + score[:, c]
        previo[c] = arg
    fin = np.full(n_f, NEG)
    fin[max(fila_fin - 4, 0):fila_fin + 5] = acum[max(fila_fin - 4, 0):fila_fin + 5]
    fila = int(np.argmax(fin))
    camino = np.zeros(n_c, dtype=int)
    for c in range(n_c - 1, -1, -1):
        camino[c] = fila
        fila += int(previo[c, fila])
    return camino


def centrar(linea: np.ndarray, camino: np.ndarray) -> np.ndarray:
    """Mueve cada punto del camino al centro del tramo oscuro que lo contiene (si lo hay)."""
    out = camino.astype(float).copy()
    for c, f in enumerate(camino):
        col = linea[:, c]
        if not col[f]:
            continue
        s = f
        while s > 0 and col[s - 1]:
            s -= 1
        e = f
        while e < len(col) - 1 and col[e + 1]:
            e += 1
        if e - s <= 12:
            out[c] = (s + e) / 2
    return out


def riego_diario(acum: np.ndarray, umbral_evento: float = 8.0) -> np.ndarray:
    """Lámina por día a partir del acumulado.

    En la esquina de cada escalón el centro de la línea gruesa queda un poco por debajo de la meseta,
    así que un riego aparece como un salto grande el día d y una "cola" pequeña el día d+1. La cola
    (< 45 % del salto y < 15 mm) se devuelve al día d. El total no cambia."""
    inc = np.diff(np.r_[0.0, acum])
    inc = np.where(inc < 0, 0, inc)
    for i in range(len(inc) - 1):
        if inc[i] >= umbral_evento and 0 < inc[i + 1] < min(0.45 * inc[i], 15):
            inc[i] += inc[i + 1]
            inc[i + 1] = 0.0
    return np.round(inc, 1)


def digitalizar_panel(a: np.ndarray, temporada: int) -> tuple[pd.DataFrame, dict]:
    f0, f1 = PANELES[temporada]
    ey, res_y = eje_y(a, f0, f1)
    fila0 = float(ey.pixel(0.0))
    # Filas +20…+60 bajo el cero: solo las etiquetas de días (el "0" del eje y queda más arriba)
    ex, res_x = eje_x_por_etiquetas(a, int(fila0) + 20, int(fila0) + 60, 150, 1250, paso=7)
    lum_base = a[int(fila0) - 5].mean(-1)
    borde_der = int(np.where(lum_base < 250)[0].max())
    dia_max = int(np.floor(ex.valor(borde_der) - 0.5))
    dias = np.arange(1, dia_max + 1)
    # Los puntos de la serie están en la marca de cada día y se unen con rectas: un riego aparece como
    # una rampa de un día completo (de la marca d−1 a la marca d). El acumulado del día d se lee en x(d).
    xs = np.minimum(np.round(ex.pixel(dias.astype(float))).astype(int), borde_der - 2)

    top = int(ey.pixel(1150.0))
    bot = int(round(fila0)) + 4
    c0, c1 = xs[0] - 10, borde_der - 2
    sub = a[top:bot, c0:c1 + 1]
    linea = es_linea(sub)
    df = pd.DataFrame({"temporada": temporada, "das": dias})
    escalas: dict[str, float] = {}
    for k, nombre in enumerate(SERIES):
        fila_fin = int(round(ey.pixel(TOTAL_TABLA2[temporada][k]))) - top
        camino = viterbi(puntaje(sub, k), int(round(fila0)) - top, fila_fin)
        valores = ey.valor(centrar(linea, camino) + top)
        v = np.maximum.accumulate(np.clip(valores, 0, None))
        diario = riego_diario(v[xs - c0])
        # El centro de la línea queda ≈0,5–1 % por debajo del valor (sesgo de 1–2 px de la calibración con
        # el centro de las etiquetas). Se reescala para que el total sea el de la Tabla 2.
        factor = TOTAL_TABLA2[temporada][k] / diario.sum()
        escalas[nombre] = round(float(factor), 4)
        diario = np.round(diario * factor, 1)
        df[f"riego_mm_{nombre}"] = diario
        df[f"riego_acum_{nombre}"] = np.round(np.cumsum(diario), 1)

    # lluvia acumulada: tope del relleno azul, contiguo desde la base (evita el texto azul claro)
    rel = np.abs(a - RELLENO).sum(-1) < 60
    # (las líneas cruzan el relleno, así que se busca el píxel de relleno más alto bajo 800 mm,
    #  por debajo de las etiquetas de texto azul claro)
    f_800 = int(ey.pixel(800.0))
    tope = []
    for c in xs:
        filas = np.where(rel[f_800:int(fila0) - 1, c - 1:c + 2].all(1))[0]
        tope.append(float(ey.valor(f_800 + filas.min() - 0.5)) if len(filas) else 0.0)
    df["lluvia_acum_fig4"] = np.maximum.accumulate(np.round(np.clip(tope, 0, None), 1))
    meta = {"temporada": temporada, "fila_0mm": fila0, "px_por_100mm": abs(100 / ey.b),
            "px_por_dia": 1 / ex.b, "x_dia1": float(ex.pixel(1.0)), "dia_max": dia_max,
            "residuo_y_mm_max": float(np.abs(res_y).max()), "residuo_x_dias_max": float(np.abs(res_x).max()),
            **{f"escala_{s}": f for s, f in escalas.items()}}
    return df, meta


def digitalizar() -> tuple[pd.DataFrame, pd.DataFrame]:
    a = cargar_rgb(FIGS / "fig4.png")
    dfs, metas = zip(*(digitalizar_panel(a, t) for t in (1, 2, 3)))
    return pd.concat(dfs, ignore_index=True), pd.DataFrame(metas)
