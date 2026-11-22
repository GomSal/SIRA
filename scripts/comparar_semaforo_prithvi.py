"""Compara el semáforo con y sin Prithvi sobre las MISMAS 198 mediciones de prueba (mismas particiones externas).

Cada variante ya midió su prueba una sola vez (scripts/semaforo_clasificador.py); aquí solo se comparan esas
predicciones, pareadas, con IC 95 % por bootstrap de bloques de 10 días (1000). Diferencia = variante − base.
Salida: reports/semaforo_clasificador_ndvi_prithvi/comparacion_con_base.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

RAIZ = Path(__file__).resolve().parents[1]
R = RAIZ / "reports"
VAR = {"base (sin Prithvi)": R / "semaforo_clasificador",
       "con y sin Prithvi, decide la validación": R / "semaforo_clasificador_ndvi_prithvi",
       "Prithvi obligatorio (cabeza sobre Prithvi)": R / "semaforo_clasificador_prithvi_obligatorio"}
CL = ["verde", "amarillo", "rojo"]

D = {k: pd.read_csv(v / "predicciones_prueba.csv") for k, v in VAR.items()}
ref = D["base (sin Prithvi)"]
for k, d in D.items():
    assert (d[["temporada", "tratamiento", "das"]].values == ref[["temporada", "tratamiento", "das"]].values).all(), k
y = ref.clase.map({c: i for i, c in enumerate(CL)}).to_numpy()
G = (ref.temporada * 100 + (ref.das - 1) // 10).to_numpy()
P = {k: d[[f"p_{c}" for c in CL]].to_numpy() for k, d in D.items()}


def met(yy, PP):
    pred = PP.argmax(1)
    out = {"auc_macro": np.mean([roc_auc_score(yy == j, PP[:, j]) for j in range(3)])}
    for j, c in enumerate(CL):
        out[f"recall_{c}"] = np.mean(pred[yy == j] == j)
    out["exactitud_balanceada"] = np.mean([out[f"recall_{c}"] for c in CL])
    return out


rng = np.random.default_rng(0)
u = np.unique(G)
ix = {g: np.where(G == g)[0] for g in u}
muestras = []
for _ in range(1000):
    ii = np.concatenate([ix[g] for g in rng.choice(u, len(u), replace=True)])
    if len(np.unique(y[ii])) == 3:
        muestras.append(ii)

filas = []
base = "base (sin Prithvi)"
m_base = met(y, P[base])
for k in VAR:
    m = met(y, P[k])
    for q, v in m.items():
        fila = {"variante": k, "metrica": q, "valor": round(v, 3)}
        if k != base:
            dif = np.array([met(y[ii], P[k][ii])[q] - met(y[ii], P[base][ii])[q] for ii in muestras])
            fila.update(dif_vs_base=round(v - m_base[q], 3),
                        dif_IC95=f"{np.quantile(dif, .025):+.3f} a {np.quantile(dif, .975):+.3f}")
        filas.append(fila)
T = pd.DataFrame(filas)
T.to_csv(R / "semaforo_clasificador_ndvi_prithvi" / "comparacion_con_base.csv", index=False)
print(T.to_string(index=False))
