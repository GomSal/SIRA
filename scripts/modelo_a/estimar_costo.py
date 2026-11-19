"""Estimado de complejidad, tiempo y costo del entrenamiento del modelo predictor (A), a partir de mediciones reales.

Entradas medidas (reports/modelo_a/estimacion/):
  stac_conteo.json    fechas por año y sensor sobre Nataima (Planetary Computer)
  bench_lectura.json  tamaño de archivo y tiempo de lectura de ventanas de una escena S2 y una Landsat
  bench_computo.json  ns por pixel·fecha del preprocesado (numpy y pandas) y segundos por ajuste de cada candidato
Supuestos (escritos en la salida): fracción despejada S2 0,28 y Landsat 0,30 (informe §4), 5 particiones de ventana
deslizante, rejilla de hiperparámetros por familia, precios on-demand us-west-2 (API de precios de AWS, 18-nov-2026).
Salida: reports/modelo_a/estimacion/estimacion.csv
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[2]
E = RAIZ / "reports/modelo_a/estimacion"
c = json.loads((E / "stac_conteo.json").read_text())
L = json.loads((E / "bench_lectura.json").read_text())
B = json.loads((E / "bench_computo.json").read_text())

S2 = {int(y): sum(v.values()) / 2 for y, v in c["sentinel-2-l2a"].items()}  # 2 gránulos (18NVK, 18NWK) por fecha
LS = {int(y): sum(v.values()) for y, v in c["landsat-c2-l2"].items()}
F_S2, F_LS = 0.28, 0.30
n_s2, n_ls = sum(S2.values()), sum(LS.values())
fechas = n_s2 + n_ls

# pares (origen despejado, objetivo despejado a ≤ 30 días) por pixel y año
pares = {}
for y in range(1984, 2027):
    n = S2.get(y, 0) * F_S2 + LS.get(y, 0) * F_LS
    pares[y] = n * (n / (365 if y < 2026 else 322)) * 30
pares_px = sum(pares.values())

# bytes por escena: archivo completo y ventana (bloques COG tocados, máximo 2×2 bloques)
s2_b = ["B04", "B08", "B8A", "B11", "SCL"]
ls_b = ["red", "nir08", "swir16", "qa_pixel"]


def bytes_escena(pref, bandas, lado_m):
    tot = 0.0
    for b in bandas:
        i = L[f"{pref}_{b}"]
        if lado_m is None:
            tot += i["bytes_archivo"]
            continue
        bloq_px = i["block"][0] * i["res"]
        n_bloq = (np.ceil(i["shape"][0] / i["block"][0]) * np.ceil(i["shape"][1] / i["block"][1]))
        toc = (np.ceil(lado_m / bloq_px) + 1) ** 2
        tot += i["bytes_archivo"] * min(toc, n_bloq) / n_bloq
    return tot


AREAS = {"lote (~1 ha)": (125, 100), "1 km²": (10_000, 1000), "ventana 5×5 km": (250_000, 5000),
         "imagen completa (tesela S2 110×110 km)": (10980 ** 2, None)}
NS_PIX = B["prepro_ns_por_pixfecha_numpy"] + B["interp_ns_por_pixfecha_numpy"] + B["ejemplos_ns_por_pixfecha"]
NS_PIX_PD = B["prepro_ns_por_pixfecha_pandas"] + B["ejemplos_ns_por_pixfecha"]
filas = []
for nom, (px, lado) in AREAS.items():
    gb = (n_s2 * bytes_escena("S2B", s2_b, lado) + n_ls * bytes_escena("LC0", ls_b, lado)) / 1e9
    pf = px * fechas
    filas.append({"area": nom, "pixeles_10m": px, "pixel_fecha": pf, "GB_a_leer": gb,
                  "horas_leer_local_2.5MBs": gb * 1e3 / 2.5 / 3600, "horas_leer_EC2_50MBs": gb * 1e3 / 50 / 3600,
                  "nucleo_h_prepro_numpy": pf * NS_PIX * 1e-9 / 3600, "nucleo_h_prepro_pandas": pf * NS_PIX_PD * 1e-9 / 3600,
                  "GB_tabla_larga_pandas(50 B/fila)": pf * 50 / 1e9, "ejemplos_todos_los_pixeles": px * pares_px})
A = pd.DataFrame(filas)

# entrenamiento: núcleo-hora por ajuste a N ejemplos (medido a 1e5/1e6 y extrapolado)
N0 = 1e6
fit = {  # (segundos de pared a N0, núcleos usados, exponente de escala con N)
    "XGBoost": (B["fit_s|XGBoost(300,d4)|1000000"], 16, 1.0),
    "Random Forest": (B["fit_s|RandomForest(300,leaf5)|100000"] * 10 * 1.2, 16, 1.1),
    "Gradient Boosting (sklearn)": (B["fit_s|GradientBoosting(200,d3)|100000"] * 10 * 1.2, 1, 1.1),
    "GRU pequeña": (B["fit_s|GRU(32,5ep)|100000"] * 10, 16, 1.0)}
CONFIGS = {"XGBoost": 8, "Random Forest": 6, "Gradient Boosting (sklearn)": 6, "GRU pequeña": 4}
K = 5  # particiones de la ventana deslizante
ARIMAX_SERIES = 5000  # ARIMAX por pixel sobre una muestra de pixeles; 2 configuraciones × K
PRUEBA_ANIOS = 35 + 200  # 7 historias acumuladas × 5 + ranking de 40 años × 5 (con XGBoost)
filas = []
for N in (1e5, 1e6, 1e7):
    tot = 0.0
    det = {}
    for m, (s, nuc, e) in fit.items():
        nh = CONFIGS[m] * K * s * (N / N0) ** e * nuc / 3600
        det[m] = nh
        tot += nh
    det["ARIMAX (5 000 pixeles)"] = ARIMAX_SERIES * 2 * K * B["fit_s|ARIMAX(1) por serie 600 pasos"] / 3600
    det["prueba de años (XGBoost)"] = PRUEBA_ANIOS * fit["XGBoost"][0] * (N / N0) * 16 / 3600
    tot += det["ARIMAX (5 000 pixeles)"] + det["prueba de años (XGBoost)"]
    tot *= 1.10  # reentrenar el ganador + predecir la prueba + bootstrap
    filas.append({"N_ejemplos": N, **{f"nucleo_h|{k}": v for k, v in det.items()}, "nucleo_h_total": tot,
                  "horas_pared_16_nucleos": tot / 16})
T = pd.DataFrame(filas)
PRECIO = {"c7i.4xlarge (16 vCPU, 32 GB)": 0.714, "c7i.8xlarge (32 vCPU, 64 GB)": 1.428}
VCPU_H_EC2 = 0.714 / 16
VCPU_H_EMRS = 0.052624 + 4 * 0.0057785  # EMR Serverless: vCPU-h + 4 GB-h por vCPU
for nom, p in PRECIO.items():
    v = int(nom.split("(")[1].split()[0])
    T[f"US$ {nom}"] = T.nucleo_h_total / v * p
T["US$ EMR Serverless (1 vCPU + 4 GB)"] = T.nucleo_h_total * VCPU_H_EMRS
A.to_csv(E / "estimacion_areas.csv", index=False)
T.to_csv(E / "estimacion_entrenamiento.csv", index=False)
pd.DataFrame([{"anio": y, "fechas_S2": S2.get(y, 0), "fechas_Landsat": LS.get(y, 0), "pares_por_pixel": p}
              for y, p in pares.items()]).to_csv(E / "pares_por_anio.csv", index=False)
pd.set_option("display.width", 250)
print(f"fechas S2 {n_s2:.0f} · Landsat {n_ls:.0f} · pares por pixel {pares_px:.0f} "
      f"(antes de 2013: {sum(v for y, v in pares.items() if y < 2013):.0f})")
print(A.round(2).to_string(index=False))
print(T.round(1).T.to_string())
print(f"US$ por vCPU-hora: EC2 c7i {VCPU_H_EC2:.4f} · EMR Serverless {VCPU_H_EMRS:.4f}")
