"""Zoom de control de la Fig. 4: imagen + rejilla de días + puntos digitalizados."""
import sys
from pathlib import Path
import numpy as np, matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sira.digitalizar import fig4  # noqa: E402
from sira.digitalizar.comun import FIGS, RAIZ, cargar_rgb  # noqa: E402

t, d0, d1, vmax = (int(v) for v in sys.argv[1:5])
a = cargar_rgb(FIGS / "fig4.png"); df, meta = fig4.digitalizar()
m = meta[meta.temporada == t].iloc[0]
x0 = int(m.x_dia1 + (d0 - 1.5) * m.px_por_dia); x1 = int(m.x_dia1 + (d1 - 0.5) * m.px_por_dia)
y1 = int(m.fila_0mm) + 5; y0 = int(m.fila_0mm - vmax * m.px_por_100mm / 100)
fig, ax = plt.subplots(figsize=(15, 8))
ax.imshow(a[y0:y1, x0:x1].astype(np.uint8), extent=(x0, x1, y1, y0))
for d in range(d0, d1 + 1):
    xx = m.x_dia1 + (d - 1) * m.px_por_dia
    ax.axvline(xx, color="k", lw=0.4); ax.text(xx, y0 + 6, str(d), fontsize=7)
g = df[(df.temporada == t) & df.das.between(d0, d1)]
for s, c in (("CD", "red"), ("MIRI", "magenta"), ("AWD+MIRI", "k")):
    ax.plot(m.x_dia1 + (g.das - 1) * m.px_por_dia, m.fila_0mm - g["riego_acum_" + s] * m.px_por_100mm / 100,
            "o", color=c, ms=4, label=s)
ax.legend(loc="upper left")
out = RAIZ / "reports" / "control" / f"fig4_gs{t}_{d0}-{d1}.png"
fig.savefig(out, dpi=90, bbox_inches="tight"); print(out)
