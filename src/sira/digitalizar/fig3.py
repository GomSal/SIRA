"""Fig. 3: humedad volumétrica del suelo a 20 cm (TDR), media ± error estándar por tratamiento.

Solo se digitalizan las MEDICIONES (los marcadores con barra de error). Las curvas suavizadas que los unen
las dibuja Excel y no son datos: entre puntos pueden pasar por valores que nadie midió.

Cómo se encuentra cada medición:
1. Topes de barra de error: trazos horizontales finos de 13–22 px (≤ 5 filas de grosor).
2. Dos topes con el mismo centro horizontal (±3 px) y unidos por una línea vertical forman una barra.
   La media es el punto medio (la barra es simétrica: media ± EE) y el EE es la mitad de su largo.
3. El tratamiento sale del color del marcador en el centro de la barra (azul CD, verde MIRI,
   naranja AWD+MIRI), que es la parte más gruesa y la que mejor conserva el color tras la compresión.
4. El día se obtiene del eje x y se redondea; se guarda cuánto se desvía del entero como control.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .comun import FIGS, EjeLineal, cargar_rgb, corridas, eje_x_por_etiquetas, mascara_texto

PANELES = {1: (0, 830), 2: (830, 1660), 3: (1660, 2481)}
TRATAMIENTOS = ("CD", "MIRI", "AWD+MIRI")


def eje_y(a: np.ndarray, f0: int, f1: int) -> tuple[EjeLineal, np.ndarray]:
    """Etiquetas 50, 45, …, 10 (columnas 64–112): centro vertical de cada número."""
    banda = mascara_texto(a[f0:f1, 64:112]).any(1)
    centros = np.array([f0 + (s + e - 1) / 2 for s, e in corridas(banda) if 24 <= e - s <= 32])
    valores = 50.0 - 5.0 * np.arange(len(centros))
    b, a0 = np.polyfit(centros, valores, 1)
    eje = EjeLineal(a0, b)
    return eje, valores - eje.valor(centros)


def topes(tinta: np.ndarray, f0: int, f1: int, c0: int, c1: int) -> list[tuple[float, float]]:
    """(fila, columna central) de cada trazo horizontal fino de 13–22 px."""
    out = []
    for f in range(f0, f1):
        for s, e in corridas(tinta[f, c0:c1]):
            if not 13 <= e - s <= 22:
                continue
            s, e = s + c0, e + c0
            # fino: 4 filas arriba o abajo, el tramo ya es casi blanco
            arriba = tinta[max(f - 4, 0), s:e].mean()
            abajo = tinta[min(f + 4, tinta.shape[0] - 1), s:e].mean()
            if min(arriba, abajo) < 0.5:
                out.append((float(f), (s + e - 1) / 2))
    # fusionar las filas de un mismo tope (un tope grueso aparece en 2–3 filas seguidas)
    out.sort()
    fus: list[list[float]] = []  # [suma_filas, columna, última_fila, n]
    for f, c in out:
        for t in fus:
            if f - t[2] <= 2 and abs(c - t[1]) <= 3:
                t[0] += f
                t[2] = f
                t[3] += 1
                break
        else:
            fus.append([f, c, f, 1])
    return [(acc / n, c) for acc, c, _, n in fus]


def ancho_marcador(tinta: np.ndarray, fila: float, col: float) -> int:
    """Ancho máximo de tinta alrededor del punto (filas ±2, columnas ±9): el rombo mide ≥ 6 px,
    una barra vertical sola mide 2–3 px."""
    f, c = int(round(fila)), int(round(col))
    mejor = 0
    for ff in range(f - 2, f + 3):
        fila_t = tinta[ff, c - 9:c + 10]
        for s, e in corridas(fila_t):
            if s <= 9 + 2 and e >= 9 - 2:  # el tramo cubre el centro
                mejor = max(mejor, e - s)
    return mejor


def emparejar(tps: list[tuple[float, float]], tinta: np.ndarray) -> list[tuple[float, float, float]]:
    """Pares (fila_arriba, fila_abajo, columna): mismo centro, unidos por una barra vertical y con un
    marcador en el punto medio. Si varios pares comparten tope, gana el que tiene marcador más claro y,
    a igualdad, el más corto."""
    cand = []
    for i, (fi, ci) in enumerate(tps):
        for j, (fj, cj) in enumerate(tps):
            if fj <= fi + 3 or fj - fi > 140 or abs(cj - ci) > 3:
                continue
            col = (ci + cj) / 2
            c = int(round(col))
            if tinta[int(fi) + 1:int(fj), c - 1:c + 2].any(1).mean() < 0.9:
                continue
            ancho = ancho_marcador(tinta, (fi + fj) / 2, col)
            if ancho >= 6:
                cand.append((-min(ancho, 12), fj - fi, i, j, fi, fj, col))
    cand.sort()
    usados: set[int] = set()
    pares = []
    for _, _, i, j, fi, fj, col in cand:
        if i in usados or j in usados:
            continue
        usados.update((i, j))
        pares.append((fi, fj, col))
    return pares


MASCARAS_COLOR = {
    0: lambda r, g, b, l: (b > r + 25) & (b >= g - 5) & (l < 190),   # CD azul
    1: lambda r, g, b, l: (g > r + 5) & (g > b + 12) & (l < 190),    # MIRI verde
    2: lambda r, g, b, l: (r > g + 25) & (r > b + 45) & (l < 215),   # AWD+MIRI naranja
}


def marcadores_color(a: np.ndarray, f0: int, f1: int, c0: int, c1: int) -> list[tuple[int, float, float, int]]:
    """Rombos de cada color: (tratamiento, fila, columna, ancho). Las líneas finas (curvas, barras, topes)
    desaparecen con una apertura morfológica 4×4; sobreviven los rombos (≈8 px) y trozos gruesos de
    barra pegados a ellos. El centro del rombo es la zona de filas más anchas de cada componente."""
    from scipy import ndimage as ndi

    sub = a[f0:f1, c0:c1].astype(int)
    r, g, b = sub[..., 0], sub[..., 1], sub[..., 2]
    lum = sub.mean(-1)
    out = []
    for k, fn in MASCARAS_COLOR.items():
        m = ndi.binary_closing(fn(r, g, b, lum), np.ones((2, 2)))
        m = ndi.binary_opening(m, np.ones((4, 4)))
        lab, n = ndi.label(m)
        for i, sl in enumerate(ndi.find_objects(lab), start=1):
            comp = lab[sl] == i
            if comp.sum() < 20:
                continue
            anchos = comp.sum(1)
            # filas de rombo: 5–12 px de ancho (los topes de barra miden 13–18 px y 2–4 filas)
            anchas = np.where((anchos >= 5) & (anchos <= 12))[0]
            if len(anchas) == 0:
                continue
            # puede haber más de un rombo apilado en el mismo componente: separar por huecos
            grupos = np.split(anchas, np.where(np.diff(anchas) > 2)[0] + 1)
            for gr in grupos:
                if len(gr) < 4:
                    continue
                filas_c = comp[gr]
                cols = np.where(filas_c.any(0))[0]
                fila = sl[0].start + gr.mean()
                col = sl[1].start + np.average(np.arange(comp.shape[1]), weights=filas_c.sum(0))
                out.append((k, f0 + float(fila), c0 + float(col), int(anchos[gr].max())))
    return out


def color_en(a: np.ndarray, fila: float, col: float, min_votos: int = 4) -> int:
    """Tratamiento por mayoría de píxeles de color en una ventana 7×7 (−1 si no hay suficientes)."""
    f, c = int(round(fila)), int(round(col))
    w = a[f - 3:f + 4, c - 3:c + 4].astype(int)
    r, g, b, lum = w[..., 0], w[..., 1], w[..., 2], w.mean(-1)
    votos = np.array([fn(r, g, b, lum).sum() for fn in MASCARAS_COLOR.values()])
    return int(np.argmax(votos)) if votos.max() >= min_votos else -1


def barra_error(tinta: np.ndarray, fila: float, col: float, f_min: int, f_max: int) -> tuple[float, float] | None:
    """Busca hacia arriba y hacia abajo el tope horizontal (≥ 11 px) de la barra de error en esa columna.
    Devuelve (fila_tope_arriba, fila_tope_abajo) o None."""
    c = int(round(col))

    def ancho(ff: int) -> int:
        fila_t = tinta[ff, c - 12:c + 13]
        return max((e - s for s, e in corridas(fila_t) if s <= 14 and e >= 10), default=0)

    f = int(round(fila))
    arriba = abajo = None
    for ff in range(f - 6, max(f - 130, f_min), -1):
        if not tinta[ff, c - 1:c + 2].any():
            break
        if ancho(ff) >= 11 and ancho(ff - 1) < 11 + 4:
            arriba = ff
            # tope: centro de las filas anchas contiguas
            while ancho(ff - 1) >= 11:
                ff -= 1
            arriba = (arriba + ff) / 2
            break
    for ff in range(f + 6, min(f + 130, f_max)):
        if not tinta[ff, c - 1:c + 2].any():
            break
        if ancho(ff) >= 11:
            abajo = ff
            while ancho(ff + 1) >= 11:
                ff += 1
            abajo = (abajo + ff) / 2
            break
    if arriba is None or abajo is None:
        return None
    return arriba, abajo


def digitalizar_panel(a: np.ndarray, temporada: int) -> tuple[pd.DataFrame, dict]:
    f0, f1 = PANELES[temporada]
    ey, res_y = eje_y(a, f0, f1)
    fila10 = float(ey.pixel(10.0))
    ex, res_x = eje_x_por_etiquetas(a, int(fila10) + 8, int(fila10) + 62, 118, 1300, paso=8,
                                    ancho_min=22, ancho_max=34)
    tinta = a.mean(-1) < 200
    c0, c1 = int(ex.pixel(1.0)) - 10, int(ex.pixel(124.0))
    f_top, f_bot = int(ey.pixel(50.0)), int(ey.pixel(18.0))  # bajo 18 % está la leyenda
    filas = []
    for k, fila, col, ancho in marcadores_color(a, f_top, f_bot, c0, c1):
        barra = barra_error(tinta, fila, col, f_top, int(fila10))
        if barra is not None and abs((barra[0] + barra[1]) / 2 - fila) <= 4:
            centro, ee = (barra[0] + barra[1]) / 2, abs(float(ey.b)) * (barra[1] - barra[0]) / 2
            fuente = "barra"
        else:
            centro, ee, fuente = fila, np.nan, "rombo"
        dia_f = float(ex.valor(col))
        filas.append({"temporada": temporada, "dia_x": round(dia_f, 2), "tratamiento": TRATAMIENTOS[k],
                      "vwc_pct": round(float(ey.valor(centro)), 2), "ee_pct": round(ee, 2) if ee == ee else np.nan,
                      "fuente": fuente, "ancho_rombo": ancho, "_fila": fila,
                      "_err_barra": abs((barra[0] + barra[1]) / 2 - fila) if barra else np.inf})
    # Segundo detector, independiente del color de la línea: barras de error completas (dos topes
    # alineados, unidos por una barra, con un rombo en el medio). El tratamiento sale del color del rombo.
    for fa, fb, col in emparejar(topes(tinta, f_top, f_bot, c0, c1), tinta):
        k = color_en(a, (fa + fb) / 2, col)
        if k < 0:
            continue
        filas.append({"temporada": temporada, "dia_x": round(float(ex.valor(col)), 2),
                      "tratamiento": TRATAMIENTOS[k], "vwc_pct": round(float(ey.valor((fa + fb) / 2)), 2),
                      "ee_pct": round(abs(float(ey.b)) * (fb - fa) / 2, 2), "fuente": "barra",
                      "ancho_rombo": ancho_marcador(tinta, (fa + fb) / 2, col), "_fila": (fa + fb) / 2,
                      "_err_barra": 0.0})
    df = deduplicar(pd.DataFrame(filas), 6 * abs(float(ex.b)))
    # Las mediciones son diarias: con barra de error (detecciones fiables) los marcadores caen a una
    # fracción fija del día (≈0.26, desfase de las etiquetas giradas respecto a la marca). Se corrige ese
    # desfase y se descartan detecciones lejos de un día entero (trozos gruesos de curva, no marcadores).
    desfase = float((df.loc[df.fuente == "barra", "dia_x"] % 1).median())
    df["dia_x"] = (df.dia_x - desfase).round(2)
    df["das"] = df.dia_x.round().astype(int)
    df["desvio_dia"] = (df.dia_x - df.das).round(2)
    df = df[df.desvio_dia.abs() <= 0.3].reset_index(drop=True)
    meta = {"temporada": temporada, "fila_10pct": fila10, "px_por_pct": abs(1 / ey.b), "px_por_dia": 1 / ex.b,
            "x_dia1": float(ex.pixel(1.0)), "residuo_y_max": float(np.abs(res_y).max()),
            "residuo_x_dias_max": float(np.abs(res_x).max()), "desfase_dias": desfase}
    return df, meta


def deduplicar(df: pd.DataFrame, tol_dias: float) -> pd.DataFrame:
    """Un tratamiento tiene a lo sumo una medición por día: detecciones del mismo color en la misma
    columna (±4 px) son el mismo marcador visto en el rombo y en sus topes. Se queda la detección con
    barra simétrica más centrada; si ninguna tiene barra, la de la fila mediana."""
    keep = []
    for _, g in df.sort_values("dia_x").groupby("tratamiento"):
        grupo: list[int] = []
        ult = None
        for i, x in zip(g.index, g.dia_x):
            if ult is not None and x - ult > tol_dias:
                keep.append(_elegir(df.loc[grupo]))
                grupo = []
            grupo.append(i)
            ult = x
        if grupo:
            keep.append(_elegir(df.loc[grupo]))
    out = df.loc[keep].drop(columns=["_fila", "_err_barra"])
    return out.sort_values(["tratamiento", "dia_x"]).reset_index(drop=True)


def _elegir(g: pd.DataFrame) -> int:
    con_barra = g[g.fuente == "barra"]
    if len(con_barra):
        return int(con_barra._err_barra.idxmin())
    return int(g.iloc[(g._fila - g._fila.median()).abs().argsort().iloc[0]].name)


def digitalizar() -> tuple[pd.DataFrame, pd.DataFrame]:
    a = cargar_rgb(FIGS / "fig3.png")
    dfs, metas = zip(*(digitalizar_panel(a, t) for t in (1, 2, 3)))
    return pd.concat(dfs, ignore_index=True), pd.DataFrame(metas)
