"""Semáforo por consumo del cultivo: ¿recomienda los mismos riegos que se le aplicaron a MIRI?

MIRI es el manejo óptimo del artículo: se programó con un balance hídrico, usó 22–54 % menos agua que CD y dio el
mismo rendimiento. Aquí un programador que solo usa satélite y clima decide cuándo y cuánto regar:
    Dr(t) = max(0, Dr(t−1) + Kc·ETo − α·lluvia)           déficit acumulado desde el último riego (mm)
    si Dr ≥ T (y días desde siembra ≤ 115):  regar I = f·Dr  y  Dr = 0
    Kc = 1,0·NDVI + 0,45 (NDVI Sentinel-2 de la franja de MIRI, rotación "oeste"); ETo NASA POWER; lluvia CHIRPS.
T (umbral, mm), α (lluvia efectiva) y f (lámina aplicada por mm de déficit: pérdidas por percolación) se calibran
con 2 temporadas y se prueban en la 3ª contra el riego real de MIRI (Fig. 4, digitalizada).
Comparaciones: Kc de la tabla FAO (sin satélite), Kc constante y riego a intervalo fijo (sin satélite ni clima).
Métricas en la temporada de prueba: error del total y de cada etapa, número de riegos, coincidencia de fechas
(un riego recomendado acierta si hay un riego real a ≤ 3 días) y error de la curva acumulada.
Salida: reports/semaforo/semaforo_consumo.csv
"""
import itertools
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
src = (RAIZ / "scripts/prueba_balance.py").read_text(encoding="utf-8").split("todo, params = []")[0]
ns = {"__file__": str(RAIZ / "scripts/prueba_balance.py")}
exec(compile(src, "prueba_balance", "exec"), ns)
OUT = RAIZ / "reports" / "semaforo"
OUT.mkdir(parents=True, exist_ok=True)
FIN_RIEGO = 115
CORTES = {1: (42, 82), 2: (48, 82), 3: (41, 81)}  # etapas del artículo (validación de la digitalización)

S = {t: ns["series"](t, "MIRI", "oeste").reset_index(drop=True) for t in (1, 2, 3)}
for t, d in S.items():
    d["kc_ndvi"] = np.clip(1.0 * d.ndvi.to_numpy() + 0.45, 0.6, 1.3)
    d["kc_fao"] = ns["kc_fao"](d.das.to_numpy())
    d["kc_cte"] = 1.1
    d["real"] = d.riego_mm.fillna(0)


def programar(d, kc, T, alfa, f):
    dr, out = 0.0, []
    eto, ll = d.eto.fillna(d.eto.mean()).to_numpy(), d.chirps.fillna(0).to_numpy()
    for i in range(len(d)):
        dr = max(0.0, dr + kc[i] * eto[i] - alfa * ll[i])
        if dr >= T and d.das.iloc[i] <= FIN_RIEGO:
            out.append(f * dr); dr = 0.0
        else:
            out.append(0.0)
    return np.array(out)


def intervalo_fijo(d, K, Q, inicio):
    return np.array([Q if (das >= inicio and das <= FIN_RIEGO and (das - inicio) % K == 0) else 0.0 for das in d.das])


def metricas(d, rec, t):
    real = d.real.to_numpy()
    ev_r, ev_p = d.das[real >= 5].to_numpy(), d.das[rec >= 5].to_numpy()
    acierto_p = np.mean([np.min(np.abs(ev_r - x)) <= 3 for x in ev_p]) if len(ev_p) and len(ev_r) else np.nan
    cubre_r = np.mean([np.min(np.abs(ev_p - x)) <= 3 for x in ev_r]) if len(ev_p) and len(ev_r) else np.nan
    b1, b2 = CORTES[t]
    etapa = lambda v: (v[d.das <= b1].sum(), v[(d.das > b1) & (d.das <= b2)].sum(), v[d.das > b2].sum())  # noqa: E731
    er, ep = etapa(real), etapa(rec)
    return {"total_real_mm": round(real.sum(), 1), "total_recom_mm": round(rec.sum(), 1),
            "error_total_pct": round(100 * (rec.sum() - real.sum()) / real.sum(), 1),
            "etapas_real_mm": "/".join(f"{x:.0f}" for x in er), "etapas_recom_mm": "/".join(f"{x:.0f}" for x in ep),
            "riegos_real": len(ev_r), "riegos_recom": len(ev_p),
            "recom_con_riego_real_a_3d_pct": round(100 * acierto_p, 0), "riegos_reales_cubiertos_pct": round(100 * cubre_r, 0),
            "rmse_acumulado_mm": round(float(np.sqrt(np.mean((np.cumsum(rec) - np.cumsum(real)) ** 2))), 1)}


def costo(rec_fn, temps):
    return float(np.mean([np.sqrt(np.mean((np.cumsum(rec_fn(S[t])) - np.cumsum(S[t].real.to_numpy())) ** 2)) for t in temps]))


res = []
for prueba in (1, 2, 3):
    ent = [t for t in (1, 2, 3) if t != prueba]
    for nombre, col in (("Kc desde NDVI (satélite)", "kc_ndvi"), ("Kc tabla FAO (sin satélite)", "kc_fao"),
                        ("Kc constante 1,1", "kc_cte")):
        rej = list(itertools.product([5, 10, 15, 20, 25, 30, 40], [0.25, 0.5, 0.75, 1.0], [1.0, 1.25, 1.5, 2.0, 2.5, 3.0]))
        mejor = min(rej, key=lambda p: costo(lambda d: programar(d, d[col].to_numpy(), *p), ent))
        rec = programar(S[prueba], S[prueba][col].to_numpy(), *mejor)
        res.append({"variante": nombre, "temporada_prueba": prueba, "T_mm": mejor[0], "alfa": mejor[1], "f": mejor[2],
                    **metricas(S[prueba], rec, prueba)})
    rej = list(itertools.product(range(3, 10), [10, 15, 20, 25, 30], [5, 10, 15, 20, 30, 35]))
    mejor = min(rej, key=lambda p: costo(lambda d: intervalo_fijo(d, *p), ent))
    rec = intervalo_fijo(S[prueba], *mejor)
    res.append({"variante": "Intervalo fijo (sin satélite ni clima)", "temporada_prueba": prueba,
                "T_mm": f"cada {mejor[0]} d", "alfa": f"{mejor[1]} mm", "f": f"desde día {mejor[2]}",
                **metricas(S[prueba], rec, prueba)})
R = pd.DataFrame(res)
R.to_csv(OUT / "semaforo_consumo.csv", index=False)
pd.set_option("display.width", 250)
print(R[["variante", "temporada_prueba", "T_mm", "alfa", "f", "total_real_mm", "total_recom_mm", "error_total_pct",
         "etapas_real_mm", "etapas_recom_mm", "riegos_real", "riegos_recom", "recom_con_riego_real_a_3d_pct",
         "riegos_reales_cubiertos_pct", "rmse_acumulado_mm"]].to_string(index=False))
print("\nPromedio de las 3 temporadas de prueba:")
print(R.groupby("variante")[["error_total_pct", "recom_con_riego_real_a_3d_pct", "riegos_reales_cubiertos_pct",
                              "rmse_acumulado_mm"]].agg(lambda s: round(np.mean(np.abs(s)) if s.name == "error_total_pct" else s.mean(), 1)).to_string())
