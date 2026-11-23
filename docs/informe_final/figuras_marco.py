"""Figuras del marco teórico del informe final (salida: docs/informe_final/figs/).

Datos tomados de las fuentes citadas en el informe:
  Martínez-Vega et al. 2025 (riego por tratamiento, suelo, déficit crítico) · Allen et al. 1998, FAO-56 (Ks)
  Agronegocios / La República 2026 (balance del arroz en Colombia) · Nederhoff y Stanghellini 2010 (tomate)
"""
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

OUT = Path(__file__).resolve().parent / "figs"
OUT.mkdir(exist_ok=True)
plt.rcParams.update({"font.family": "Arial", "font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.titlesize": 11, "savefig.dpi": 220, "savefig.bbox": "tight"})
VERDE, AMAR, ROJO, AZUL, GRIS, OSC = "#2E9E5B", "#F2C230", "#E04B4B", "#2F7FC1", "#B4B2A9", "#2C2C2A"


def gota(ax, x, y, s, color):
    """Gota estilizada centrada en (x, y) de alto s."""
    from matplotlib.path import Path as MPath
    from matplotlib.patches import PathPatch
    v = [(0, 0.5), (0.32, 0.02), (0.32, -0.18), (0, -0.5), (-0.32, -0.18), (-0.32, 0.02), (0, 0.5)]
    c = [MPath.MOVETO, MPath.CURVE3, MPath.CURVE3, MPath.CURVE3, MPath.CURVE3, MPath.CURVE3, MPath.CURVE3]
    # contorno: punta arriba, panza abajo
    v = [(0, 0.5), (0.36, -0.05), (0.3, -0.38), (0, -0.5), (-0.3, -0.38), (-0.36, -0.05), (0, 0.5)]
    p = MPath([(x + a * s, y + b * s) for a, b in v], [MPath.MOVETO] + [MPath.LINETO] * 6)
    ax.add_patch(PathPatch(p, fc=color, ec="none", joinstyle="round"))


# ---------------------------------------------------------------- Fig. arroz en Colombia
def fig_arroz_colombia():
    prod, contra, imp = 1.86, 0.2622, 0.0878  # Mt de arroz blanco, 2025
    tot = prod + contra + imp
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 2.6), gridspec_kw={"width_ratios": [2.3, 1]})
    izq = 0
    for v, nom, col in ((prod, "Producción nacional", VERDE), (contra, "Contrabando", ROJO), (imp, "Importación legal", AZUL)):
        a1.barh(0, v, left=izq, color=col, height=0.5)
        pct = 100 * v / tot
        if v > 0.5:
            a1.text(izq + v / 2, 0, f"{nom}\n{v:.2f} Mt ({pct:.0f} %)", ha="center", va="center", color="white", fontsize=9)
        izq += v
    a1.annotate(f"Contrabando\n{contra * 1000:.0f} mil t ({100 * contra / tot:.0f} %)", xy=(prod + contra / 2, 0.25),
                xytext=(1.55, 0.75), fontsize=8.5, ha="center", arrowprops=dict(arrowstyle="-", color=OSC, lw=0.6))
    a1.annotate(f"Importación\n{imp * 1000:.0f} mil t ({100 * imp / tot:.0f} %)", xy=(prod + contra + imp / 2, 0.25),
                xytext=(2.12, 0.75), fontsize=8.5, ha="center", arrowprops=dict(arrowstyle="-", color=OSC, lw=0.6))
    a1.set_xlim(0, 2.3), a1.set_ylim(-0.4, 1.1), a1.set_yticks([])
    a1.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.1f}".replace(".", ",")))
    a1.set_xlabel("Consumo de arroz blanco en Colombia, 2025 (millones de toneladas)")
    a1.spines["left"].set_visible(False)
    a2.pie([85.9, 14.1], colors=[AZUL, GRIS], startangle=90, counterclock=False,
           wedgeprops=dict(width=0.38, edgecolor="white"))
    a2.text(0, 0, "85,9 %", ha="center", va="center", fontsize=12, fontweight="bold", color=AZUL)
    a2.set_title("Origen de las importaciones:\nEstados Unidos", fontsize=9.5)
    fig.savefig(OUT / "fig_arroz_colombia.png")
    plt.close(fig)


# ---------------------------------------------------------------- Fig. Países Bajos vs Colombia
def fig_paises_bajos():
    a_co, a_nl = 1_141_748, 41_543
    fig = plt.figure(figsize=(7.2, 3.3))
    a1 = fig.add_axes([0.0, 0.08, 0.36, 0.84])
    # contornos Natural Earth 1:110m, cada país proyectado en km alrededor de su propia latitud media
    # (x = lon·cos(lat0)·111,32; y = lat·110,57): así las dos siluetas quedan a la misma escala de área
    import json
    import math
    from matplotlib.patches import Polygon
    g = json.load(open(Path(__file__).resolve().parent / "datos/colombia_paises_bajos_ne110m.geojson", encoding="utf-8"))
    anillos = {}
    for f in g["features"]:
        geom = f["geometry"]
        polis = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        ext = [p[0] for p in polis]
        lat0 = sum(y for r in ext for _, y in r) / sum(len(r) for r in ext)
        k = math.cos(math.radians(lat0)) * 111.32
        anillos[f["properties"]["iso"]] = [[(x * k, y * 110.57) for x, y in r] for r in ext]

    def colocar(iso, x0, y0):  # esquina inferior izquierda de la silueta en (x0, y0) km
        rs = anillos[iso]
        mx, my = min(x for r in rs for x, _ in r), min(y for r in rs for _, y in r)
        return [[(x - mx + x0, y - my + y0) for x, y in r] for r in rs]

    co, nl = colocar("COL", 0, 0), colocar("NLD", 1380, 80)
    for r in co:
        a1.add_patch(Polygon(r, fc="#FCE7B5", ec="#B88A12", lw=0.8))
    for r in nl:
        a1.add_patch(Polygon(r, fc="#F28C28", ec="#A35400", lw=0.8))
    a1.text(620, 900, "Colombia\n1 141 748 km²", ha="center", va="center", fontsize=9.5)
    xs_nl = [x for r in nl for x, _ in r]
    a1.text((min(xs_nl) + max(xs_nl)) / 2, max(y for r in nl for _, y in r) + 30, "Países Bajos\n41 543 km²",
            ha="center", va="bottom", fontsize=8.5)
    a1.plot([0, 500], [-90, -90], color=OSC, lw=1.2)
    a1.text(250, -120, "500 km", ha="center", va="top", fontsize=8)
    a1.set_xlim(-30, 1780), a1.set_ylim(-220, 1950), a1.set_aspect("equal"), a1.axis("off")
    a1.set_title(f"Área a escala: Colombia es {a_co / a_nl:.1f} veces mayor".replace(".", ","), fontsize=9.5)
    a2 = fig.add_axes([0.42, 0.08, 0.58, 0.84])
    filas = (("Campo abierto (general)", 200, "100–300 L/kg"), ("Campo abierto con goteo", 60, "60 L/kg"),
             ("Invernadero neerlandés\ncon hidroponía cerrada", 15, "15 L/kg"))
    for i, (nom, litros, etq) in enumerate(filas):
        y = 2 - i
        n = round(litros / 5)
        col = GRIS if i < 2 else AZUL
        for k in range(n):
            gota(a2, 4.3 + (k % 20) * 0.33, y + 0.15 - (k // 20) * 0.38, 0.32, col)
        a2.text(0, y, nom, va="center", fontsize=8.8)
        a2.text(4.3 + 20 * 0.33 + 0.15, y, etq, va="center", fontsize=8.8, color=OSC, fontweight="bold" if i == 2 else None)
    a2.set_xlim(0, 12.6), a2.set_ylim(-0.6, 2.6), a2.axis("off")
    a2.set_title("Agua para producir 1 kg de tomate (1 gota = 5 L)", fontsize=9.5)
    fig.savefig(OUT / "fig_paises_bajos_tomate.png")
    plt.close(fig)


# ---------------------------------------------------------------- Fig. riego por tratamiento en Nataima
def fig_riego_nataima():
    riego = {"CD": (1010.6, 405.0, 820.0), "MIRI": (517.9, 317.0, 376.0), "AWD+MIRI": (367.3, 210.0, 291.0)}
    reduc = {"MIRI": (49, 22, 54), "AWD+MIRI": (64, 48, 65)}
    cols = {"CD": ROJO, "MIRI": AMAR, "AWD+MIRI": VERDE}
    fig, ax = plt.subplots(figsize=(6.4, 2.9))
    w = 0.26
    for j, (t, v) in enumerate(riego.items()):
        xs = [i + (j - 1) * w for i in range(3)]
        b = ax.bar(xs, v, w, color=cols[t], label=t)
        for i, (x, val) in enumerate(zip(xs, v)):
            txt = f"{val:.0f}" + (f"\n−{reduc[t][i]} %" if t in reduc else "")
            ax.text(x, val + 15, txt, ha="center", va="bottom", fontsize=7.5)
    ax.set_xticks(range(3), ["GS1 (may–sep 2021)", "GS2 (dic 2021–abr 2022)", "GS3 (jul–nov 2022)"])
    ax.set_ylabel("Lámina de riego (mm)"), ax.set_ylim(0, 1180)
    ax.legend(frameon=False, ncol=3, loc="upper right", fontsize=8.5)
    fig.savefig(OUT / "fig_riego_nataima.png")
    plt.close(fig)


# ---------------------------------------------------------------- Fig. umbrales de humedad y semáforo
def fig_umbrales():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.2, 3.0), gridspec_kw={"width_ratios": [1, 1.35]})
    # escala de humedad volumétrica con las tres franjas
    for lo, hi, col, txt in ((30, 34, VERDE, "Suficiente: ≥ 30 %\n(capacidad de campo)"),
                             (27.1, 30, AMAR, "Media: 27,1–30 %\nregar en un intervalo"),
                             (25, 27.1, ROJO, "Baja: < 27,1 %\nregar ya")):
        a1.add_patch(Rectangle((0, lo), 1, hi - lo, fc=col, ec="white", alpha=0.9))
        a1.text(1.15, (lo + hi) / 2, txt, va="center", fontsize=8.5)
    for v, t in ((30, "CC 30 %"), (27.1, "27,1 %"), (26, "PMP ≈ 26 %")):
        a1.plot([-0.12, 0], [v, v], color=OSC, lw=0.8)
        a1.text(-0.18, v, t, ha="right", va="center", fontsize=8)
    a1.set_xlim(-1.3, 3.6), a1.set_ylim(25, 34), a1.axis("off")
    a1.set_title("Humedad volumétrica del suelo (TDR)", fontsize=9.5)
    # coeficiente de estrés Ks de FAO-56 contra el déficit
    TAW, P = 6.69, 0.65
    RAW = P * TAW
    a2.plot([0, RAW, TAW], [1, 1, 0], color=OSC, lw=1.6)
    a2.axvspan(-1, 0, color=VERDE, alpha=0.25, lw=0)
    a2.axvspan(0, RAW, color=AMAR, alpha=0.25, lw=0)
    a2.axvspan(RAW, TAW, color=ROJO, alpha=0.25, lw=0)
    a2.axvline(RAW, color=OSC, lw=0.6, ls="--")
    a2.text(RAW - 0.1, 0.55, "RAW = p·TAW\n= 4,34 mm", fontsize=8, ha="right")
    a2.text(TAW, 1.04, "TAW = 6,7 mm", fontsize=8, ha="center")
    a2.set_xlim(-1, 7.2), a2.set_ylim(0, 1.12)
    a2.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:.1f}".replace(".", ",")))
    a2.set_xlabel("Déficit de agua en la raíz, Dr (mm)"), a2.set_ylabel("Coeficiente de estrés hídrico, Ks")
    a2.set_title("Estrés hídrico según FAO-56", fontsize=9.5)
    fig.tight_layout(w_pad=1.5)
    fig.savefig(OUT / "fig_umbrales_semaforo.png")
    plt.close(fig)


if __name__ == "__main__":
    fig_arroz_colombia(), fig_paises_bajos(), fig_riego_nataima(), fig_umbrales()
    print("figuras en", OUT)
