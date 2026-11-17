"""Gráficas de control del dataset final: una por temporada (lluvia, riego y humedad medida)."""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
d = pd.read_csv(RAIZ / "data" / "processed" / "nataima_diario.csv")
COL = {"CD": "#1f4e79", "MIRI": "#3a7d1e", "AWD+MIRI": "#d06010"}

for t, g in d.groupby("temporada"):
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True, gridspec_kw={"height_ratios": [1, 1.3]})
    dia = g.drop_duplicates("das")
    a1.bar(dia.das, dia.lluvia_mm, color="#5ab4e5", label="lluvia (Fig. 2)")
    for i, (s, c) in enumerate(COL.items()):
        h = g[(g.tratamiento == s) & (g.riego_mm > 0)]
        a1.bar(h.das + (i - 1) * 0.28, h.riego_mm, width=0.28, color=c, label=f"riego {s} (Fig. 4)")
    a1.set_ylabel("mm/día")
    a1.legend(ncol=4, fontsize=8)
    a1.set_title(f"Nataima GS{t}: datos digitalizados de Martínez-Vega et al. (2025)")
    for s, c in COL.items():
        h = g[(g.tratamiento == s) & g.vwc_pct.notna()]
        a2.errorbar(h.das, h.vwc_pct, yerr=h.vwc_ee_pct, fmt="o", ms=4, color=c, capsize=2, label=s)
    for v, n in ((47.65, "SAT"), (30, "FC"), (27.1, "umbral estrés"), (26, "PWP")):
        a2.axhline(v, ls="--", lw=0.8, color="gray")
        a2.text(g.das.max() + 1, v, n, fontsize=8, va="center")
    for e in ("reproductiva", "maduracion"):
        a2.axvline(g[g.etapa == e].das.min() - 0.5, color="k", lw=0.6)
    a2.set_ylabel("humedad volumétrica a 20 cm (%)")
    a2.set_xlabel("días después de siembra")
    a2.legend(fontsize=8)
    out = RAIZ / "reports" / "control" / f"nataima_GS{t}.png"
    fig.savefig(out, dpi=100, bbox_inches="tight")
    print(out)
