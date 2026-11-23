"""Figuras de la app (sección 5): recortes de las capturas de https://arroz-a-tiempo-sira.lovable.app.

Capturas (figs/app_*.png) tomadas con Edge sin ventana, 500 px CSS de ancho, escala 2.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image  # noqa: E402

F = Path(__file__).resolve().parent / "figs"
plt.rcParams.update({"font.family": "Arial", "font.size": 9, "savefig.dpi": 220, "savefig.bbox": "tight"})


def recorte(nombre, y0, y1, x0=0, x1=1000):
    return Image.open(F / nombre).convert("RGB").crop((x0, y0, x1, y1))


# ---------------------------------------------------------------- recorrido del agricultor
paneles = [("1. Parcela", recorte("app_parcela.png", 0, 1440)),
           ("2. Día y mapa", recorte("app_semaforo.png", 0, 2440)),
           ("3. Tarjetas", recorte("app_semaforo.png", 3560, 5000)),
           ("4. Cómo funciona", recorte("app_como_funciona.png", 0, 1240))]
fig, axs = plt.subplots(1, 4, figsize=(7.4, 3.9), gridspec_kw={"wspace": 0.08})
for ax, (t, im) in zip(axs, paneles):
    ax.imshow(im)
    ax.set_anchor("N")
    ax.set_title(t, fontsize=8.5, fontweight="bold", loc="left")
    ax.set_xticks([]), ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#B4B2A9"), s.set_linewidth(0.6)
fig.savefig(F / "fig_app_recorrido.png")
plt.close(fig)

# ---------------------------------------------------------------- detalle: barra de 31 días, celdas y leyenda
barra = recorte("app_semaforo.png", 205, 600)
celdas = recorte("app_semaforo.png", 1780, 2380, 170, 870)
leyenda = recorte("app_semaforo.png", 4600, 4960)
fig = plt.figure(figsize=(7.2, 3.3))
a1 = fig.add_axes([0.0, 0.52, 0.5, 0.46])
a2 = fig.add_axes([0.52, 0.0, 0.48, 0.98])
a3 = fig.add_axes([0.0, 0.0, 0.5, 0.44])
for ax, im, t in ((a1, barra, "a) Día elegido y barra de 31 días"), (a2, celdas, "b) Celdas de 10 m sobre la parcela"),
                  (a3, leyenda, "c) Significado de cada color")):
    ax.imshow(im)
    ax.set_title(t, fontsize=8.5, fontweight="bold", loc="left")
    ax.set_xticks([]), ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#B4B2A9"), s.set_linewidth(0.6)
fig.savefig(F / "fig_app_detalle.png")
plt.close(fig)
print("listo")

# ---------------------------------------------------------------- otros días en una parcela grande (24,6 ha, varios lotes)
dias = [("Hoy, 19 nov", "app_dia00.png"), ("+16 días, 5 dic", "app_dia16.png"), ("+18 días, 7 dic", "app_dia18.png"),
        ("+20 días, 9 dic", "app_dia20.png")]
fig, axs = plt.subplots(1, 4, figsize=(7.4, 2.9), gridspec_kw={"wspace": 0.05})
for ax, (t, f) in zip(axs, dias):
    ax.imshow(Image.open(F / f).convert("RGB").crop((0, 75, 430, 478)))
    ax.set_title(t, fontsize=8.5, fontweight="bold")
    ax.set_xticks([]), ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#B4B2A9"), s.set_linewidth(0.6)
fig.savefig(F / "fig_app_dias.png")
plt.close(fig)
print("dias listo")
