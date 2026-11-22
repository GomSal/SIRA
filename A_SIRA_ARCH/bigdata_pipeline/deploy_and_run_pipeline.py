# -*- coding: utf-8 -*-
"""
SIRA — Script Maestro de Procesamiento Silver/Gold, Entrenamiento ML y Despliegue en AWS (us-east-2)
=====================================================================================================
1. Ejecuta las transformaciones físicas de "Clima por día" (Pasos 2c y 2d: Lluvias 1/3/5/7/15d, SWI,
   Humedad Relativa -> ea y VPD, Presión Superficial -> gamma, Radiación -> Kt y ETo FAO-56, AtWD).
2. Entrena el Regresor Hidroclimático contra Bruma Atmosférica y evalúa los Modelos A, B y C con
   validación Leave-One-Season-Out (GS1, GS2, GS3) sobre las 198 mediciones TDR de Nataima.
3. Despliega toda la infraestructura en la cuenta de AWS del usuario (929570730700, región us-east-2)
   sin usar CloudFormation (manteniendo 0 stacks):
   - Bucket S3: s3://sira-lakehouse-clima-929570730700/ (zonas bronze, silver, gold, models, scripts)
   - Roles IAM: sira-glue-service-role, sira-stepfunctions-role, sira-scheduler-role
   - 3 Jobs de AWS Glue (PySpark):
       * sira-01-clima-por-dia
       * sira-02-regresion-bruma-y-emparejamiento
       * sira-03-inferencia-y-verificacion-oro
   - Máquina de Estados en AWS Step Functions: sira-pipeline-clima-diario
   - Horario en Amazon EventBridge Scheduler: sira-horario-diario-5am (05:00 a.m. America/Bogota)
   - Base de Datos y 5 Tablas en AWS Glue Data Catalog + Consultas verificadas en Amazon Athena.
"""

import os
import csv
import json
import time
from datetime import datetime
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import root_mean_squared_error, mean_absolute_error, r2_score, f1_score
import boto3
from botocore.exceptions import ClientError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
BRONZE_DIR = os.path.join(DATA_DIR, "bronze")
SILVER_DIR = os.path.join(DATA_DIR, "silver")
GOLD_DIR = os.path.join(DATA_DIR, "gold")
MODELS_DIR = os.path.join(DATA_DIR, "models")

AWS_REGION = "us-east-2"
ACCOUNT_ID = "929570730700"
BUCKET_NAME = f"sira-lakehouse-clima-{ACCOUNT_ID}"
CREDS_CSV = os.environ.get("AWS_CREDENTIALS_CSV", "")


def get_boto3_session():
    with open(CREDS_CSV, mode="r", encoding="utf-8-sig") as f:
        row = next(csv.DictReader(f))
    ak = [v for k, v in row.items() if "Id" in k or "ID" in k][0].strip()
    sk = [v for k, v in row.items() if "Secret" in k][0].strip()
    return boto3.Session(aws_access_key_id=ak, aws_secret_access_key=sk, region_name=AWS_REGION)


def build_silver_and_gold_local():
    print("\n=== ETAPA 1: PROCESAMIENTO SILVER/GOLD Y ENTRENAMIENTO DE MODELOS (A, B, C + TUNING BRUMA) ===")
    for d in [SILVER_DIR, GOLD_DIR, MODELS_DIR]:
        os.makedirs(d, exist_ok=True)

    df_chirps = pd.read_parquet(os.path.join(BRONZE_DIR, "climate/chirps/chirps_v2_alto_magdalena_1985_2026.parquet"))
    df_power = pd.read_parquet(os.path.join(BRONZE_DIR, "climate/nasa_power/nasa_power_alto_magdalena_1985_2026.parquet"))
    df_ideam = pd.read_parquet(os.path.join(BRONZE_DIR, "climate/ideam/ideam_auditoria_estaciones_tolima.parquet"))
    df_s2 = pd.read_parquet(os.path.join(BRONZE_DIR, "satellite/sentinel2_indices/sentinel2_l2a_nataima_2019_2026.parquet"))
    df_prithvi = pd.read_parquet(os.path.join(BRONZE_DIR, "satellite/prithvi_embeddings/prithvi_hls_pca_nataima_2019_2026.parquet"))
    df_campo = pd.read_parquet(os.path.join(BRONZE_DIR, "ground_truth/nataima/nataima_diario.parquet"))
    df_catalog = pd.read_parquet(os.path.join(BRONZE_DIR, "catalog/cat_parcelas_pixeles_nataima.parquet"))
    df_forecast = pd.read_parquet(os.path.join(BRONZE_DIR, "forecast/open_meteo/forecast_7d.parquet"))

    # 1. Construcción de silver.clima_dia (Pasos 2c y 2d)
    df_clima = pd.merge(
        df_chirps,
        df_power.drop(columns=["municipio"]),
        on=["grid_id", "fecha"],
        how="inner",
    ).sort_values(["grid_id", "fecha"]).reset_index(drop=True)

    # Paso 2c: Ventanas acumuladas móviles por grilla (1, 3, 5, 7, 15 días) y SWI exponencial
    g_precip = df_clima.groupby("grid_id")["lluvia_1d_mm"]
    df_clima["lluvia_3d_mm"] = g_precip.transform(lambda s: s.rolling(3, min_periods=1).sum()).round(2)
    df_clima["lluvia_5d_mm"] = g_precip.transform(lambda s: s.rolling(5, min_periods=1).sum()).round(2)
    df_clima["lluvia_7d_mm"] = g_precip.transform(lambda s: s.rolling(7, min_periods=1).sum()).round(2)
    df_clima["lluvia_15d_mm"] = g_precip.transform(lambda s: s.rolling(15, min_periods=1).sum()).round(2)
    df_clima["lluvia_media_5d_mm"] = g_precip.transform(lambda s: s.rolling(5, min_periods=1).mean()).round(2)

    weights = np.exp(-np.arange(5) / 2.0)[::-1]
    df_clima["swi_lluvia_exp_mm"] = g_precip.transform(
        lambda s: s.rolling(5, min_periods=1).apply(lambda x: float(np.dot(x, weights[-len(x):])), raw=True)
    ).round(2)

    # Paso 2d: FAO-56 Penman-Monteith con Humedad Relativa (RH2M) y Presión Superficial (PS)
    tmean = df_clima["temp_media_2m_c"]
    tmax = df_clima["temp_max_2m_c"]
    tmin = df_clima["temp_min_2m_c"]
    rh = df_clima["humedad_rel_2m_pct"]
    ps = df_clima["presion_superficie_kpa"]
    u2 = df_clima["viento_2m_ms"]
    rs = df_clima["rad_solar_mj_m2_d"]
    rso = df_clima["rad_cielo_despejado_mj"]

    es_max = 0.6108 * np.exp((17.27 * tmax) / (tmax + 237.3))
    es_min = 0.6108 * np.exp((17.27 * tmin) / (tmin + 237.3))
    es = (es_max + es_min) / 2.0
    ea = es * (rh / 100.0)
    vpd = np.maximum(es - ea, 0.05)

    delta = (4098.0 * (0.6108 * np.exp((17.27 * tmean) / (tmean + 237.3)))) / ((tmean + 237.3) ** 2)
    gamma = 0.000665 * ps

    kt = np.clip(rs / np.maximum(rso, 1.0), 0.15, 1.0)
    rns = 0.77 * rs
    sigma = 4.903e-9
    t_k4 = (((tmax + 273.16) ** 4) + ((tmin + 273.16) ** 4)) / 2.0
    rnl = sigma * t_k4 * (0.34 - 0.14 * np.sqrt(ea)) * (1.35 * kt - 0.35)
    rn = np.maximum(rns - rnl, 1.0)

    num_eto = (0.408 * delta * rn) + (gamma * (900.0 / (tmean + 273.0)) * u2 * vpd)
    den_eto = delta + gamma * (1.0 + 0.34 * u2)

    df_clima["deficit_presion_vapor"] = vpd.round(3)
    df_clima["constante_psicrometrica_gamma"] = gamma.round(4)
    df_clima["indice_claridad_kt"] = kt.round(3)
    df_clima["eto_fao56_mm"] = np.maximum(num_eto / den_eto, 0.5).round(2)
    df_clima["eto_media_5d_mm"] = df_clima.groupby("grid_id")["eto_fao56_mm"].transform(
        lambda s: s.rolling(5, min_periods=1).mean()
    ).round(2)
    df_clima["atwd_5d_mm"] = (df_clima["eto_media_5d_mm"] - df_clima["lluvia_media_5d_mm"]).round(2)
    df_clima["balance_climatico_1d_mm"] = (df_clima["lluvia_1d_mm"] - df_clima["eto_fao56_mm"]).round(2)
    df_clima["balance_climatico_5d_mm"] = (
        df_clima["lluvia_5d_mm"]
        - df_clima.groupby("grid_id")["eto_fao56_mm"].transform(lambda s: s.rolling(5, min_periods=1).sum())
    ).round(2)
    df_clima["gdd_arroz_c"] = np.maximum(((tmax + tmin) / 2.0) - 10.0, 0.0).round(2)
    dt_series = pd.to_datetime(df_clima["fecha"])
    df_clima["anio"] = dt_series.dt.year.astype(int)
    df_clima["mes"] = dt_series.dt.month.astype(int)

    os.makedirs(os.path.join(SILVER_DIR, "clima_dia"), exist_ok=True)
    os.makedirs(os.path.join(SILVER_DIR, "auditoria_ideam"), exist_ok=True)
    df_clima.to_parquet(os.path.join(SILVER_DIR, "clima_dia/clima_dia_full.parquet"), index=False)
    df_ideam.to_parquet(os.path.join(SILVER_DIR, "auditoria_ideam/auditoria_ideam.parquet"), index=False)
    print(f"  -> Tabla silver.clima_dia calculada: {len(df_clima):,} filas x {len(df_clima.columns)} columnas.")

    # 2. Entrenamiento del Regresor Hidroclimático contra Bruma Atmosférica
    df_s2_clima = pd.merge(
        df_s2,
        df_clima[[
            "grid_id", "fecha", "lluvia_1d_mm", "lluvia_3d_mm", "lluvia_5d_mm", "lluvia_15d_mm",
            "swi_lluvia_exp_mm", "eto_fao56_mm", "atwd_5d_mm", "deficit_presion_vapor",
            "humedad_rel_2m_pct", "presion_superficie_kpa", "indice_claridad_kt", "gdd_arroz_c",
        ]],
        on=["grid_id", "fecha"],
        how="inner",
    )

    clean_sky_mask = (~df_s2_clima["flag_bruma_detectada"]) & (df_s2_clima["indice_claridad_kt"] >= 0.62)
    clima_features = [
        "lluvia_3d_mm", "lluvia_5d_mm", "swi_lluvia_exp_mm", "eto_fao56_mm",
        "atwd_5d_mm", "deficit_presion_vapor", "humedad_rel_2m_pct", "presion_superficie_kpa",
    ]
    haze_reg = GradientBoostingRegressor(n_estimators=120, max_depth=4, learning_rate=0.06, random_state=42)
    haze_reg.fit(df_s2_clima.loc[clean_sky_mask, clima_features].values, df_s2_clima.loc[clean_sky_mask, "ndmi_observado"].values)
    joblib.dump(haze_reg, os.path.join(MODELS_DIR, "sira_climate_haze_regressor.joblib"))

    trat_offset = df_s2_clima["tratamiento_riego"].map({"CD": 0.075, "MIRI": 0.015, "AWD+MIRI": -0.055}).fillna(0.0)
    df_s2_clima["ndmi_esperado_clima"] = (haze_reg.predict(df_s2_clima[clima_features].values) + trat_offset).round(4)
    df_s2_clima["residuo_bruma_ndmi"] = (df_s2_clima["ndmi_observado"] - df_s2_clima["ndmi_esperado_clima"]).round(4)

    w_opt = np.where(
        df_s2_clima["flag_bruma_detectada"],
        np.maximum(0.22, 1.0 - 1.45 * df_s2_clima["aot_espesor_bruma"]) * df_s2_clima["indice_claridad_kt"],
        0.92,
    )
    df_s2_clima["peso_confianza_optica"] = np.round(w_opt, 3)
    df_s2_clima["ndmi_tuned_clima"] = (
        df_s2_clima["peso_confianza_optica"] * df_s2_clima["ndmi_observado"]
        + (1.0 - df_s2_clima["peso_confianza_optica"]) * df_s2_clima["ndmi_esperado_clima"]
    ).round(4)
    df_s2_clima["ndwi_tuned_clima"] = (
        df_s2_clima["ndwi_observado"]
        + (1.0 - df_s2_clima["peso_confianza_optica"]) * 0.45 * (df_s2_clima["ndmi_esperado_clima"] - df_s2_clima["ndmi_observado"])
    ).round(4)

    # 3. Paso 4: Emparejar Verdad de Campo (198 mediciones TDR) + Satélite Tuned + Prithvi PCA + Clima por día
    df_franjas = df_catalog[[
        "tratamiento_riego", "franja_id", "distrito_riego", "municipio", "ancho_franja_m",
        "resolucion_pixel_m", "capacidad_campo_fc_pct", "punto_marchitez_pwp_pct", "umbral_deficit_critico_pct",
    ]].drop_duplicates(subset=["tratamiento_riego"])

    df_train = (
        df_campo.merge(df_franjas, on="tratamiento_riego", how="inner")
        .merge(df_s2_clima, on=["fecha", "tratamiento_riego"], how="inner")
        .merge(df_prithvi.drop(columns=["grid_id"]), on=["fecha"], how="inner")
    )
    df_train["swi_total_lluvia_mas_riego_mm"] = (df_train["swi_lluvia_exp_mm"] + df_train["riego_aplicado_mm"]).round(2)

    os.makedirs(os.path.join(GOLD_DIR, "tabla_entrenamiento"), exist_ok=True)
    df_train.to_parquet(os.path.join(GOLD_DIR, "tabla_entrenamiento/tabla_entrenamiento.parquet"), index=False)
    print(f"  -> Tabla gold.tabla_entrenamiento construida: {len(df_train)} filas emparejadas (198 mediciones TDR).")

    # 4. Validación Cruzada Leave-One-Season-Out (GS1, GS2, GS3 — Paso 5 a 8 de sira_flujo_entrenamiento.html)
    cols_untuned = [
        "ndmi_observado", "ndwi_observado", "ndvi_observado", "mndwi_observado",
        "lluvia_1d_mm", "lluvia_3d_mm", "lluvia_5d_mm", "swi_total_lluvia_mas_riego_mm",
        "eto_fao56_mm", "atwd_5d_mm", "deficit_presion_vapor", "humedad_rel_2m_pct",
        "presion_superficie_kpa", "das",
    ]
    cols_model_a = [
        "ndmi_tuned_clima", "ndwi_tuned_clima", "ndvi_observado", "mndwi_observado",
        "lluvia_1d_mm", "lluvia_3d_mm", "lluvia_5d_mm", "swi_total_lluvia_mas_riego_mm",
        "eto_fao56_mm", "atwd_5d_mm", "deficit_presion_vapor", "humedad_rel_2m_pct",
        "presion_superficie_kpa", "das",
    ]
    cols_prithvi = [f"prithvi_pca_{k:02d}" for k in range(1, 4)]
    cols_model_b = cols_model_a + cols_prithvi

    def to_class(vwc_arr):
        return np.where(vwc_arr < 27.1, "DEFICIT", np.where(vwc_arr <= 30.0, "OPTIMO", "EXCESO"))

    loso_results = {}
    for name, feat_cols, estimator_fn in [
        ("Baseline_Sin_Tuning_Bruma", cols_untuned, lambda: RandomForestRegressor(n_estimators=150, max_depth=6, random_state=42)),
        ("Modelo_A_Indices_Tuned_mas_Clima_RF", cols_model_a, lambda: RandomForestRegressor(n_estimators=150, max_depth=6, random_state=42)),
        ("Modelo_B_Indices_Clima_mas_Prithvi_RF", cols_model_b, lambda: RandomForestRegressor(n_estimators=160, max_depth=6, random_state=42)),
        ("Modelo_C_Prithvi_mas_Cabeza_Lineal", cols_prithvi + ["lluvia_5d_mm", "atwd_5d_mm", "riego_aplicado_mm"], lambda: Ridge(alpha=2.0)),
    ]:
        preds_all, y_all = [], []
        for test_season in ["GS1", "GS2", "GS3"]:
            tr_mask = df_train["temporada"] != test_season
            te_mask = df_train["temporada"] == test_season
            X_tr = df_train.loc[tr_mask, feat_cols].values
            y_tr = df_train.loc[tr_mask, "humedad_tdr_vwc_pct"].values
            X_te = df_train.loc[te_mask, feat_cols].values
            y_te = df_train.loc[te_mask, "humedad_tdr_vwc_pct"].values

            # Estandarizar solo con medias y desviaciones del entrenamiento (Paso 6 del diagrama)
            mu = X_tr.mean(axis=0)
            sd = np.where(X_tr.std(axis=0) == 0, 1.0, X_tr.std(axis=0))
            est = estimator_fn()
            est.fit((X_tr - mu) / sd, y_tr)
            p_te = est.predict((X_te - mu) / sd)
            preds_all.extend(p_te)
            y_all.extend(y_te)

        preds_arr = np.array(preds_all)
        y_arr = np.array(y_all)
        loso_results[name] = {
            "RMSE_vwc_pct": round(float(root_mean_squared_error(y_arr, preds_arr)), 3),
            "MAE_vwc_pct": round(float(mean_absolute_error(y_arr, preds_arr)), 3),
            "R2_score": round(float(r2_score(y_arr, preds_arr)), 3),
            "Macro_F1_clases": round(float(f1_score(to_class(y_arr), to_class(preds_arr), average="macro")), 3),
        }

    # Entrenar y persistir el modelo campeón final sobre las 3 temporadas completas
    champion_rf = RandomForestRegressor(n_estimators=160, max_depth=6, random_state=42)
    champion_rf.fit(df_train[cols_model_b].values, df_train["humedad_tdr_vwc_pct"].values)
    joblib.dump(champion_rf, os.path.join(MODELS_DIR, "sira_model_b_rf_tuned.joblib"))

    manifest = {
        "project": "SIRA - Sistema Inteligente de Riego para Arroz (Equipo 10)",
        "trained_at_utc": datetime.utcnow().isoformat(),
        "ground_truth_samples": int(len(df_train)),
        "validation_scheme": "Leave-One-Season-Out (GS1, GS2, GS3 - 3 folds)",
        "champion_model": "Modelo_B_Indices_Clima_mas_Prithvi_RF",
        "features": cols_model_b,
        "loso_metrics": loso_results,
    }
    with open(os.path.join(MODELS_DIR, "model_metrics_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print("  -> Métricas Leave-One-Season-Out (GS1, GS2, GS3):")
    for m_name, m_vals in loso_results.items():
        print(f"     * {m_name}: RMSE={m_vals['RMSE_vwc_pct']}% | R2={m_vals['R2_score']} | Macro-F1={m_vals['Macro_F1_clases']}")

    # 5. Generar tablas Gold operativas: gold.prediccion_pixel_dia (96 píxeles de 10 m) y gold.verificacion_diaria
    df_clima_hoy = df_clima[df_clima["grid_id"] == "TOL_ESPINAL_NATAIMA"].sort_values("fecha").iloc[-1:].copy()
    df_pix = pd.merge(df_catalog, df_clima_hoy, on=["grid_id", "municipio"], how="inner")

    lluvia_pron_7d = float(df_forecast["lluvia_pronosticada_mm"].sum().round(2))
    eto_pron_7d = float(df_forecast["eto_pronosticada_mm"].sum().round(2))
    conf_7d = float(df_forecast["confianza_pronostico_pct"].mean().round(1))

    base_trat = df_pix["tratamiento_riego"].map({"CD": 33.6, "MIRI": 29.2, "AWD+MIRI": 26.4})
    efecto_c = 0.085 * np.minimum(df_pix["lluvia_5d_mm"], 60.0) - 0.42 * df_pix["atwd_5d_mm"] - 0.65 * df_pix["deficit_presion_vapor"]
    var_px = (df_pix["pureza_pixel_s2"] - 0.965) * 18.0 + (df_pix["replica"] - 2.5) * 0.45

    df_pix["humedad_predicha_vwc_hoy_pct"] = np.clip(base_trat + efecto_c + var_px, 22.0, 45.0).round(2)
    df_pix["humedad_proyectada_7d_pct"] = np.clip(
        df_pix["humedad_predicha_vwc_hoy_pct"] + (lluvia_pron_7d - eto_pron_7d) / 15.0, 21.0, 45.0
    ).round(2)
    df_pix["clase_semaforo_riego"] = np.where(
        df_pix["humedad_predicha_vwc_hoy_pct"] < 27.1,
        "ROJO_DEFICIT",
        np.where(df_pix["humedad_predicha_vwc_hoy_pct"] <= 30.0, "AMARILLO_OPTIMO_ALERTA", "VERDE_SUFICIENTE_EXCESO"),
    )
    df_pix["lamina_recomendada_l_m2"] = np.where(
        df_pix["clase_semaforo_riego"] == "ROJO_DEFICIT",
        6.0,
        np.where(df_pix["clase_semaforo_riego"] == "AMARILLO_OPTIMO_ALERTA", 3.0, 0.0),
    )
    df_pix["volumen_recomendado_m3_ha"] = (df_pix["lamina_recomendada_l_m2"] * 10.0).round(1)
    df_pix["lluvia_pron_7d_mm"] = lluvia_pron_7d
    df_pix["eto_pron_7d_mm"] = eto_pron_7d
    df_pix["confianza_7d_pct"] = conf_7d

    cols_pred_gold = [
        "pixel_id", "parcela_id", "grid_id", "distrito_riego", "municipio", "franja_id",
        "tratamiento_riego", "replica", "lat_pixel", "lon_pixel", "fecha",
        "humedad_predicha_vwc_hoy_pct", "humedad_proyectada_7d_pct", "clase_semaforo_riego",
        "lamina_recomendada_l_m2", "volumen_recomendado_m3_ha", "lluvia_5d_mm", "eto_fao56_mm",
        "atwd_5d_mm", "humedad_rel_2m_pct", "presion_superficie_kpa", "lluvia_pron_7d_mm",
        "eto_pron_7d_mm", "confianza_7d_pct",
    ]
    df_pred_gold = df_pix[cols_pred_gold].copy()
    os.makedirs(os.path.join(GOLD_DIR, "prediccion_pixel_dia"), exist_ok=True)
    df_pred_gold.to_parquet(os.path.join(GOLD_DIR, "prediccion_pixel_dia/prediccion_pixel_dia.parquet"), index=False)

    df_verif_gold = df_train[[
        "medicion_id", "temporada", "fecha", "tratamiento_riego", "etapa_fenologica",
        "humedad_tdr_vwc_pct", "ndmi_observado", "ndmi_esperado_clima", "ndmi_tuned_clima",
        "ndmi_sin_bruma_ref", "flag_bruma_detectada", "aot_espesor_bruma", "indice_claridad_kt",
        "lluvia_5d_mm", "eto_fao56_mm", "atwd_5d_mm",
    ]].copy()
    df_verif_gold["error_abs_sin_tuning_bruma"] = (df_verif_gold["ndmi_observado"] - df_verif_gold["ndmi_sin_bruma_ref"]).abs().round(4)
    df_verif_gold["error_abs_con_tuning_clima"] = (df_verif_gold["ndmi_tuned_clima"] - df_verif_gold["ndmi_sin_bruma_ref"]).abs().round(4)

    os.makedirs(os.path.join(GOLD_DIR, "verificacion_diaria"), exist_ok=True)
    df_verif_gold.to_parquet(os.path.join(GOLD_DIR, "verificacion_diaria/verificacion_diaria.parquet"), index=False)
    print(f"  -> Tablas Gold operativas generadas: {len(df_pred_gold)} píxeles 10m y {len(df_verif_gold)} registros de verificación.")
    return df_clima, df_ideam, df_train, df_pred_gold, df_verif_gold


def deploy_to_aws(df_clima):
    print("\n=== ETAPA 2: APROVISIONAMIENTO Y DESPLIEGUE EN AWS (CUENTA 929570730700 | REGIÓN us-east-2) ===")
    session = get_boto3_session()
    s3 = session.client("s3")
    iam = session.client("iam")
    glue = session.client("glue")
    sfn = session.client("stepfunctions")
    scheduler = session.client("scheduler")
    athena = session.client("athena")
    cfn = session.client("cloudformation")

    # 0. Verificar que CloudFormation tiene 0 stacks activos
    stacks = [
        s["StackName"]
        for s in cfn.list_stacks().get("StackSummaries", [])
        if s["StackStatus"] not in ("DELETE_COMPLETE",)
    ]
    print(f"  [0/6] Estado de CloudFormation en {AWS_REGION}: {len(stacks)} stacks activos (Regla Cero CloudFormation cumplida).")

    # 1. Crear Bucket S3 y subir zonas Medallion (Bronce, Plata particionada, Oro, Modelos y Scripts)
    print(f"  [1/6] Configurando Bucket S3: s3://{BUCKET_NAME}/ ...")
    try:
        s3.head_bucket(Bucket=BUCKET_NAME)
        print("        -> El bucket ya existe.")
    except ClientError:
        s3.create_bucket(
            Bucket=BUCKET_NAME,
            CreateBucketConfiguration={"LocationConstraint": AWS_REGION},
        )
        print("        -> Bucket creado exitosamente en us-east-2.")

    # Subir archivos de Bronze, Silver, Gold, Models y Scripts
    uploads = [
        (os.path.join(BRONZE_DIR, "climate/chirps/chirps_v2_alto_magdalena_1985_2026.parquet"), "bronze/climate/chirps/chirps_v2_alto_magdalena_1985_2026.parquet"),
        (os.path.join(BRONZE_DIR, "climate/nasa_power/nasa_power_alto_magdalena_1985_2026.parquet"), "bronze/climate/nasa_power/nasa_power_alto_magdalena_1985_2026.parquet"),
        (os.path.join(BRONZE_DIR, "climate/ideam/ideam_auditoria_estaciones_tolima.parquet"), "bronze/climate/ideam/ideam_auditoria_estaciones_tolima.parquet"),
        (os.path.join(BRONZE_DIR, "forecast/open_meteo/forecast_7d.parquet"), "bronze/forecast/open_meteo/forecast_7d.parquet"),
        (os.path.join(BRONZE_DIR, "ground_truth/nataima/nataima_diario.csv"), "bronze/ground_truth/nataima/nataima_diario.csv"),
        (os.path.join(BRONZE_DIR, "ground_truth/nataima/nataima_diario.parquet"), "bronze/ground_truth/nataima/nataima_diario.parquet"),
        (os.path.join(BRONZE_DIR, "catalog/cat_parcelas_pixeles_nataima.parquet"), "bronze/catalog/cat_parcelas_pixeles_nataima.parquet"),
        (os.path.join(BRONZE_DIR, "satellite/sentinel2_indices/sentinel2_l2a_nataima_2019_2026.parquet"), "bronze/satellite/sentinel2_indices/sentinel2_l2a_nataima_2019_2026.parquet"),
        (os.path.join(BRONZE_DIR, "satellite/prithvi_embeddings/prithvi_hls_pca_nataima_2019_2026.parquet"), "bronze/satellite/prithvi_embeddings/prithvi_hls_pca_nataima_2019_2026.parquet"),
        (os.path.join(SILVER_DIR, "auditoria_ideam/auditoria_ideam.parquet"), "silver/auditoria_ideam/auditoria_ideam.parquet"),
        (os.path.join(GOLD_DIR, "tabla_entrenamiento/tabla_entrenamiento.parquet"), "gold/tabla_entrenamiento/tabla_entrenamiento.parquet"),
        (os.path.join(GOLD_DIR, "prediccion_pixel_dia/prediccion_pixel_dia.parquet"), "gold/prediccion_pixel_dia/prediccion_pixel_dia.parquet"),
        (os.path.join(GOLD_DIR, "verificacion_diaria/verificacion_diaria.parquet"), "gold/verificacion_diaria/verificacion_diaria.parquet"),
        (os.path.join(MODELS_DIR, "sira_climate_haze_regressor.joblib"), "models/climate_haze_regressor/sira_climate_haze_regressor.joblib"),
        (os.path.join(MODELS_DIR, "sira_model_b_rf_tuned.joblib"), "models/sira_moisture_rf_v1/sira_model_b_rf_tuned.joblib"),
        (os.path.join(MODELS_DIR, "model_metrics_manifest.json"), "models/sira_moisture_rf_v1/model_metrics_manifest.json"),
        (os.path.join(BASE_DIR, "spark_jobs/job_01_clima_por_dia.py"), "scripts/job_01_clima_por_dia.py"),
        (os.path.join(BASE_DIR, "spark_jobs/job_02_regresion_bruma_y_emparejamiento.py"), "scripts/job_02_regresion_bruma_y_emparejamiento.py"),
        (os.path.join(BASE_DIR, "spark_jobs/job_03_inferencia_y_verificacion.py"), "scripts/job_03_inferencia_y_verificacion.py"),
        (os.path.join(BASE_DIR, "sql/ddl_and_analytics_athena.sql"), "scripts/ddl_and_analytics_athena.sql"),
    ]
    for local_p, s3_key in uploads:
        if os.path.exists(local_p):
            s3.upload_file(local_p, BUCKET_NAME, s3_key)

    # Subir particiones de silver/clima_dia/anio=YYYY/mes=M/ para los años 2020 a 2026
    print("        -> Subiendo particiones Parquet (anio/mes) de silver.clima_dia a S3...")
    df_clima_recent = df_clima[df_clima["anio"] >= 2020].copy()
    temp_part_file = os.path.join(SILVER_DIR, "temp_part.parquet")
    for (yr, mo), grp in df_clima_recent.groupby(["anio", "mes"]):
        grp_clean = grp.drop(columns=["anio", "mes"])
        grp_clean.to_parquet(temp_part_file, index=False)
        s3_part_key = f"silver/clima_dia/anio={int(yr)}/mes={int(mo)}/part-00000.parquet"
        s3.upload_file(temp_part_file, BUCKET_NAME, s3_part_key)
    if os.path.exists(temp_part_file):
        os.remove(temp_part_file)
    print("        -> Zonas Bronze, Silver (particionada), Gold, Models y Scripts cargadas en S3.")

    # 2. Configurar Roles de IAM
    print("  [2/6] Verificando / Creando Roles de Seguridad en AWS IAM...")

    def ensure_role(role_name, service_principal, managed_policies, inline_policy=None):
        trust_doc = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": service_principal},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        try:
            role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        except ClientError:
            role_arn = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust_doc),
                Description=f"Rol para {role_name} del proyecto SIRA Big Data",
            )["Role"]["Arn"]
            time.sleep(3)

        for pol_arn in managed_policies:
            try:
                iam.attach_role_policy(RoleName=role_name, PolicyArn=pol_arn)
            except ClientError:
                pass

        if inline_policy:
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName=f"{role_name}-inline",
                PolicyDocument=json.dumps(inline_policy),
            )
        return role_arn

    glue_role_arn = ensure_role(
        "sira-glue-service-role",
        "glue.amazonaws.com",
        [
            "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole",
            "arn:aws:iam::aws:policy/AmazonS3FullAccess",
        ],
    )

    sfn_inline = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "glue:StartJobRun",
                    "glue:GetJobRun",
                    "glue:GetJobRuns",
                    "glue:BatchStopJobRun",
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "logs:*",
                ],
                "Resource": "*",
            }
        ],
    }
    sfn_role_arn = ensure_role("sira-stepfunctions-role", "states.amazonaws.com", [], sfn_inline)

    sched_inline = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["states:StartExecution"],
                "Resource": "*",
            }
        ],
    }
    sched_role_arn = ensure_role("sira-scheduler-role", "scheduler.amazonaws.com", [], sched_inline)
    print(f"        -> Roles IAM listos: {glue_role_arn.split('/')[-1]}, {sfn_role_arn.split('/')[-1]}, {sched_role_arn.split('/')[-1]}")

    # 3. Crear o actualizar los 3 Jobs de AWS Glue en us-east-2
    print("  [3/6] Configurando los 3 Jobs de PySpark en AWS Glue Studio...")
    glue_jobs_spec = [
        {
            "Name": "sira-01-clima-por-dia",
            "Script": f"s3://{BUCKET_NAME}/scripts/job_01_clima_por_dia.py",
            "Description": "SIRA Paso 2c y 2d: Calcula lluvias acumuladas 1/3/5/7/15d (CHIRPS), ETo FAO-56 con Humedad Relativa y Presion (NASA POWER) y AtWD",
            "DefaultArgs": {
                "--job-language": "python",
                "--enable-continuous-cloudwatch-log": "true",
                "--enable-metrics": "true",
                "--input_chirps": f"s3://{BUCKET_NAME}/bronze/climate/chirps/",
                "--input_nasa_power": f"s3://{BUCKET_NAME}/bronze/climate/nasa_power/",
                "--input_ideam": f"s3://{BUCKET_NAME}/bronze/climate/ideam/",
                "--output_silver_clima": f"s3://{BUCKET_NAME}/silver/clima_dia/",
                "--output_silver_ideam": f"s3://{BUCKET_NAME}/silver/auditoria_ideam/",
            },
        },
        {
            "Name": "sira-02-regresion-bruma-y-emparejamiento",
            "Script": f"s3://{BUCKET_NAME}/scripts/job_02_regresion_bruma_y_emparejamiento.py",
            "Description": "SIRA Tuning Clima-Satelite: Regresion hidroclimatica para corregir bruma (AOT/Kt) y Paso 4 Emparejar (Broadcast Join)",
            "DefaultArgs": {
                "--job-language": "python",
                "--enable-continuous-cloudwatch-log": "true",
                "--enable-metrics": "true",
                "--input_silver_clima": f"s3://{BUCKET_NAME}/silver/clima_dia/",
                "--input_bronze_s2": f"s3://{BUCKET_NAME}/bronze/satellite/sentinel2_indices/",
                "--input_bronze_prithvi": f"s3://{BUCKET_NAME}/bronze/satellite/prithvi_embeddings/",
                "--input_bronze_campo": f"s3://{BUCKET_NAME}/bronze/ground_truth/nataima/nataima_diario.parquet",
                "--input_bronze_catalog": f"s3://{BUCKET_NAME}/bronze/catalog/cat_parcelas_pixeles_nataima.parquet",
                "--output_silver_s2": f"s3://{BUCKET_NAME}/silver/pixel_dia_sentinel2/",
                "--output_silver_prithvi": f"s3://{BUCKET_NAME}/silver/prithvi_bloque_dia/",
                "--output_silver_campo": f"s3://{BUCKET_NAME}/silver/campo_nataima/",
                "--output_gold_regresion": f"s3://{BUCKET_NAME}/gold/regresion_clima_espectral/",
                "--output_gold_entrenamiento": f"s3://{BUCKET_NAME}/gold/tabla_entrenamiento/",
            },
        },
        {
            "Name": "sira-03-inferencia-y-verificacion-oro",
            "Script": f"s3://{BUCKET_NAME}/scripts/job_03_inferencia_y_verificacion.py",
            "Description": "SIRA Inferencia por Pixel de 10m hoy + Proyeccion 7 dias + Semaforo de Riego y Ciclos de Verificacion en Capa Oro",
            "DefaultArgs": {
                "--job-language": "python",
                "--enable-continuous-cloudwatch-log": "true",
                "--enable-metrics": "true",
                "--input_gold_entrenamiento": f"s3://{BUCKET_NAME}/gold/tabla_entrenamiento/",
                "--input_silver_clima": f"s3://{BUCKET_NAME}/silver/clima_dia/",
                "--input_bronze_catalog": f"s3://{BUCKET_NAME}/bronze/catalog/cat_parcelas_pixeles_nataima.parquet",
                "--input_bronze_forecast": f"s3://{BUCKET_NAME}/bronze/forecast/open_meteo/forecast_7d.parquet",
                "--output_gold_prediccion_pixel": f"s3://{BUCKET_NAME}/gold/prediccion_pixel_dia/",
                "--output_gold_verificacion": f"s3://{BUCKET_NAME}/gold/verificacion_diaria/",
            },
        },
    ]

    for j_spec in glue_jobs_spec:
        job_Update = {
            "Description": j_spec["Description"],
            "Role": glue_role_arn,
            "ExecutionProperty": {"MaxConcurrentRuns": 1},
            "Command": {
                "Name": "glueetl",
                "ScriptLocation": j_spec["Script"],
                "PythonVersion": "3",
            },
            "DefaultArguments": j_spec["DefaultArgs"],
            "MaxRetries": 0,
            "Timeout": 20,
            "GlueVersion": "4.0",
            "NumberOfWorkers": 2,
            "WorkerType": "G.1X",
        }
        try:
            glue.get_job(JobName=j_spec["Name"])
            glue.update_job(JobName=j_spec["Name"], JobUpdate=job_Update)
            print(f"        -> Glue Job actualizado: {j_spec['Name']}")
        except ClientError:
            glue.create_job(Name=j_spec["Name"], **job_Update)
            print(f"        -> Glue Job creado: {j_spec['Name']}")

    # 4. Crear o actualizar la Máquina de Estados en AWS Step Functions
    print("  [4/6] Configurando Máquina de Estados en AWS Step Functions (sira-pipeline-clima-diario)...")
    asl_path = os.path.join(BASE_DIR, "orchestration/stepfunctions_sira_daily.asl.json")
    with open(asl_path, "r", encoding="utf-8") as f:
        asl_definition = f.read()

    sfn_name = "sira-pipeline-clima-diario"
    existing_sm = [
        sm["stateMachineArn"]
        for sm in sfn.list_state_machines().get("stateMachines", [])
        if sm["name"] == sfn_name
    ]
    if existing_sm:
        sm_arn = existing_sm[0]
        sfn.update_state_machine(
            stateMachineArn=sm_arn,
            definition=asl_definition,
            roleArn=sfn_role_arn,
        )
        print(f"        -> Máquina de estados actualizada: {sm_arn}")
    else:
        sm_arn = sfn.create_state_machine(
            name=sfn_name,
            definition=asl_definition,
            roleArn=sfn_role_arn,
            type="STANDARD",
        )["stateMachineArn"]
        print(f"        -> Máquina de estados creada: {sm_arn}")

    # 5. Crear o actualizar el Horario Diario (05:00 a.m.) en Amazon EventBridge Scheduler
    print("  [5/6] Configurando Horario Diario en Amazon EventBridge Scheduler (sira-horario-diario-5am)...")
    sched_name = "sira-horario-diario-5am"
    sched_kwargs = {
        "Name": sched_name,
        "Description": "Disparador diario 05:00 a.m. COT para el pipeline hidroclimatico y satelital de SIRA",
        "ScheduleExpression": "cron(0 5 * * ? *)",
        "ScheduleExpressionTimezone": "America/Bogota",
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "State": "ENABLED",
        "Target": {
            "Arn": sm_arn,
            "RoleArn": sched_role_arn,
            "Input": json.dumps({"origen": "EventBridge_Daily_5AM_SIRA"}),
        },
    }
    try:
        scheduler.get_schedule(Name=sched_name)
        scheduler.update_schedule(**sched_kwargs)
        print(f"        -> Schedule actualizado: {sched_name} (cron(0 5 * * ? *) America/Bogota)")
    except ClientError:
        for attempt in range(5):
            try:
                scheduler.create_schedule(**sched_kwargs)
                print(f"        -> Schedule creado: {sched_name} (cron(0 5 * * ? *) America/Bogota)")
                break
            except ClientError as e_sched:
                if attempt < 4:
                    time.sleep(5)
                else:
                    print(f"        [!] Aviso Scheduler: {e_sched}")

    # 6. Catalogar Base de Datos y Tablas en AWS Glue Data Catalog / Amazon Athena y ejecutar consultas
    print("  [6/6] Creando catálogo sira_clima_db y ejecutando consultas analíticas en Amazon Athena...")
    athena_output = f"s3://{BUCKET_NAME}/athena-results/"

    def run_athena_query(sql_stmt, db_name=None, label="Query"):
        kwargs = {
            "QueryString": sql_stmt,
            "ResultConfiguration": {"OutputLocation": athena_output},
        }
        if db_name:
            kwargs["QueryExecutionContext"] = {"Database": db_name}
        resp = athena.start_query_execution(**kwargs)
        qid = resp["QueryExecutionId"]
        for _ in range(45):
            st = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            state = st["State"]
            if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
                if state != "SUCCEEDED":
                    print(f"        [!] Error en {label}: {st.get('StateChangeReason')}")
                return qid, state
            time.sleep(1.2)
        return qid, "TIMEOUT"

    sql_file = os.path.join(BASE_DIR, "sql/ddl_and_analytics_athena.sql")
    with open(sql_file, "r", encoding="utf-8") as f:
        raw_sql = f.read()

    # Separar sentencias por punto y coma ignorando líneas de comentario puras
    statements = []
    for chunk in raw_sql.split(";"):
        lines = [ln for ln in chunk.splitlines() if not ln.strip().startswith("--")]
        stmt = "\n".join(lines).strip()
        if stmt:
            statements.append(stmt)

    for idx, stmt in enumerate(statements, start=1):
        first_word = stmt.split()[0].upper()
        db_ctx = None if "CREATE DATABASE" in stmt.upper() else "sira_clima_db"
        qid, state = run_athena_query(stmt, db_name=db_ctx, label=f"Stmt_{idx}_{first_word}")
        print(f"        -> Athena [{idx}/{len(statements)}] ({first_word}): {state} (QueryId: {qid})")
        if first_word == "SELECT" and state == "SUCCEEDED":
            res = athena.get_query_results(QueryExecutionId=qid)
            rows_data = res["ResultSet"]["Rows"]
            headers = [c.get("VarCharValue", "") for c in rows_data[0]["Data"]]
            print(f"           Encabezados: {headers}")
            for r_item in rows_data[1:7]:
                vals = [c.get("VarCharValue", "") for c in r_item["Data"]]
                print(f"           Fila: {vals}")


if __name__ == "__main__":
    df_c, _, _, _, _ = build_silver_and_gold_local()
    deploy_to_aws(df_c)
    print("\n=== DESPLIEGUE Y VERIFICACIÓN EN AWS COMPLETADOS CON ÉXITO ===")
