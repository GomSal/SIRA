"""Balance hídrico calibrado (reservorio con memoria) contra el Dr medido con TDR en Nataima.

El balance FAO-56 con los parámetros del artículo (TAW = 6,7 mm, drenaje rápido) no reproduce el TDR
(reports/balance_hidrico/): el suelo se mantiene sobre capacidad de campo días después de cada riego y la lluvia
se pierde en gran parte por percolación (el artículo lo dice). Aquí se prueba un reservorio lineal:
    S(t) = S(t−1)·exp(−1/τ) + α·lluvia + riego − Kc_FAO·ETo
    Dr_estimado = c0·S + c1
τ (memoria, días) y α (fracción efectiva de la lluvia) y c0, c1 se calibran con 2 temporadas y se prueba en la 3ª.
Usa el riego REAL del artículo: es el techo de lo que puede lograr un balance; no es la versión de producción.
"""
import itertools
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

RAIZ = Path(__file__).resolve().parents[1]
src = (RAIZ / "scripts/prueba_balance.py").read_text(encoding="utf-8").split("todo, params = []")[0]
ns = {"__file__": str(RAIZ / "scripts/prueba_balance.py")}
exec(compile(src, "prueba_balance", "exec"), ns)
S = {(t, tr): ns["series"](t, tr, None) for t in (1, 2, 3) for tr in ns["TRAT"]}


def reservorio(d, tau, alfa):
    s, out, dec = 0.0, [], np.exp(-1 / tau)
    for ll, ri, eto, das in zip(d.lluvia_mm.fillna(0), d.riego_mm.fillna(0), d.eto.fillna(d.eto.mean()), d.das):
        s = s * dec + alfa * ll + ri - ns["kc_fao"](np.array([das]))[0] * eto
        out.append(s)
    return np.array(out)


def datos(temps, tau, alfa):
    x, y = [], []
    for (t, tr), d in S.items():
        if t in temps:
            m = d.dr_obs.notna().to_numpy()
            x += list(reservorio(d, tau, alfa)[m]); y += list(d.dr_obs.to_numpy()[m])
    return np.array(x), np.array(y)


clase = lambda v: np.select([v < 0, v > 4.35], [0, 2], 1)  # noqa: E731
rejilla = list(itertools.product([2, 5, 10, 15, 20, 30, 45], [0, 0.1, 0.25, 0.5, 1.0]))
res = []
for prueba in (1, 2, 3):
    ent = [t for t in (1, 2, 3) if t != prueba]

    def ajuste(p):
        x, y = datos(ent, *p)
        A = np.c_[x, np.ones_like(x)]
        c = np.linalg.lstsq(A, y, rcond=None)[0]
        return np.sqrt(np.mean((A @ c - y) ** 2)), c

    mejor = min(rejilla, key=lambda p: ajuste(p)[0])
    c = ajuste(mejor)[1]
    x, y = datos([prueba], *mejor)
    yh = c[0] * x + c[1]
    yb = np.mean(datos(ent, *mejor)[1])
    res.append({"temporada_prueba": prueba, "tau_dias": mejor[0], "alfa_lluvia": mejor[1],
                "rmse_mm": round(float(np.sqrt(np.mean((yh - y) ** 2))), 2),
                "rmse_linea_base_mm": round(float(np.sqrt(np.mean((yb - y) ** 2))), 2),
                "spearman": round(float(spearmanr(yh, y)[0]), 2),
                "acierto_clase": round(float(np.mean(clase(yh) == clase(y))), 2),
                "acierto_clase_linea_base": round(float(np.mean(clase(np.full_like(y, yb)) == clase(y))), 2), "n": len(y)})
R = pd.DataFrame(res)
R.to_csv(RAIZ / "reports/balance_hidrico/reservorio_resultados.csv", index=False)
print(R.to_string(index=False))
