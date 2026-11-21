"""Curvas ROC del semáforo B (variante ndvi_prithvi) para cada familia de modelo, sobre la MISMA prueba sellada.

Cada familia se corrió con scripts/semaforo_clasificador.py ndvi_prithvi <familia>: mismas 4 particiones de prueba,
selección de hiperparámetros (y de usar o no Prithvi) en validación dentro de la familia, una medición por punto.
Curva ROC macro (cada clase contra las demás, promedio de la tasa de verdaderos positivos sobre una rejilla común de
falsos positivos). IC 95 % del AUC macro por bootstrap de bloques de 10 días (1000).
Salida: reports/semaforo_clasificador_ndvi_prithvi/roc_familias.csv (puntos) y roc_auc.csv (AUC por familia).
"""
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, roc_curve

RAIZ = Path(__file__).resolve().parents[1]
BASE = RAIZ / "reports" / "semaforo_clasificador_ndvi_prithvi"
FAM = {"gb": "Gradient Boosting", "xgb": "XGBoost", "rf": "Random Forest", "arimax": "ARIMAX",
       "logistica": "Regresión logística"}
CL = ["verde", "amarillo", "rojo"]
REJ = np.linspace(0, 1, 101)

curvas, aucs = [], []
rng = np.random.default_rng(0)
for k, nombre in FAM.items():
    d = pd.read_csv(BASE / f"familia_{k}" / "predicciones_prueba.csv")
    y = d.clase.map({c: i for i, c in enumerate(CL)}).to_numpy()
    P = d[[f"p_{c}" for c in CL]].to_numpy()
    tpr = np.mean([np.interp(REJ, *roc_curve(y == j, P[:, j])[:2]) for j in range(3)], axis=0)
    tpr[0] = 0.0
    curvas += [{"familia": nombre, "fpr": f, "tpr": t} for f, t in zip(REJ, tpr)]
    auc = np.mean([roc_auc_score(y == j, P[:, j]) for j in range(3)])
    g = (d.temporada * 100 + (d.das - 1) // 10).to_numpy()
    u = np.unique(g)
    ix = {b: np.where(g == b)[0] for b in u}
    bs = []
    for _ in range(1000):
        ii = np.concatenate([ix[b] for b in rng.choice(u, len(u))])
        if len(np.unique(y[ii])) == 3:
            bs.append(np.mean([roc_auc_score(y[ii] == j, P[ii, j]) for j in range(3)]))
    sel = pd.read_csv(BASE / f"familia_{k}" / "prueba_por_particion.csv").hiperparametros
    aucs.append({"familia": nombre, "auc_macro": round(auc, 3), "ic95_bajo": round(np.quantile(bs, .025), 3),
                 "ic95_alto": round(np.quantile(bs, .975), 3),
                 "particiones_con_prithvi": int(sel.str.contains("prithvi").sum())})
pd.DataFrame(curvas).round(4).to_csv(BASE / "roc_familias.csv", index=False)
A = pd.DataFrame(aucs).sort_values("auc_macro", ascending=False)
A.to_csv(BASE / "roc_auc.csv", index=False)
print(A.to_string(index=False))
