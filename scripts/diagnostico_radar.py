"""¿El radar Sentinel-1 ve el riego y la humedad en Nataima?

Para cada fecha de radar dentro de una siembra y cada tratamiento (según la rotación de franjas):
  - Dr medido (TDR, día medido o interpolado PCHIP a ≤ 2 días de una medición),
  - agua recibida en los últimos 1, 3 y 7 días (riego del tratamiento + lluvia del lote),
  - estado del reservorio calibrado S (con riego real; τ = 45 d, α = 0,5): lo que habría que recuperar,
  - VV, VH, VH−VV.
Dos lecturas:
  1. Global (Spearman entre todas las fechas): mezcla etapa, órbita y clima.
  2. Dentro de cada fecha: se resta la media de las 3 franjas ese día (mismo clima, etapa y órbita; distinto riego).
     Si el radar ve el agua, la franja más regada debe salir distinta de las otras ese mismo día.
Salida: reports/radar/diagnostico_radar.csv y datos_radar_tratamiento.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

RAIZ = Path(__file__).resolve().parents[1]
OUT = RAIZ / "reports" / "radar"
OUT.mkdir(parents=True, exist_ok=True)
SIEMBRAS = {1: pd.Timestamp("2021-05-27"), 2: pd.Timestamp("2021-12-04"), 3: pd.Timestamp("2022-07-09")}
ROT = {"este": {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("CD", "MIRI", "AWD+MIRI"), 3: ("AWD+MIRI", "CD", "MIRI")},
       "oeste": {1: ("MIRI", "AWD+MIRI", "CD"), 2: ("AWD+MIRI", "CD", "MIRI"), 3: ("CD", "MIRI", "AWD+MIRI")}}

radar = pd.read_csv(RAIZ / "data/interim/radar_s1_franjas.csv", parse_dates=["fecha"])
diario = pd.read_csv(RAIZ / "data/processed/nataima_diario.csv")
cont = pd.read_csv(RAIZ / "data/processed/humedad_continua.csv")


def reservorio(d: pd.DataFrame, tau: float = 45, alfa: float = 0.5) -> np.ndarray:
    s, out, dec = 0.0, [], np.exp(-1 / tau)
    eto = d.eto5_mm.clip(1, 8).fillna(4.0)
    for ll, ri, e in zip(d.lluvia_mm.fillna(0), d.riego_mm.fillna(0), eto):
        s = s * dec + alfa * ll + ri - 1.1 * e
        out.append(s)
    return np.array(out)


filas = []
for rot, asign in ROT.items():
    for t, d0 in SIEMBRAS.items():
        for pos, trat in zip(("O", "C", "E"), asign[t]):
            d = diario[(diario.temporada == t) & (diario.tratamiento == trat)].sort_values("das").reset_index(drop=True)
            d["S"] = reservorio(d)
            agua = d.riego_mm.fillna(0) + d.lluvia_mm.fillna(0)
            for k in (1, 3, 7):
                d[f"agua_{k}d"] = agua.rolling(k, min_periods=1).sum()
            d["riego_7d"] = d.riego_mm.fillna(0).rolling(7, min_periods=1).sum()
            c = cont[(cont.temporada == t) & (cont.tratamiento == trat)].set_index("das")
            r = radar[radar.franja_fisica == pos].copy()
            r["das"] = (r.fecha - d0).dt.days + 1
            for _, x in r[r.das.between(1, int(d.das.max()))].iterrows():
                fila = d[d.das == x.das]
                if fila.empty:
                    continue
                f = fila.iloc[0]
                vwc = c.vwc_pct.get(x.das, np.nan) if (x.das in c.index and c.dist_medicion_dias.get(x.das, 99) <= 2) else np.nan
                filas.append({"rotacion": rot, "temporada": t, "fecha": x.fecha.date(), "das": int(x.das), "orbita": x.orbita,
                              "tratamiento": trat, "franja": pos, "vv": x.vv_db, "vh": x.vh_db, "vh_vv": x.vh_vv_db,
                              "dr_tdr": 150 * (0.30 - vwc / 100) if vwc == vwc else np.nan, "S": f.S,
                              "agua_1d": f.agua_1d, "agua_3d": f.agua_3d, "agua_7d": f.agua_7d, "riego_7d": f.riego_7d})
D = pd.DataFrame(filas)
D.to_csv(OUT / "datos_radar_tratamiento.csv", index=False)

res = []
for rot, g in D.groupby("rotacion"):
    g = g.copy()
    for col in ("vv", "vh", "vh_vv", "dr_tdr", "S", "agua_1d", "agua_3d", "agua_7d", "riego_7d"):
        g[col + "_rel"] = g[col] - g.groupby(["temporada", "fecha"])[col].transform("mean")
    for objetivo in ("dr_tdr", "S", "agua_1d", "agua_3d", "agua_7d", "riego_7d"):
        for senal in ("vv", "vh", "vh_vv"):
            for modo, a, b in (("global", senal, objetivo), ("dentro_de_fecha", senal + "_rel", objetivo + "_rel")):
                m = g[a].notna() & g[b].notna()
                if m.sum() < 12:
                    continue
                rho, p = spearmanr(g.loc[m, a], g.loc[m, b])
                res.append({"rotacion": rot, "objetivo": objetivo, "senal": senal, "modo": modo,
                            "n": int(m.sum()), "spearman": round(rho, 2), "p": round(p, 3)})
R = pd.DataFrame(res)
R.to_csv(OUT / "diagnostico_radar.csv", index=False)
piv = R.pivot_table(index=["objetivo", "senal"], columns=["modo", "rotacion"], values="spearman")
print(piv.round(2).to_string())
print("\nfechas de radar dentro de siembras:", D.groupby("temporada").fecha.nunique().to_dict())
