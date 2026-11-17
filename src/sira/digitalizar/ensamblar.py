"""Une las tres figuras digitalizadas en la tabla diaria de Nataima y escribe el reporte de validación.

Salidas:
- data/interim/fig2_lluvia_atwd.csv, fig4_riego.csv, fig3_vwc_detecciones.csv (todo lo detectado)
- data/processed/nataima_diario.csv (formato largo: temporada × día × tratamiento)
- reports/validacion_digitalizacion.md

Uso:  python -m sira.digitalizar.ensamblar   (desde src/, o con src en PYTHONPATH)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import fig2, fig3, fig4
from .comun import RAIZ

TRATAMIENTOS = ("CD", "MIRI", "AWD+MIRI")

# Valores publicados en el artículo (Martínez-Vega et al. 2025) usados SOLO para validar.
LLUVIA_TABLA2 = {1: 521.4, 2: 548.7, 3: 472.2}
LLUVIA_MAX_TEXTO = {1: 113.0, 2: 87.0, 3: 67.0}
RIEGO_TABLA2 = {1: (1010.6, 517.9, 367.3), 2: (404.7, 316.8, 210.5), 3: (820.0, 376.0, 291.5)}
EVENTOS_TABLA2 = {1: (23, 23, 16), 2: (14, 12, 9), 3: (24, 21, 13)}
ETAPAS_FIG4 = {  # (veg, rep, mad) por serie: CD, MIRI, AWD+MIRI, lluvia
    1: ((205.9, 428.4, 376.3), (137.8, 215.5, 164.6), (112.4, 113.9, 140.9), (357.6, 59.4, 104.4)),
    2: ((109.9, 172.8, 121.9), (71.9, 178.7, 66.2), (72.5, 100.6, 37.4), (182.0, 88.2, 278.2)),
    3: ((262.8, 283.7, 273.4), (103.3, 149.3, 123.4), (92.8, 83.8, 114.8), (184.8, 120.1, 166.5)),
}
VWC_TEXTO = {  # (mín, máx) citados en el texto; None = no citado
    (1, "CD"): (32, 44), (2, "CD"): (31, 38.7), (3, "CD"): (31, 41),
    (1, "MIRI"): (28, 39), (2, "MIRI"): (28, 35), (3, "MIRI"): (28, 33),
    (1, "AWD+MIRI"): (24, None), (2, "AWD+MIRI"): (26, None), (3, "AWD+MIRI"): (25, None),
}


def cortes_etapa(riego: pd.DataFrame) -> dict[int, tuple[int, int, float]]:
    """Último día de etapa vegetativa y reproductiva que mejor reproduce las 12 sumas por etapa impresas
    en la Fig. 4 de cada temporada (3 tratamientos + lluvia). Devuelve (fin_veg, fin_rep, error_medio)."""
    cols = [f"riego_acum_{s}" for s in TRATAMIENTOS] + ["lluvia_acum_fig4"]
    out = {}
    for t, g in riego.groupby("temporada"):
        g = g.set_index("das")
        mejor = None
        for b1 in range(35, 56):
            for b2 in range(b1 + 25, int(g.index.max()) - 5):
                err = []
                for k, c in enumerate(cols):
                    v = g[c]
                    s = (v.loc[b1], v.loc[b2] - v.loc[b1], v.iloc[-1] - v.loc[b2])
                    err += [abs(s[i] - ETAPAS_FIG4[t][k][i]) for i in range(3)]
                e = float(np.mean(err))
                # desempate leve hacia el sombreado de la figura (fin veg ≈41, fin rep ≈81): varios cortes
                # dan la misma suma cuando no hubo riego ni lluvia en esos días
                pen = e + 0.05 * (abs(b1 - 41) + abs(b2 - 81))
                if mejor is None or pen < mejor[3]:
                    mejor = (b1, b2, e, pen)
        mejor = mejor[:3]
        out[int(t)] = mejor
    return out


def ensamblar() -> tuple[pd.DataFrame, dict]:
    lluvia, meta2 = fig2.digitalizar()
    riego, meta4 = fig4.digitalizar()
    vwc_todas, meta3 = fig3.digitalizar()
    interim = RAIZ / "data" / "interim"
    lluvia.to_csv(interim / "fig2_lluvia_atwd.csv", index=False)
    riego.to_csv(interim / "fig4_riego.csv", index=False)
    vwc_todas.to_csv(interim / "fig3_vwc_detecciones.csv", index=False)

    cortes = cortes_etapa(riego)
    vwc = vwc_todas[vwc_todas.fuente == "barra"]  # solo mediciones confirmadas por su barra de error
    filas = []
    desfases_atwd: dict[int, int] = {}
    for t, gl in lluvia.groupby("temporada"):
        gl = gl.set_index("das").sort_index()
        # ETo media de 5 días (día actual y 4 anteriores) a partir de la definición de AtWD del artículo:
        # AtWD = media5(ETo) − media5(lluvia)  ⇒  media5(ETo) = AtWD + media5(lluvia)
        # En la figura la línea de AtWD puede estar corrida un día respecto a las barras (en GS1 lo está):
        # se elige el desfase (−1, 0, +1) que deja la ETo más suave, porque la ETo real casi no salta.
        desf = min((-1, 0, 1), key=lambda k: float(
            (gl.atwd_mm + gl.lluvia_mm.shift(k).rolling(5, min_periods=5).mean()).std()))
        eto5 = gl.atwd_mm + gl.lluvia_mm.shift(desf).rolling(5, min_periods=5).mean()
        desfases_atwd[int(t)] = desf
        gr = riego[riego.temporada == t].set_index("das")
        b1, b2, _ = cortes[int(t)]
        for d in gl.index:
            etapa = "vegetativa" if d <= b1 else ("reproductiva" if d <= b2 else "maduracion")
            for s in TRATAMIENTOS:
                m = vwc[(vwc.temporada == t) & (vwc.tratamiento == s) & (vwc.das == d)]
                filas.append({
                    "temporada": int(t), "das": int(d), "tratamiento": s, "etapa": etapa,
                    "lluvia_mm": gl.at[d, "lluvia_mm"], "atwd_mm": gl.at[d, "atwd_mm"],
                    "eto5_mm": round(float(eto5.get(d, np.nan)), 2),
                    "riego_mm": gr.at[d, f"riego_mm_{s}"] if d in gr.index else np.nan,
                    "vwc_pct": float(m.vwc_pct.iloc[0]) if len(m) else np.nan,
                    "vwc_ee_pct": float(m.ee_pct.iloc[0]) if len(m) else np.nan,
                })
    diario = pd.DataFrame(filas)
    diario.to_csv(RAIZ / "data" / "processed" / "nataima_diario.csv", index=False)
    contexto = {"meta2": meta2, "meta3": meta3, "meta4": meta4, "cortes": cortes, "riego": riego,
                "lluvia": lluvia, "vwc_todas": vwc_todas, "vwc": vwc, "diario": diario,
                "desfases_atwd": desfases_atwd}
    return diario, contexto


def reporte(ctx: dict) -> str:
    L = ["# Validación de la digitalización (Martínez-Vega et al. 2025, Figs. 2–4)", ""]
    lluvia, riego, vwc, todas = ctx["lluvia"], ctx["riego"], ctx["vwc"], ctx["vwc_todas"]

    L += ["## Fig. 2: lluvia diaria", "", "| Temporada | Total digitalizado | Tabla 2 | Dif. | Máx. diario | Texto |",
          "|---|---|---|---|---|---|"]
    for t, g in lluvia.groupby("temporada"):
        tot, ref = g.lluvia_mm.sum(), LLUVIA_TABLA2[t]
        L.append(f"| GS{t} | {tot:.1f} mm | {ref} mm | {100 * (tot - ref) / ref:+.1f} % | "
                 f"{g.lluvia_mm.max():.1f} mm | {LLUVIA_MAX_TEXTO[t]:.0f} mm |")
    L += ["", "GS2: la barra más alta de la figura mide ≈77 mm; el texto del artículo dice 87 mm. "
          "La figura y el total de la Tabla 2 son coherentes con 77 mm.", ""]

    L += ["## Fig. 4: riego por tratamiento", "",
          "El final de cada línea se ancla al total de la Tabla 2 (única referencia usada al digitalizar). "
          "Las sumas por etapa impresas en la figura y el número de riegos se usan solo para validar.", ""]
    esc = ctx["meta4"]
    L += ["Factor de escala aplicado a cada serie para que su total sea el de la Tabla 2 (corrige un sesgo de "
          "1–2 px entre el centro de la línea y el valor): "
          + "; ".join(f"GS{int(r['temporada'])} " + ", ".join(f"{s} {r[f'escala_{s}']:.3f}" for s in TRATAMIENTOS)
                      for _, r in esc.iterrows()) + ".", ""]
    L += ["| Temporada | Cortes de etapa (fin veg / fin rep) | Error medio de las 12 sumas por etapa |", "|---|---|---|"]
    for t, (b1, b2, e) in ctx["cortes"].items():
        L.append(f"| GS{t} | día {b1} / día {b2} | {e:.1f} mm |")
    L += ["", "El sombreado de la figura marca la etapa reproductiva entre los días ≈42 y ≈82; las sumas impresas "
          "solo cuadran con los cortes de la tabla (≈2 mm de error medio), así que esos son los que usó el artículo.", ""]
    L += ["| Temporada | Serie | Veg (dig./fig.) | Rep (dig./fig.) | Mad (dig./fig.) | Riegos (dig./Tabla 2) |",
          "|---|---|---|---|---|---|"]
    for t, g in riego.groupby("temporada"):
        g = g.set_index("das")
        b1, b2, _ = ctx["cortes"][t]
        for k, s in enumerate(list(TRATAMIENTOS) + ["lluvia"]):
            v = g[f"riego_acum_{s}" if k < 3 else "lluvia_acum_fig4"]
            sums = (v.loc[b1], v.loc[b2] - v.loc[b1], v.iloc[-1] - v.loc[b2])
            ref = ETAPAS_FIG4[t][k]
            nev = f"{int((g[f'riego_mm_{s}'] >= 5).sum())} / {EVENTOS_TABLA2[t][k]}" if k < 3 else "—"
            L.append(f"| GS{t} | {s} | {sums[0]:.1f} / {ref[0]} | {sums[1]:.1f} / {ref[1]} | "
                     f"{sums[2]:.1f} / {ref[2]} | {nev} |")
    L.append("")

    L += ["## Fig. 3: humedad del suelo (TDR, 20 cm)", "",
          "Solo se guardan como medición los marcadores confirmados por su barra de error simétrica. "
          "Las detecciones de \"solo rombo\" suelen caer sobre la curva suavizada y se descartan.", "",
          "| Temporada | Tratamiento | Mediciones | Descartadas | Mín. (dig./texto) | Máx. (dig./texto) | Días |",
          "|---|---|---|---|---|---|---|"]
    for (t, s), g in vwc.groupby(["temporada", "tratamiento"]):
        desc = len(todas[(todas.temporada == t) & (todas.tratamiento == s) & (todas.fuente != "barra")])
        ref = VWC_TEXTO[(t, s)]
        L.append(f"| GS{t} | {s} | {len(g)} | {desc} | {g.vwc_pct.min():.1f} / {ref[0]} | "
                 f"{g.vwc_pct.max():.1f} / {ref[1] if ref[1] else '—'} | {g.das.min()}–{g.das.max()} |")
    m3 = ctx["meta3"]
    L += ["", "Desfase del eje x corregido con las propias mediciones (deben caer en días enteros): "
          + ", ".join(f"GS{int(r.temporada)} {r.desfase_dias:.2f} d" for r in m3.itertuples())
          + ". Incertidumbre del día de medición: ±1 día.", ""]

    d = ctx["diario"]
    e5 = d.drop_duplicates(["temporada", "das"]).eto5_mm.dropna()
    des = ", ".join(f"GS{t} {k:+d} d" for t, k in ctx["desfases_atwd"].items())
    L += ["## ETo derivada del AtWD", "",
          f"media5(ETo) = AtWD + media5(lluvia): mediana {e5.median():.2f} mm/d, percentiles 5–95 "
          f"{e5.quantile(.05):.2f}–{e5.quantile(.95):.2f} mm/d (plausible para Espinal: ≈3–6 mm/d). "
          f"Desfase de la línea de AtWD respecto a las barras de lluvia, elegido por suavidad: {des}. "
          "Cerca de lluvias grandes la resta amplifica cualquier error de lectura; esta columna es orientativa "
          "y se contrastará con una ETo FAO-56 calculada con NASA POWER.", ""]
    return "\n".join(L)


def main() -> None:
    diario, ctx = ensamblar()
    (RAIZ / "reports" / "validacion_digitalizacion.md").write_text(reporte(ctx), encoding="utf-8")
    n_med = diario.vwc_pct.notna().sum()
    print(f"nataima_diario.csv: {len(diario)} filas, {n_med} con humedad medida")


if __name__ == "__main__":
    main()
