# -*- coding: utf-8 -*-
"""
SIRA (Iteración B — B_SIRA_ARCH) · Microservicio API Dual (Modelo B con Riego + Modelo C sin Riego)
====================================================================================================
Alineado a `GomSal/SIRA@modelo-humedad` (HEAD 8fde03d, 21-nov-2026):
Expone dos modos operativos complementarios sobre la Capa Oro sin re-ejecutar el Modelo A:

1. `POST /regar_hoy` (`simular_regar_hoy` — Modo Modelo B con registro de riego):
   - Lee `oro/pronostico_predictor` (NDVI/NDMI h=1..30d), `oro/geos_s2s_corregido` (5 miembros)
     y `plata/estado_hidrico_pixel`, propaga el balance recursivo (7 exógenas oficiales de
     ARIMAX(1) + reservorios `S_tau45_a05` y `S_tau60_a05` del Reentreno B Etapa 1) y devuelve
     la comparación instantánea `sin_riego` vs `con_riego` en <50 ms.

2. `GET /semaforo_c` (`consultar_semaforo_c` — Modo Modelo C operativo sin riego registrado):
   - Consulta los **20.286 píxeles reales** de la zona 2x2 km (400 ha alrededor de Nataima) en
     `oro/semaforo_c_pixeles` y el resumen zonal en `oro/semaforo_c_resumen`, devolviendo clases
     `suficiente` (0) / `media` (1) / `baja` (2), `dr0_mm`, `dias_hasta_baja`, déficit en L/m² y
     m³, y la bandera morada `posible_enfermedad_o_deficiencia_nutricional`.
"""

import json
import math
import os
from datetime import datetime, timezone
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
MODELS_DIR = os.path.join(DATA_DIR, "models")


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def load_b_runtime_artifacts():
    with open(os.path.join(MODELS_DIR, "semaforo_b", "arimax1_params.json"), "r", encoding="utf-8") as f:
        arimax = json.load(f)
    with open(os.path.join(MODELS_DIR, "pronosticador_a", "meta.json"), "r", encoding="utf-8") as f:
        meta_a = json.load(f)
    df_estado = pd.read_parquet(os.path.join(DATA_DIR, "plata", "estado_hidrico_pixel", "estado_hidrico_pixel.parquet"))
    df_pred_a = pd.read_parquet(os.path.join(DATA_DIR, "oro", "pronostico_predictor", "pronostico_predictor_latest.parquet"))
    df_geos = pd.read_parquet(os.path.join(DATA_DIR, "oro", "geos_s2s_corregido", "geos_s2s_corregido_latest.parquet"))
    return arimax, meta_a, df_estado, df_pred_a, df_geos


def _simular_trayectoria(arimax: dict, estado_row: pd.Series, df_a_px: pd.DataFrame, df_geos_em: pd.DataFrame, lamina_riego_mm: float, dia_aplicacion: int = 1):
    mu_x = np.array(arimax["mu_x"], dtype=float)
    sd_x = np.array(arimax["sd_x"], dtype=float)
    beta = np.array(arimax["beta"], dtype=float)
    k0 = float(arimax["k0"])
    sd_err = float(arimax["sd_err"])
    prior = np.array(arimax["prior"], dtype=float)

    taw, p_ag, dsat = 6.7, 0.65, 26.475
    raw = p_ag * taw
    f20, f5 = math.exp(-1.0 / 20.0), math.exp(-1.0 / 5.0)
    f45, f60 = math.exp(-1.0 / 45.0), math.exp(-1.0 / 60.0)

    df_a_px = df_a_px.sort_values("horizonte")
    miembros = sorted(df_geos_em["miembro"].unique())
    por_horizonte = {int(h): [] for h in df_a_px["horizonte"]}

    for mb in miembros:
        g_mb = df_geos_em[df_geos_em["miembro"] == mb].set_index("plazo")
        s20 = float(estado_row["s_tau20_init"])
        s5 = float(estado_row["s_tau5_init"])
        dr = float(estado_row["dr_k05_init"])
        s45 = float(estado_row.get("s_tau45_a05_init", 12.0))
        s60 = float(estado_row.get("s_tau60_a05_init", 15.0))
        ddr = int(estado_row["dias_desde_riego_init"])
        eto_hist = [float(estado_row["eto_4d_prev_sum"]) / 4.0] * 4
        riego_hist = [float(estado_row["riego_4d_prev_sum"]) / 4.0] * 4

        for _, ra in df_a_px.iterrows():
            h = int(ra["horizonte"])
            nd = float(ra["ndvi_pred"])
            kc = min(1.3, max(0.6, 1.0 * nd + 0.45))
            ll = float(g_mb.loc[h, "lluvia_corr_mm"]) if h in g_mb.index else 0.0
            et = float(g_mb.loc[h, "eto_corr_mm"]) if h in g_mb.index else 4.2
            rg = float(lamina_riego_mm) if h == dia_aplicacion else 0.0

            sale = kc * et
            s20 = s20 * f20 + (ll + rg) - sale
            s5 = s5 * f5 + (ll + rg) - sale
            s45 = s45 * f45 + 0.5 * ll + rg - sale
            s60 = s60 * f60 + 0.5 * ll + rg - sale

            dr -= ll + rg
            ks = 1.0 if dr <= raw else max(0.0, (taw - dr) / ((1.0 - p_ag) * taw))
            dr += ks * sale
            if dr < 0.0:
                dr *= 0.5
            dr = min(taw, max(-dsat, dr))

            ddr = 0 if rg > 0.0 else min(ddr + 1, 60)
            riego_hist.append(rg)
            eto_hist.append(et)
            r5 = sum(riego_hist[-5:])
            e5 = sum(eto_hist[-5:])

            x_vec = np.array([s20, s5, dr, r5, float(ddr), e5, nd], dtype=float)
            z_vec = (x_vec - mu_x) / sd_x
            mu_vwc = float(np.dot(z_vec, beta) + k0)
            p_r = _norm_cdf((27.1 - mu_vwc) / sd_err)
            p_a = _norm_cdf((30.0 - mu_vwc) / sd_err) - p_r
            p_v = max(1e-6, 1.0 - p_r - p_a)
            probs = np.array([p_v, max(1e-6, p_a), max(1e-6, p_r)]) / prior
            probs = probs / probs.sum()
            por_horizonte[h].append((mu_vwc, float(probs[0]), float(probs[1]), float(probs[2]), s45, s60))

    resumen_dias, primer_amarillo, primer_rojo = [], None, None
    for h in sorted(por_horizonte.keys()):
        vals = por_horizonte[h]
        vwc_mean = float(np.mean([v[0] for v in vals]))
        pv, pa, pr = float(np.mean([v[1] for v in vals])), float(np.mean([v[2] for v in vals])), float(np.mean([v[3] for v in vals]))
        pr_min, pr_max = float(np.min([v[3] for v in vals])), float(np.max([v[3] for v in vals]))
        clase = ["VERDE", "AMARILLO", "ROJO"][int(np.argmax([pv, pa, pr]))]
        if clase in ("AMARILLO", "ROJO") and primer_amarillo is None:
            primer_amarillo = h
        if clase == "ROJO" and primer_rojo is None:
            primer_rojo = h
        resumen_dias.append(
            {
                "horizonte_dia": h,
                "vwc_esperada_pct": round(vwc_mean, 2),
                "p_verde": round(pv, 3),
                "p_amarillo": round(pa, 3),
                "p_rojo": round(pr, 3),
                "p_rojo_rango_5m": [round(pr_min, 3), round(pr_max, 3)],
                "s_tau45_a05_medio": round(float(np.mean([v[4] for v in vals])), 2),
                "clase_semaforo": clase,
            }
        )
    return resumen_dias, primer_amarillo, primer_rojo


def simular_regar_hoy(payload: dict) -> dict:
    """Modo Modelo B (finca con riego registrado): simula escenario sin riego vs riego X mm."""
    arimax, meta_a, df_estado, df_pred_a, df_geos = load_b_runtime_artifacts()
    pixel_id = payload.get("pixel_id") or str(df_estado["pixel_id"].iloc[0])
    lamina_mm = float(payload.get("lamina_riego_mm", 30.0))
    dia_ap = int(payload.get("dia_aplicacion", 1))

    est_match = df_estado[df_estado["pixel_id"] == pixel_id]
    if est_match.empty:
        est_match = df_estado.iloc[[0]]
        pixel_id = str(est_match["pixel_id"].iloc[0])
    estado_row = est_match.iloc[0]

    df_a_px = df_pred_a[df_pred_a["pixel_id"] == pixel_id].copy()
    ultima_em = str(df_geos["emision"].max())
    df_geos_em = df_geos[df_geos["emision"] == ultima_em].copy()

    dias_sin, am_sin, roj_sin = _simular_trayectoria(arimax, estado_row, df_a_px, df_geos_em, 0.0, dia_ap)
    dias_con, am_con, roj_con = _simular_trayectoria(arimax, estado_row, df_a_px, df_geos_em, lamina_mm, dia_ap)

    return {
        "modo": "MODELO_B_CON_RIEGO_REGISTRADO",
        "arquitectura": "B_SIRA_ARCH (GomSal/SIRA@modelo-humedad · HEAD 8fde03d)",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "predio_id": str(estado_row["predio_id"]),
        "pixel_id": pixel_id,
        "emision_geos_s2s": ultima_em,
        "miembros_geos_s2s": int(df_geos_em["miembro"].nunique()),
        "modelos_activos": {
            "modelo_a": f"{meta_a['elegidos']['ndvi']['familia']} (NDVI/NDMI, 20 features, sin clima/Prithvi)",
            "modelo_b_oficial": "ARIMAX(1) sin Prithvi (AUC 0.75, 12/17 rojos)",
            "reentreno_b_etapa1_tracking": "S_tau45_a05 (AUC val 0.786) y S_tau60_a05 (AUC val 0.790)",
            "re_ejecuto_modelo_a": False,
        },
        "escenario_sin_riego": {"dias_hasta_amarillo": am_sin, "dias_hasta_rojo": roj_sin, "primeros_5_dias": dias_sin[:5]},
        "escenario_con_riego": {"lamina_riego_mm": lamina_mm, "dias_hasta_amarillo": am_con, "dias_hasta_rojo": roj_con, "primeros_5_dias": dias_con[:5]},
    }


def consultar_semaforo_c(payload: dict = None) -> dict:
    """Modo Modelo C (operación sin riego registrado · commit 1a592aa): consulta 20.286 píxeles y resumen zonal."""
    payload = payload or {}
    with open(os.path.join(DATA_DIR, "oro", "semaforo_c_mapas", "resumen.json"), "r", encoding="utf-8") as f:
        resumen = json.load(f)
    df_c = pd.read_parquet(os.path.join(DATA_DIR, "oro", "semaforo_c_pixeles", "semaforo_c_pixeles_latest.parquet"))
    px_req = payload.get("pixel")
    if px_req is not None and (df_c["pixel"] == int(px_req)).any():
        r = df_c[df_c["pixel"] == int(px_req)].iloc[0]
    else:
        r = df_c[df_c["clase_h0"] >= 0].iloc[0]
    clase_lbl = {0: "suficiente", 1: "media", 2: "baja", -1: "fuera_de_cultivo"}
    return {
        "modo": "MODELO_C_OPERATIVO_SIN_RIEGO",
        "commit_origen": "1a592aa (scripts/operacion/semaforo_c.py)",
        "hoy": resumen["hoy"],
        "geos_inicio": resumen["geos_inicio"],
        "zona_ha": resumen["zona_ha"],
        "pixeles_totales_en_oro": int(len(df_c)),
        "pixel_consultado": {
            "pixel": int(r["pixel"]),
            "dr0_mm": float(r["dr0_mm"]),
            "dias_hasta_baja_sin_riego": int(r["dias_hasta_baja"]),
            "h0": {"clase": clase_lbl[int(r["clase_h0"])], "dr_mm": float(r["dr_mm_h0"]), "p_baja": float(r["p_baja_h0"]), "ndvi": float(r["ndvi_h0"]), "ndmi": float(r["ndmi_h0"]), "marca_fitosanitaria": bool(r["marca_h0"])},
            "h7": {"clase": clase_lbl[int(r["clase_h7"])], "dr_mm": float(r["dr_mm_h7"]), "p_baja": float(r["p_baja_h7"]), "marca_fitosanitaria": bool(r["marca_h7"])},
            "h15": {"clase": clase_lbl[int(r["clase_h15"])], "dr_mm": float(r["dr_mm_h15"]), "p_baja": float(r["p_baja_h15"]), "marca_fitosanitaria": bool(r["marca_h15"])},
            "h30": {"clase": clase_lbl[int(r["clase_h30"])], "dr_mm": float(r["dr_mm_h30"]), "p_baja": float(r["p_baja_h30"]), "marca_fitosanitaria": bool(r["marca_h30"])},
        },
        "dias_hasta_baja_sin_riego_pct_zona": resumen["dias_hasta_baja_sin_riego_pct"],
        "visor_html_s3": "s3://sira-b-lakehouse-929570730700/oro/semaforo_c_mapas/mapa.html",
    }


def lambda_handler(event, context=None):
    body = event.get("body", event)
    if isinstance(body, str):
        body = json.loads(body)
    body = body or {}
    ruta = str(event.get("rawPath") or body.get("modo") or "/regar_hoy")
    res = consultar_semaforo_c(body) if "semaforo_c" in ruta else simular_regar_hoy(body)
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps(res, ensure_ascii=False)}


if __name__ == "__main__":
    print("=== PRUEBA 1: MODELO B (POST /regar_hoy) ===")
    print(json.dumps(simular_regar_hoy({"lamina_riego_mm": 30.0}), indent=2, ensure_ascii=False))
    print("\n=== PRUEBA 2: MODELO C (GET /semaforo_c) ===")
    print(json.dumps(consultar_semaforo_c(), indent=2, ensure_ascii=False))
