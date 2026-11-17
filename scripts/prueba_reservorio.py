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
import sys
ROT = sys.argv[1] if len(sys.argv) > 1 else None   # None = Kc tabla FAO; "este"/"oeste" = Kc desde NDVI
S = {(t, tr): ns["series"](t, tr, ROT) for t in (1, 2, 3) for tr in ns["TRAT"]}


def reservorio(d, tau, alfa, a=None, b=None):
    """Kc: tabla FAO-56 si a es None; si no, Kc = a·NDVI + b (NDVI Sentinel-2 de la franja, interpolado)."""
    kc = ns["kc_fao"](d.das.to_numpy()) if a is None else np.clip(a * d.ndvi.to_numpy() + b, 0.2, 1.5)
    s, out, dec = 0.0, [], np.exp(-1 / tau)
    for ll, ri, eto, k in zip(d.lluvia_mm.fillna(0), d.riego_mm.fillna(0), d.eto.fillna(d.eto.mean()), kc):
        s = s * dec + alfa * ll + ri - k * eto
        out.append(s)
    return np.array(out)


def datos(temps, tau, alfa, a=None, b=None):
    x, y = [], []
    for (t, tr), d in S.items():
        if t in temps:
            m = d.dr_obs.notna().to_numpy()
            x += list(reservorio(d, tau, alfa, a, b)[m]); y += list(d.dr_obs.to_numpy()[m])
    return np.array(x), np.array(y)


clase = lambda v: np.select([v < 0, v > 4.35], [0, 2], 1)  # noqa: E731
rejilla = list(itertools.product([2, 5, 10, 15, 20, 30, 45], [0, 0.1, 0.25, 0.5, 1.0]))
MODO = sys.argv[2] if len(sys.argv) > 2 else "libre"
if ROT:  # Kc desde NDVI: se calibran también a y b (recta NDVI → Kc)
    TAUS, ALFAS = [10, 20, 30, 45, 60, 90], [0.1, 0.25, 0.5, 0.75]
    if MODO == "libre":  # rejilla amplia; los óptimos quedan en el borde con Kc físicamente irreales
        AB = [(a, b) for a in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0) for b in (-0.4, -0.2, 0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2)]
    elif MODO == "fisico":  # solo rectas que dejan el Kc dentro del rango del arroz (0,6–1,3) con NDVI 0,2–0,85
        AB = [(a, b) for a in np.arange(0.1, 1.31, 0.1) for b in np.arange(0.3, 1.21, 0.05)
              if 0.6 <= a * 0.2 + b and a * 0.85 + b <= 1.3]
    else:  # "constante": control sin NDVI (a = 0, Kc = b)
        AB = [(0.0, b) for b in (0.6, 0.8, 1.0, 1.05, 1.1, 1.2, 1.3)]
    rejilla = [(t, al, a, b) for t, al in itertools.product(TAUS, ALFAS) for a, b in AB]
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
    res.append({"kc": f"NDVI {ROT} ({MODO})" if ROT else "tabla FAO", "temporada_prueba": prueba, "tau_dias": mejor[0],
                "alfa_lluvia": mejor[1], "kc_a": mejor[2] if ROT else None, "kc_b": mejor[3] if ROT else None,
                "rmse_mm": round(float(np.sqrt(np.mean((yh - y) ** 2))), 2),
                "rmse_linea_base_mm": round(float(np.sqrt(np.mean((yb - y) ** 2))), 2),
                "spearman": round(float(spearmanr(yh, y)[0]), 2),
                "acierto_clase": round(float(np.mean(clase(yh) == clase(y))), 2),
                "acierto_clase_linea_base": round(float(np.mean(clase(np.full_like(y, yb)) == clase(y))), 2), "n": len(y)})
R = pd.DataFrame(res)
R.to_csv(RAIZ / f"reports/balance_hidrico/reservorio_resultados_{ROT + '_' + MODO if ROT else 'fao'}.csv", index=False)
print(R.to_string(index=False))
