"""Figuras y tablas de la sección 3.3 (modelos). Salida: docs/informe_final/figs/ y figs/tablas_modelos.json.

Fuentes (resultados ya guardados, no se reentrena nada):
  A: data/interim/modelo_a/kaggle_final/resultados_a/paso7_validacion.csv, paso8_prueba_clima_observado.csv
  B: reports/semaforo_clasificador_ndvi_prithvi/roc_familias.csv, roc_auc.csv, familia_*/validacion_modelo_de_uso.csv
"""
import json
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import pandas as pd  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
RES_A = RAIZ / "data/interim/modelo_a/kaggle_final/resultados_a"
RES_B = RAIZ / "reports/semaforo_clasificador_ndvi_prithvi"
OUT = Path(__file__).resolve().parent / "figs"
plt.rcParams.update({"font.family": "Arial", "font.size": 9.5, "axes.spines.top": False, "axes.spines.right": False,
                     "savefig.dpi": 220, "savefig.bbox": "tight"})
coma = matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:g}".replace(".", ","))
T = {}

# ---------------------------------------------------------------- A: mejor configuración por familia (validación)
v = pd.read_csv(RES_A / "paso7_validacion.csv")
v = v[~v.modelo.isin(["persistencia", "climatología"]) & v.skill_medio.notna()]
fam = v.loc[v.groupby(["indice", "modelo"]).skill_medio.idxmax()]
T["a_familias"] = {f"{r.indice}|{r.modelo}": [round(r.skill_medio, 3), round(r.rmse, 3), r.empata_con_el_mejor]
                   for r in fam.itertuples()}

# ---------------------------------------------------------------- A: skill por horizonte en la prueba sellada
p = pd.read_csv(RES_A / "paso8_prueba_clima_observado.csv")
p = p[p.grupo.str.match(r"h\d+$")].copy()
p["h"] = p.grupo.str[1:].astype(int)
fig, ax = plt.subplots(figsize=(6.2, 2.8))
for ind, col, nom in (("ndvi", "#2E9E5B", "NDVI"), ("ndmi", "#2F7FC1", "NDMI")):
    q = p[p.indice == ind].sort_values("h")
    lo = q.skill_vs_persistencia_IC95.str.extract(r"([+-]?[\d.]+) a ([+-]?[\d.]+)").astype(float)
    ax.fill_between(q.h, lo[0], lo[1], color=col, alpha=0.15, lw=0)
    ax.plot(q.h, q.skill_vs_persistencia, "-o", color=col, ms=4, lw=1.4, label=f"{nom} frente a persistencia")
    ax.plot(q.h, q.skill_vs_climatologia, "--", color=col, lw=1.0, alpha=0.8, label=f"{nom} frente a climatología")
    T[f"a_prueba_{ind}"] = q[["h", "rmse", "skill_vs_persistencia", "skill_vs_persistencia_IC95"]].round(3).to_dict("records")
ax.axhline(0, color="#2C2C2A", lw=0.7)
ax.set_xticks([1, 3, 7, 15, 30]), ax.set_xlabel("Horizonte de pronóstico (días)")
ax.set_ylabel("Skill = 1 − RMSE / RMSE base"), ax.yaxis.set_major_formatter(coma)
ax.legend(frameon=False, fontsize=7.8, ncol=2, loc="upper center", bbox_to_anchor=(0.5, 1.22))
fig.savefig(OUT / "fig_modelo_a_skill.png")
plt.close(fig)

# ---------------------------------------------------------------- B: curvas ROC por familia (prueba sellada)
r = pd.read_csv(RES_B / "roc_familias.csv")
auc = pd.read_csv(RES_B / "roc_auc.csv").set_index("familia")
cols = {"ARIMAX": "#534AB7", "Random Forest": "#2E9E5B", "XGBoost": "#E0A800", "Gradient Boosting": "#2F7FC1",
        "Regresión logística": "#888780"}
fig, ax = plt.subplots(figsize=(4.4, 3.9))
for f_, c_ in cols.items():
    g = r[r.familia == f_]
    a = auc.loc[f_]
    ax.plot(g.fpr, g.tpr, color=c_, lw=2.0 if f_ == "ARIMAX" else 1.2,
            label=f"{f_}: {a.auc_macro:.2f} [{a.ic95_bajo:.2f}–{a.ic95_alto:.2f}]".replace(".", ","))
ax.plot([0, 1], [0, 1], ":", color="#B4B2A9", lw=1)
ax.set_xlabel("Tasa de falsos positivos"), ax.set_ylabel("Tasa de verdaderos positivos")
ax.xaxis.set_major_formatter(coma), ax.yaxis.set_major_formatter(coma)
ax.set_aspect("equal"), ax.set_xlim(0, 1), ax.set_ylim(0, 1.0)
ax.legend(frameon=False, fontsize=7.6, loc="lower right", title="AUC macro [IC 95 %]", title_fontsize=7.8)
fig.savefig(OUT / "fig_modelo_b_roc.png")
plt.close(fig)

# validación de cada familia (modelo de uso)
vb = {}
for d in sorted(RES_B.glob("familia_*")):
    m = json.load(open(d / "modelo_de_uso.json", encoding="utf-8"))
    vb[m["elegido"]] = {"auc_val": round(m["auc_macro_validacion"], 3), "hiper": m["hiperparametros"]}
T["b_familias_validacion"] = vb
json.dump(T, open(OUT / "tablas_modelos.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print(json.dumps({k: T[k] for k in ("a_familias", "b_familias_validacion")}, ensure_ascii=False, indent=1, default=str))
