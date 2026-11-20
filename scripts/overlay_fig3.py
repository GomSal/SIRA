"""Control de la Fig. 3: mediciones digitalizadas sobre la imagen original."""
import sys
from pathlib import Path
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sira.digitalizar import fig3  # noqa: E402
from sira.digitalizar.comun import FIGS, RAIZ, cargar_rgb  # noqa: E402

t = int(sys.argv[1]); d0, d1 = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (25, 125)
a = cargar_rgb(FIGS / "fig3.png"); df, meta = fig3.digitalizar()
m = meta[meta.temporada == t].iloc[0]
x0 = int(m.x_dia1 + (d0 - 1) * m.px_por_dia); x1 = int(m.x_dia1 + (d1 - 1) * m.px_por_dia)
y0 = int(m.fila_10pct - (48 - 10) * m.px_por_pct); y1 = int(m.fila_10pct - (18 - 10) * m.px_por_pct)
fig, ax = plt.subplots(figsize=(18, 9))
ax.imshow(a[y0:y1, x0:x1].astype(np.uint8), extent=(x0, x1, y1, y0))
g = df[(df.temporada == t) & df.dia_x.between(d0, d1)]
for s, c in (("CD", "red"), ("MIRI", "lime"), ("AWD+MIRI", "magenta"), ("?", "black")):
    h = g[g.tratamiento == s]
    x = m.x_dia1 + (h.dia_x + m.desfase_dias - 1) * m.px_por_dia
    y = m.fila_10pct - (h.vwc_pct - 10) * m.px_por_pct
    b = (h.fuente == "barra").values
    ax.plot(x[b], y[b], "x", color=c, ms=9, mew=2, label=f"{s} barra ({b.sum()})")
    ax.plot(x[~b], y[~b], "o", mfc="none", color=c, ms=7, label=f"{s} solo rombo ({(~b).sum()})")
ax.legend(loc="upper right")
out = RAIZ / "reports" / "control" / f"fig3_gs{t}_{d0}-{d1}.png"
fig.savefig(out, dpi=80, bbox_inches="tight"); print(out)
