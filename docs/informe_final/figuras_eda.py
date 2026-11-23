"""Figuras y cifras del análisis exploratorio (sección 3.2 del informe final). Salida: docs/informe_final/figs/.

Datos: data/processed/nataima_diario.csv (digitalizado de Martínez-Vega et al. 2025), reports/humedad_unificada/
tabla_entrenamiento_ndmi.csv (198 mediciones TDR con clima, NDVI, NDMI y balance) y
data/processed/modelo_b/pixeles_lote_s2_landsat.csv (índices Sentinel-2 por franja, para el NDWI).
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "figs"
OUT.mkdir(exist_ok=True)
plt.rcParams.update({"font.family": "Arial", "font.size": 9.5, "axes.spines.top": False, "axes.spines.right": False,
                     "savefig.dpi": 220, "savefig.bbox": "tight"})
COL = {"CD": "#E04B4B", "MIRI": "#E0A800", "AWD+MIRI": "#2E9E5B"}
OSC, AZUL, GRIS = "#2C2C2A", "#2F7FC1", "#B4B2A9"
coma = matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:g}".replace(".", ","))
SIEMBRAS = {1: "2021-05-27", 2: "2021-12-04", 3: "2022-07-09"}
ROT = {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}  # oeste/centro/este
FRAC = {"oeste": "frac_MIRI", "centro": "frac_AWD+MIRI", "este": "frac_CD"}
MALAS = {"2021-07-07", "2021-08-11", "2021-09-15", "2022-01-03"}
GS_TXT = {1: "GS1 · may–sep 2021", 2: "GS2 · dic 2021–abr 2022", 3: "GS3 · jul–nov 2022"}

D = pd.read_csv(RAIZ / "data/processed/nataima_diario.csv")
M = pd.read_csv(RAIZ / "reports/humedad_unificada/tabla_entrenamiento_ndmi.csv")

# NDWI (McFeeters) por franja: misma QC que el NDVI del entrenamiento, interpolado por día desde siembra
px = pd.read_csv(RAIZ / "data/processed/modelo_b/pixeles_lote_s2_landsat.csv", parse_dates=["fecha"])
px = px[(px.sensor == "S2") & (px[list(FRAC.values())].max(axis=1) >= 0.9) & ~px.fecha.dt.strftime("%Y-%m-%d").isin(MALAS)].copy()
px["fis"] = px[list(FRAC.values())].idxmax(axis=1).map({v: k for k, v in FRAC.items()})
px = px.groupby(["fecha", "fis"], as_index=False)[["ndvi", "ndmi", "ndwi"]].mean()
ndwi = []
for (t, trat), g in M.groupby(["temporada", "tratamiento"]):
    s = px[px.fis == dict(zip(ROT[t], ("oeste", "centro", "este")))[trat]].copy()
    s["das"] = (s.fecha - pd.Timestamp(SIEMBRAS[t])).dt.days + 1
    s = s[s.das.between(-15, 140)].sort_values("das")
    ndwi.append(pd.Series(np.interp(g.das, s.das, s.ndwi), index=g.index))
M["ndwi"] = pd.concat(ndwi)


def fig_series_tdr():
    fig, axs = plt.subplots(3, 1, figsize=(7.2, 6.6), sharex=True)
    for ax, t in zip(axs, (1, 2, 3)):
        g = D[D.temporada == t]
        ll = g.groupby("das").lluvia_mm.first()
        a2 = ax.twinx()
        a2.bar(ll.index, ll.values, color=AZUL, alpha=0.35, width=1.0, label="Lluvia")
        a2.set_ylim(0, 260), a2.invert_yaxis(), a2.set_ylabel("Lluvia (mm/día)", color=AZUL, fontsize=8.5)
        a2.spines["right"].set_visible(True), a2.tick_params(axis="y", colors=AZUL, labelsize=8)
        ax.axhspan(30, 46, color="#2E9E5B", alpha=0.06, lw=0)
        ax.axhspan(27.1, 30, color="#F2C230", alpha=0.12, lw=0)
        ax.axhspan(20, 27.1, color="#E04B4B", alpha=0.08, lw=0)
        for trat in ("CD", "MIRI", "AWD+MIRI"):
            s = g[(g.tratamiento == trat) & g.vwc_pct.notna()]
            ax.plot(s.das, s.vwc_pct, "-o", ms=2.6, lw=1.1, color=COL[trat], label=trat)
        ax.set_ylim(22, 45), ax.set_ylabel("Humedad TDR (%)")
        ax.text(0.01, 0.95, GS_TXT[t], transform=ax.transAxes, fontsize=9, fontweight="bold", va="top")
        ax.set_zorder(a2.get_zorder() + 1), ax.patch.set_visible(False)
    axs[0].legend(frameon=False, ncol=3, loc="upper right", fontsize=8.5, bbox_to_anchor=(1.0, 1.18))
    axs[-1].set_xlabel("Días desde la siembra")
    fig.align_ylabels(axs)
    fig.savefig(OUT / "fig_eda_series_tdr.png")
    plt.close(fig)


def fig_correlaciones():
    grupos = [("Agua que entra", [("riego_5d", "Riego 5 días"), ("lluvia_5d", "Lluvia 5 días (CHIRPS)")]),
              ("Clima (NASA POWER)", [("eto_5d", "ETo 5 días"), ("T2M_MAX", "Temperatura máxima"), ("RH2M", "Humedad del aire")]),
              ("Satélite (Sentinel-2)", [("ndvi", "NDVI"), ("ndmi", "NDMI"), ("ndwi", "NDWI")]),
              ("Balance hídrico", [("S_tau20", "Reservorio S (τ = 20 d)"), ("Dr_k0.5", "Déficit Dr FAO-56")])]
    filas = []
    for gr, vs in grupos:
        for c, nom in vs:
            filas.append((gr, nom, spearmanr(M[c], M.vwc_pct, nan_policy="omit")[0]))
    fig, ax = plt.subplots(figsize=(6.6, 3.6))
    y = np.arange(len(filas))[::-1]
    colg = {"Agua que entra": AZUL, "Clima (NASA POWER)": GRIS, "Satélite (Sentinel-2)": "#7F77DD", "Balance hídrico": "#1D9E75"}
    for yi, (gr, nom, r) in zip(y, filas):
        ax.barh(yi, r, color=colg[gr], height=0.65)
        ax.text(r + (0.012 if r >= 0 else -0.012), yi, f"{r:+.2f}".replace(".", ",").replace("-", "−"), va="center",
                ha="left" if r >= 0 else "right", fontsize=8.5)
    ax.set_yticks(y, [n for _, n, _ in filas])
    ax.axvline(0, color=OSC, lw=0.7)
    ax.set_xlim(-0.3, 0.68), ax.xaxis.set_major_formatter(coma)
    ax.set_xlabel("Correlación de Spearman con la humedad TDR (n = 198)")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=v, label=k) for k, v in colg.items()], frameon=False, fontsize=8, loc="center left", bbox_to_anchor=(1.0, 0.5))
    fig.savefig(OUT / "fig_eda_correlaciones.png")
    plt.close(fig)
    return {n: round(float(r), 2) for _, n, r in filas}


def fig_ndvi_kc():
    fig, axs = plt.subplots(1, 3, figsize=(7.2, 2.6), sharey=True)
    for ax, t in zip(axs, (1, 2, 3)):
        g = M[M.temporada == t]
        for trat in ("CD", "MIRI", "AWD+MIRI"):
            s = g[g.tratamiento == trat].sort_values("das")
            ax.plot(s.das, s.ndvi, "-", lw=1.3, color=COL[trat], label=trat)
        ax.set_title(GS_TXT[t].split(" · ")[0], fontsize=9.5)
        ax.set_xlabel("Días desde siembra")
        ax.yaxis.set_major_formatter(coma)
    axs[0].set_ylabel("NDVI (Sentinel-2)")
    a2 = axs[-1].twinx()
    lo, hi = axs[-1].get_ylim()
    a2.set_ylim(lo + 0.45, hi + 0.45), a2.set_ylabel("Kc = NDVI + 0,45"), a2.yaxis.set_major_formatter(coma)
    a2.spines["right"].set_visible(True)
    axs[0].legend(frameon=False, fontsize=7.5, loc="lower center")
    fig.tight_layout(w_pad=0.8)
    fig.savefig(OUT / "fig_eda_ndvi_kc.png")
    plt.close(fig)


def fig_propagador():
    fig, ax = plt.subplots(figsize=(7.2, 2.9))
    ax.set_xlim(0, 100), ax.set_ylim(-8, 42), ax.axis("off")

    def caja(x, y, w, h, titulo, sub, fc, ec, tam_sub=7.8):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.5", fc=fc, ec=ec, lw=0.8))
        ax.text(x + w / 2, y + h * 0.66, titulo, ha="center", va="center", fontsize=8.8, fontweight="bold", color=OSC)
        ax.text(x + w / 2, y + h * 0.28, sub, ha="center", va="center", fontsize=tam_sub, color="#5F5E5A")

    def flecha(x1, y1, x2, y2, col=OSC, rad=0.0):
        ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=10, color=col, lw=1.0,
                                     connectionstyle=f"arc3,rad={rad}"))

    caja(1, 13, 16, 10, "Dr día i − 1", "estado de ayer", "#E1F5EE", "#0F6E56")
    caja(33, 13, 34, 10, "Ecuación FAO-56", "Dr,i = Dr,i−1 − P − I + Ks·Kc·ETo", "#E1F5EE", "#0F6E56", 8.2)
    caja(83, 13, 16, 10, "Dr día i", "estado de hoy", "#E1F5EE", "#0F6E56")
    tops = (("Lluvia P", "CHIRPS · GEOS-S2S", "#E6F1FB", "#185FA5"), ("Riego I", "dato o escenario", "#E6F1FB", "#185FA5"),
            ("ETo", "POWER · GEOS-S2S", "#F1EFE8", "#888780"), ("Kc del satélite", "Kc = NDVI + 0,45", "#EEEDFE", "#534AB7"))
    for k, (t_, s_, fc, ec) in enumerate(tops):
        x = 6 + k * 22.5
        caja(x, 31, 19.5, 8.5, t_, s_, fc, ec)
        flecha(x + 9.75, 30.6, 40 + k * 6.5, 23.6)
    flecha(17.8, 18, 32.4, 18), flecha(67.6, 18, 82.4, 18)
    flecha(91, 12.4, 9, 12.4, col="#0F6E56", rad=-0.18)
    ax.text(50, -6.5, "se repite cada día: el Dr de hoy es el punto de partida de mañana", ha="center", fontsize=8,
            color="#0F6E56", style="italic")
    fig.savefig(OUT / "fig_eda_propagador.png")
    plt.close(fig)


if __name__ == "__main__":
    fig_series_tdr()
    r = fig_correlaciones()
    fig_ndvi_kc()
    fig_propagador()
    json.dump(r, open(OUT / "eda_correlaciones.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(r)
    print("NDWI medio por tratamiento:", M.groupby("tratamiento").ndwi.mean().round(3).to_dict())
