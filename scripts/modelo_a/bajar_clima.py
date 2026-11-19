"""Clima diario 1981–2026 en Nataima para el modelo predictor (A).

NASA POWER (API diaria, comunidad AG): T máx, T mín, humedad relativa, viento a 2 m, radiación, precipitación.
  ETo FAO-56 Penman-Monteith con la misma función que el Modelo B (cloud/ronda1_b/job_b.py, eto_fao56).
  La radiación existe desde 1984-01-01; antes, la ETo queda vacía.
CHIRPS v2 diario vía ClimateSERV (datatype 0, promedio sobre 0,05° alrededor del lote), igual que reports/comparacion_lluvia.md.
Salida: data/interim/modelo_a/clima_1981_2026.csv
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "cloud/ronda1_b"))
from job_b import eto_fao56  # noqa: E402

LON, LAT = -74.9621, 4.1895
OUT = RAIZ / "data/interim/modelo_a"
OUT.mkdir(parents=True, exist_ok=True)


def get(url, t=180):
    for k in range(5):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=t).read())
        except Exception:  # noqa: BLE001
            if k == 4:
                raise
            time.sleep(10 * (k + 1))


# ---------------- NASA POWER
par = "T2M_MAX,T2M_MIN,RH2M,WS2M,ALLSKY_SFC_SW_DWN,PRECTOTCORR"
p = get(f"https://power.larc.nasa.gov/api/temporal/daily/point?parameters={par}&community=AG&longitude={LON}"
        f"&latitude={LAT}&start=19810101&end=20261118&format=JSON")["properties"]["parameter"]
df = pd.DataFrame({k: pd.Series(v) for k, v in p.items()})
df.index = pd.to_datetime(df.index, format="%Y%m%d")
df = df.replace(-999.0, np.nan)
df["eto"] = eto_fao56(df.T2M_MAX, df.T2M_MIN, df.RH2M, df.WS2M, df.ALLSKY_SFC_SW_DWN, df.index.dayofyear)
print("POWER", df.index.min().date(), df.index.max().date(), "ETo con dato desde", df.eto.first_valid_index().date(),
      "faltantes ETo tras 1984:", int(df.loc["1984":, "eto"].isna().sum()), flush=True)

# ---------------- CHIRPS (ClimateSERV), por tramos de 10 años
d = 0.025
geom = json.dumps({"type": "Polygon", "coordinates": [[[LON - d, LAT - d], [LON + d, LAT - d], [LON + d, LAT + d],
                                                        [LON - d, LAT + d], [LON - d, LAT - d]]]})
base = "https://climateserv.servirglobal.net/api/"
lluvia = {}
for a0 in range(1981, 2027, 10):
    a1 = min(a0 + 9, 2026)
    q = urllib.parse.urlencode({"datatype": 0, "begintime": f"01/01/{a0}", "endtime": f"12/31/{a1}" if a1 < 2026 else "11/18/2026",
                                "intervaltype": 0, "operationtype": 5, "geometry": geom})
    rid = get(base + "submitDataRequest/?" + q)[0]
    while True:
        prog = get(base + f"getDataRequestProgress/?id={rid}")[0]
        if prog in (100, 100.0) or prog == -1:
            break
        time.sleep(5)
    datos = get(base + f"getDataFromRequest/?id={rid}", t=600)["data"]
    for x in datos:
        v = x["value"]["avg"]
        lluvia[pd.Timestamp(x["date"]) if "date" in x else pd.Timestamp(x["year"], x["month"], x["day"])] = v
    print("CHIRPS", a0, a1, len(datos), flush=True)
df["lluvia"] = pd.Series(lluvia).sort_index().reindex(df.index)
df.loc[df.lluvia < -1, "lluvia"] = np.nan
df.index.name = "fecha"
df.to_csv(OUT / "clima_1981_2026.csv")
print("listo:", df.lluvia.first_valid_index().date(), df.lluvia.last_valid_index().date(),
      "faltantes lluvia:", int(df.lluvia.isna().sum()), "media lluvia mm/d", round(df.lluvia.mean(), 2),
      "media ETo", round(df.eto.mean(), 2))
