"""Figuras de la sección 3.4 (resultados del semáforo). Salida: docs/informe_final/figs/.

Fuente: reports/semaforo_c/ (corrida oficial: resumen.json, diario.npz y semaforo_hXX.png).
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
SEM = RAIZ / "reports/semaforo_c"
OUT = Path(__file__).resolve().parent / "figs"
plt.rcParams.update({"font.family": "Arial", "font.size": 9.5, "axes.spines.top": False, "axes.spines.right": False,
                     "savefig.dpi": 220, "savefig.bbox": "tight"})
VERDE, AMAR, ROJO, AZUL, OSC = "#2E9E5B", "#F2C230", "#E04B4B", "#2F7FC1", "#2C2C2A"
coma = matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:g}".replace(".", ","))

R = json.load(open(SEM / "resumen.json", encoding="utf-8"))
Z = np.load(SEM / "diario.npz")
CL = Z["clase"]  # (pixeles, 31): -1 fuera de cultivo, 0 suficiente, 1 media, 2 baja
DEF = Z["deficit_mm"]
H = CL.shape[1]
en = CL[:, 0] >= 0
pct = {k: [100 * np.mean(CL[en & (CL[:, h] >= 0), h] == k) for h in range(H)] for k in (0, 1, 2)}
ll = np.array([d["lluvia_mm_hasta_h"] for d in R["diario"]])
eto = np.array([d["eto_mm_hasta_h"] for d in R["diario"]])
ll_d, eto_d = np.diff(ll, prepend=0), np.diff(eto, prepend=0)

# ---------------------------------------------------------------- serie de 31 días
fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.0, 4.6), sharex=True, gridspec_kw={"height_ratios": [1.5, 1]})
x = np.arange(H)
a1.bar(x, pct[0], color=VERDE, width=0.85, label="Suficiente")
a1.bar(x, pct[1], bottom=pct[0], color=AMAR, width=0.85, label="Media")
a1.bar(x, pct[2], bottom=np.array(pct[0]) + np.array(pct[1]), color=ROJO, width=0.85, label="Baja")
a1.set_ylabel("Área en cultivo (%)"), a1.set_ylim(0, 100)
a1.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.2), fontsize=8.5)
dm = [float(np.median(DEF[en, h])) for h in range(H)]
a2.bar(x - 0.2, ll_d, width=0.4, color=AZUL, label="Lluvia GEOS-S2S (mm/día)")
a2.bar(x + 0.2, eto_d, width=0.4, color="#B4B2A9", label="ETo (mm/día)")
a3 = a2.twinx()
a3.plot(x, dm, "-", color=OSC, lw=1.5, label="Déficit mediano (mm)")
a3.axhline(4.34, color=ROJO, lw=0.8, ls="--"), a3.axhline(0, color=VERDE, lw=0.8, ls="--")
a3.set_ylabel("Déficit Dr (mm)"), a3.spines["right"].set_visible(True), a3.yaxis.set_major_formatter(coma)
a2.set_ylabel("mm/día"), a2.set_xlabel(f"Días desde hoy ({R['hoy']})")
h1, l1 = a2.get_legend_handles_labels()
h2, l2 = a3.get_legend_handles_labels()
a2.legend(h1 + h2, l1 + l2, frameon=False, fontsize=7.8, ncol=3, loc="upper center", bbox_to_anchor=(0.5, 1.28))
fig.align_ylabels((a1, a2))
fig.tight_layout(h_pad=2.2)
fig.savefig(OUT / "fig_res_serie30.png")
plt.close(fig)

# ---------------------------------------------------------------- mapas a 0, 7, 15 y 30 días
fig, axs = plt.subplots(1, 4, figsize=(7.2, 2.1))
for ax, h in zip(axs, (0, 7, 15, 30)):
    im = np.array(Image.open(SEM / f"semaforo_h{h:02d}.png").convert("RGBA")).astype(float) / 255
    fondo = np.ones_like(im)
    fondo[..., :3] = 0.93
    a = im[..., 3:4]
    comp = im[..., :3] * a + fondo[..., :3] * (1 - a)
    ax.imshow(comp, interpolation="nearest")
    ax.set_title("Hoy" if h == 0 else f"+{h} días", fontsize=9)
    ax.set_xticks([]), ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(True), s.set_color("#B4B2A9"), s.set_linewidth(0.6)
axs[0].plot([10, 60], [192, 192], color=OSC, lw=1.5)
axs[0].text(35, 186, "500 m", ha="center", va="bottom", fontsize=7)
fig.tight_layout(w_pad=0.6)
fig.savefig(OUT / "fig_res_mapas.png")
plt.close(fig)

# ---------------------------------------------------------------- días hasta humedad baja
roja = CL[en] == 2
dias = np.where(roja.any(1), roja.argmax(1), -1)
cuenta = {d: int((dias == d).sum()) for d in range(H)}
fig, ax = plt.subplots(figsize=(5.4, 2.4))
xs = [d for d in range(H) if cuenta[d] > 0]
ax.bar(xs, [100 * cuenta[d] / en.sum() for d in xs], color=ROJO, width=0.7)
for d in xs:
    ax.text(d, 100 * cuenta[d] / en.sum() + 1.5, f"{100 * cuenta[d] / en.sum():.1f} %".replace(".", ","), ha="center", fontsize=8)
ax.set_xlabel("Días hasta humedad baja si no se riega desde hoy"), ax.set_ylabel("Píxeles en cultivo (%)")
ax.set_xlim(-0.5, 10.5), ax.set_xticks(range(0, 11)), ax.set_ylim(0, 105)
fig.savefig(OUT / "fig_res_dias_baja.png")
plt.close(fig)

res = {"hoy": R["hoy"], "pixeles_cultivo": int(en.sum()), "ha_cultivo": round(en.sum() / 100, 1),
       "pct_por_h": {h: [round(pct[k][h], 1) for k in (0, 1, 2)] for h in (0, 7, 15, 30)},
       "deficit_mediano": {h: round(dm[h], 2) for h in (0, 7, 15, 30)}, "dias_baja": {d: c for d, c in cuenta.items() if c},
       "lluvia_acum": {h: ll[h] for h in (7, 15, 30)}, "eto_acum": {h: eto[h] for h in (7, 15, 30)},
       "sin_rojo": int((dias < 0).sum()), "lluvia_hueco": R.get("lluvia_hueco")}
json.dump(res, open(OUT / "resultados_semaforo.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
print(json.dumps(res, ensure_ascii=False, default=float))
