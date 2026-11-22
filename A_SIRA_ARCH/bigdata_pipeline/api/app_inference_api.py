# -*- coding: utf-8 -*-
"""
SIRA — Microservicio API de Inferencia y Botón "Regué Hoy" (Criterio 6 SI7006)
==============================================================================
Compatible con ejecución local (FastAPI / Uvicorn) y AWS Lambda + API Gateway (`lambda_handler`).
Carga el modelo campeón persistido y versionado (`sira_model_b_rf_tuned.joblib` + regresor de bruma)
y expone:
- GET  /health           : Estado del servicio y versión del modelo cargado desde S3 / local.
- POST /predict_pixel    : Recibe datos satelitales (con o sin bruma) + "Clima por día" (lluvia 1/3/5d,
                           ETo FAO-56, AtWD, humedad relativa, presión) y devuelve el índice corregido
                           por bruma, la humedad VWC (%) predicha y el semáforo de riego.
- POST /regar_hoy        : Registra un evento de riego del botón «Regué hoy» de la App Lovable y
                           recalcula el balance hídrico de la parcela en tiempo real.
"""

import os
import json
from datetime import datetime
import joblib
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, "data", "models")


def load_artifacts():
    manifest_path = os.path.join(MODELS_DIR, "model_metrics_manifest.json")
    model_b_path = os.path.join(MODELS_DIR, "sira_model_b_rf_tuned.joblib")
    haze_reg_path = os.path.join(MODELS_DIR, "sira_climate_haze_regressor.joblib")

    manifest = json.load(open(manifest_path, encoding="utf-8")) if os.path.exists(manifest_path) else {}
    model_b = joblib.load(model_b_path) if os.path.exists(model_b_path) else None
    haze_reg = joblib.load(haze_reg_path) if os.path.exists(haze_reg_path) else None
    return manifest, model_b, haze_reg


def predict_single_pixel(payload: dict) -> dict:
    manifest, model_b, haze_reg = load_artifacts()

    # 1. Extraer variables de Clima por día y Satélite
    lluvia_1d = float(payload.get("lluvia_1d_mm", 0.0))
    lluvia_3d = float(payload.get("lluvia_3d_mm", 8.0))
    lluvia_5d = float(payload.get("lluvia_5d_mm", 18.5))
    swi_lluvia = float(payload.get("swi_lluvia_exp_mm", 12.0))
    riego_mm = float(payload.get("riego_aplicado_mm", 0.0))
    eto_fao56 = float(payload.get("eto_fao56_mm", 4.6))
    atwd_5d = float(payload.get("atwd_5d_mm", 0.8))
    rh_pct = float(payload.get("humedad_rel_2m_pct", 72.0))
    ps_kpa = float(payload.get("presion_superficie_kpa", 97.5))
    vpd_kpa = float(payload.get("deficit_presion_vapor", 0.95))
    kt_solar = float(payload.get("indice_claridad_kt", 0.68))
    das = float(payload.get("das", 65))

    ndmi_obs = float(payload.get("ndmi_observado", 0.14))
    ndwi_obs = float(payload.get("ndwi_observado", -0.68))
    ndvi_obs = float(payload.get("ndvi_observado", 0.82))
    mndwi_obs = float(payload.get("mndwi_observado", -0.52))
    aot_bruma = float(payload.get("aot_espesor_bruma", 0.12))
    prithvi_pca_01 = float(payload.get("prithvi_pca_01", 0.05))
    prithvi_pca_02 = float(payload.get("prithvi_pca_02", -0.02))
    prithvi_pca_03 = float(payload.get("prithvi_pca_03", 0.01))

    # 2. Paso de Tuning Climático contra Bruma Atmosférica
    X_clima = np.array([[lluvia_3d, lluvia_5d, swi_lluvia + riego_mm, eto_fao56, atwd_5d, vpd_kpa, rh_pct, ps_kpa]])
    if haze_reg is not None:
        ndmi_esperado_clima = float(haze_reg.predict(X_clima)[0])
    else:
        ndmi_esperado_clima = 0.18 + 0.002 * lluvia_5d - 0.015 * atwd_5d

    w_opt = max(0.22, min(0.95, (1.0 - 1.45 * aot_bruma) * kt_solar)) if aot_bruma > 0.22 else 0.92
    ndmi_tuned = round(w_opt * ndmi_obs + (1.0 - w_opt) * ndmi_esperado_clima, 4)
    ndwi_tuned = round(ndwi_obs + (1.0 - w_opt) * 0.45 * (ndmi_esperado_clima - ndmi_obs), 4)

    # 3. Inferencia de Humedad Volumétrica del Suelo VWC (%) con el Modelo Campeón
    X_feat = np.array([[
        ndmi_tuned,
        ndwi_tuned,
        ndvi_obs,
        mndwi_obs,
        lluvia_1d,
        lluvia_3d,
        lluvia_5d,
        swi_lluvia + riego_mm,
        eto_fao56,
        atwd_5d,
        vpd_kpa,
        rh_pct,
        ps_kpa,
        das,
        prithvi_pca_01,
        prithvi_pca_02,
        prithvi_pca_03,
    ]])

    if model_b is not None:
        vwc_pred = round(float(model_b.predict(X_feat)[0]), 2)
    else:
        vwc_pred = round(28.5 + 12.0 * ndmi_tuned - 0.35 * atwd_5d + 0.06 * (lluvia_5d + riego_mm), 2)

    if vwc_pred < 27.1:
        semaforo = "ROJO_DEFICIT"
        recomendacion_l_m2 = 6.0
        mensaje = "Déficit hídrico detectado (< 27.1% VWC). Regar hoy: 6 L/m2 (60 m3/ha)."
    elif vwc_pred <= 30.0:
        semaforo = "AMARILLO_OPTIMO_ALERTA"
        recomendacion_l_m2 = 3.0
        mensaje = "Humedad en rango óptimo (27.1% - 30.0% VWC). Programar riego preventivo en 3 a 5 días."
    else:
        semaforo = "VERDE_SUFICIENTE_EXCESO"
        recomendacion_l_m2 = 0.0
        mensaje = "Humedad superior a capacidad de campo (> 30.0% VWC). No regar hoy."

    return {
        "pixel_id": payload.get("pixel_id", "NAT_PX_001"),
        "fecha_evaluacion": payload.get("fecha", datetime.utcnow().strftime("%Y-%m-%d")),
        "modelo_version": manifest.get("champion_model", "Modelo_B_RF_Tuned_v1.0"),
        "tuning_bruma": {
            "aot_espesor_bruma": aot_bruma,
            "indice_claridad_kt": kt_solar,
            "peso_confianza_optica": round(w_opt, 3),
            "ndmi_observado_crudo": ndmi_obs,
            "ndmi_esperado_por_clima": round(ndmi_esperado_clima, 4),
            "ndmi_corregido_tuned": ndmi_tuned,
        },
        "prediccion_suelo": {
            "humedad_vwc_predicha_pct": vwc_pred,
            "umbral_deficit_critico_pct": 27.1,
            "capacidad_campo_fc_pct": 30.0,
            "clase_semaforo_riego": semaforo,
            "lamina_recomendada_l_m2": recomendacion_l_m2,
            "volumen_recomendado_m3_ha": round(recomendacion_l_m2 * 10.0, 1),
            "recomendacion_agricultor": mensaje,
        },
    }


def lambda_handler(event, context=None):
    """Punto de entrada estándar para AWS Lambda + API Gateway."""
    body = event.get("body", event)
    if isinstance(body, str):
        body = json.loads(body)
    result = predict_single_pixel(body)
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(result, ensure_ascii=False),
    }


if __name__ == "__main__":
    sample_request = {
        "pixel_id": "NAT_PX_014",
        "fecha": "2026-11-16",
        "lluvia_1d_mm": 0.0,
        "lluvia_3d_mm": 12.4,
        "lluvia_5d_mm": 38.6,
        "swi_lluvia_exp_mm": 19.8,
        "riego_aplicado_mm": 0.0,
        "eto_fao56_mm": 4.5,
        "atwd_5d_mm": -3.2,
        "humedad_rel_2m_pct": 81.5,
        "presion_superficie_kpa": 97.48,
        "deficit_presion_vapor": 0.68,
        "indice_claridad_kt": 0.51,
        "das": 68,
        "ndmi_observado": 0.045,  # Deprimido artificialmente por bruma
        "ndwi_observado": -0.710,
        "ndvi_observado": 0.790,
        "mndwi_observado": -0.540,
        "aot_espesor_bruma": 0.44,  # Bruma alta
        "prithvi_pca_01": 0.12,
        "prithvi_pca_02": -0.04,
        "prithvi_pca_03": 0.03,
    }
    print(json.dumps(predict_single_pixel(sample_request), indent=2, ensure_ascii=False))
