"""Curvas continuas de humedad del suelo por franja (tratamiento) y temporada.

Interpolación PCHIP (monótona por tramos): pasa exactamente por cada medición y, a diferencia de la curva
suavizada de Excel del artículo, nunca inventa picos ni valles más allá de los valores medidos vecinos.
No se extrapola: antes de la primera medición y después de la última no hay valor.

Salida: data/processed/humedad_continua.csv con
  temporada, tratamiento, das, fecha, vwc_pct, tipo (medido | interpolado),
  hueco_dias (días entre las dos mediciones que encierran ese día), dist_medicion_dias.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

RAIZ = Path(__file__).resolve().parents[3]


def fechas_siembra() -> dict[int, pd.Timestamp]:
    """Fechas estimadas al alinear la lluvia del artículo con CHIRPS/POWER/ERA5 (scripts/comparar_lluvia.py)."""
    ll = pd.read_csv(RAIZ / "data" / "interim" / "lluvia_fuentes_diaria.csv", parse_dates=["fecha"])
    return {int(t): g.loc[g.das == 1, "fecha"].iloc[0] for t, g in ll.groupby("temporada")}


def curvas() -> pd.DataFrame:
    d = pd.read_csv(RAIZ / "data" / "processed" / "nataima_diario.csv")
    siembra = fechas_siembra()
    filas = []
    for (t, s), g in d.dropna(subset=["vwc_pct"]).groupby(["temporada", "tratamiento"]):
        g = g.sort_values("das")
        x, y = g.das.to_numpy(float), g.vwc_pct.to_numpy(float)
        f = PchipInterpolator(x, y, extrapolate=False)
        dias = np.arange(int(x.min()), int(x.max()) + 1)
        medidos = set(g.das.astype(int))
        for dia in dias:
            i = np.searchsorted(x, dia)
            hueco = 0 if dia in medidos else int(x[i] - x[i - 1])
            filas.append({"temporada": int(t), "tratamiento": s, "das": int(dia),
                          "fecha": (siembra[int(t)] + pd.Timedelta(days=int(dia) - 1)).date(),
                          "vwc_pct": round(float(f(dia)), 2),
                          "tipo": "medido" if dia in medidos else "interpolado",
                          "hueco_dias": hueco,
                          "dist_medicion_dias": int(np.min(np.abs(x - dia)))})
    return pd.DataFrame(filas)


def main() -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    c = curvas()
    c.to_csv(RAIZ / "data" / "processed" / "humedad_continua.csv", index=False)
    col = {"CD": "#1f4e79", "MIRI": "#3a7d1e", "AWD+MIRI": "#d06010"}
    fig, axs = plt.subplots(3, 1, figsize=(12, 11), sharex=True)
    for ax, (t, g) in zip(axs, c.groupby("temporada")):
        for s, h in g.groupby("tratamiento"):
            ax.plot(h.das, h.vwc_pct, color=col[s], lw=1.5, label=f"{s} (PCHIP)")
            m = h[h.tipo == "medido"]
            ax.plot(m.das, m.vwc_pct, "o", color=col[s], ms=4)
        for v, n in ((30, "FC"), (27.1, "umbral déficit"), (26, "PWP")):
            ax.axhline(v, ls="--", lw=0.7, color="gray")
            ax.text(g.das.max() + 1, v, n, fontsize=8, va="center")
        siembra = pd.Timestamp(g.fecha.iloc[0]) - pd.Timedelta(days=int(g.das.iloc[0]) - 1)
        ax.set_title(f"GS{t}: humedad del suelo a 20 cm (siembra ≈ {siembra.date()})", fontsize=10)
        ax.set_ylabel("VWC (%)")
        ax.legend(fontsize=8, ncol=3)
    axs[-1].set_xlabel("días después de siembra (puntos = medición; línea = interpolación PCHIP)")
    fig.tight_layout()
    fig.savefig(RAIZ / "reports" / "control" / "humedad_continua.png", dpi=110)
    res = c.groupby(["temporada", "tratamiento"]).agg(
        dias=("das", "size"), medidos=("tipo", lambda s: (s == "medido").sum()),
        desde=("das", "min"), hasta=("das", "max"), hueco_max=("hueco_dias", "max"))
    print(res.to_string())


if __name__ == "__main__":
    main()
