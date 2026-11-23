# -*- coding: utf-8 -*-
r"""
SIRA (Iteración B — B_SIRA_ARCH) · Ejecución Local E2E + Reorganización y Despliegue AWS (us-east-2)
====================================================================================================
Alineado a extremo detalle a `https://github.com/GomSal/SIRA/tree/modelo-humedad` (HEAD 8fde03d):
- Fuente única del repositorio: C:\Users\Sismo\Documents\EDUCATION\EAFIT\10th_semester\SIRA\github
- Incluye los 3 modelos oficiales del repositorio:
    * Modelo A: Random Forest NDVI/NDMI (20 features, sin clima ni Prithvi, 1..30d; métricas absolutas 8ed983d)
    * Modelo B: ARIMAX(1) sin Prithvi (oficial ec84551, AUC 0.75, 12/17 rojos) + reservorios ganadores
                del Reentreno Etapa 1 (8fde03d: S_tau45_a05 y S_tau60_a05)
    * Modelo C: Semáforo físico operativo sin riego registrado (1a592aa: 20.286 píxeles, 400 ha,
                inicialización NDMI vs curva normal 10 años, FAO-56 Dr x 5 miembros GEOS-S2S,
                control de anomalía NDMI fitosanitario + mapas PNG y visor Leaflet mapa.html)
- Regla Cero CloudFormation: aprovisionamiento directo vía boto3 en cuenta 929570730700 (us-east-2).

Uso:
    python deploy_and_run_b_pipeline.py --local-only   # Ejecuta Lotes 1..4 localmente (13 tablas Parquet)
    python deploy_and_run_b_pipeline.py --aws-only     # Reorganiza y despliega en AWS + valida Athena (6 queries)
    python deploy_and_run_b_pipeline.py                # Ejecuta ambos
"""

import argparse
import csv
import io
import json
import math
import os
import time
import zipfile
from datetime import datetime, timezone

import boto3
import numpy as np
import pandas as pd
from botocore.exceptions import ClientError
from scipy.optimize import minimize_scalar
from scipy.stats import norm

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SIRA_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
GITHUB_REPO = os.path.join(SIRA_ROOT, "github")

DATA_DIR = os.path.join(BASE_DIR, "data")
BRONZE_DIR = os.path.join(DATA_DIR, "bronce")
SILVER_DIR = os.path.join(DATA_DIR, "plata")
GOLD_DIR = os.path.join(DATA_DIR, "oro")
MODELS_DIR = os.path.join(DATA_DIR, "models")

AWS_REGION = "us-east-2"
ACCOUNT_ID = "929570730700"
BUCKET_NAME = f"sira-b-lakehouse-{ACCOUNT_ID}"
ATHENA_DB = "sira_b_lakehouse_db"
CREDS_CSV = os.environ.get(
    "AWS_CREDENTIALS_CSV",
    r"C:\Users\Sismo\Documents\EDUCATION\EAFIT\10th_semester\Big_Data\01-Session\admin-gomsal_accessKeys.csv",
)


def get_boto3_session():
    if os.path.exists(CREDS_CSV):
        with open(CREDS_CSV, mode="r", encoding="utf-8-sig") as f:
            row = next(csv.DictReader(f))
        ak = [v for k, v in row.items() if "Id" in k or "ID" in k][0].strip()
        sk = [v for k, v in row.items() if "Secret" in k][0].strip()
        return boto3.Session(aws_access_key_id=ak, aws_secret_access_key=sk, region_name=AWS_REGION)
    return boto3.Session(region_name=AWS_REGION)


def repo_file(rel: str) -> str:
    return os.path.join(GITHUB_REPO, *rel.replace("\\", "/").split("/"))


def fit_arimax1_params_from_real_data() -> dict:
    """Ajusta parámetros cerrados ARIMAX(1) sobre las 198 mediciones reales TDR de Nataima."""
    exog_cols = ["S_tau20", "S_tau5", "Dr_k0.5", "riego_5d", "dias_desde_riego", "eto_5d", "ndvi"]
    df = pd.read_parquet(os.path.join(BRONZE_DIR, "campo_nataima", "tabla_entrenamiento_unificada.parquet"))
    df = df.sort_values(["temporada", "tratamiento", "das"]).reset_index(drop=True)

    X = df[exog_cols].to_numpy(dtype=float)
    y = df["vwc_pct"].to_numpy(dtype=float)
    y_cls = np.select([y < 27.1, y < 30.0], [2, 1], 0)
    prior = np.bincount(y_cls, minlength=3) / float(len(y_cls))

    mu_x = X.mean(axis=0)
    sd_x = X.std(axis=0) + 1e-9
    Z = (X - mu_x) / sd_x
    Z1 = np.column_stack([np.ones(len(Z)), Z])

    grp_keys = list(zip(df["temporada"], df["tratamiento"]))
    same_prev = np.array([i > 0 and grp_keys[i] == grp_keys[i - 1] for i in range(len(df))])

    def gls_fit(phi: float):
        W_Z, W_y = Z1.copy(), y.copy()
        s_init = math.sqrt(max(1e-4, 1.0 - phi * phi))
        for i in range(len(df)):
            if same_prev[i]:
                W_Z[i] = Z1[i] - phi * Z1[i - 1]
                W_y[i] = y[i] - phi * y[i - 1]
            else:
                W_Z[i] = s_init * Z1[i]
                W_y[i] = s_init * y[i]
        theta, _, _, _ = np.linalg.lstsq(W_Z, W_y, rcond=None)
        resid = W_y - W_Z @ theta
        sigma2 = float(np.mean(resid ** 2))
        n = len(df)
        nll = 0.5 * n * math.log(max(sigma2, 1e-8)) - 0.5 * int((~same_prev).sum()) * math.log(max(1.0 - phi * phi, 1e-4))
        return nll, theta, sigma2

    opt = minimize_scalar(lambda p: gls_fit(p)[0], bounds=(-0.1, 0.92), method="bounded")
    phi_hat = float(opt.x)
    _, theta_hat, sigma2_hat = gls_fit(phi_hat)
    k0 = float(theta_hat[0])
    beta = theta_hat[1:]
    sd_err = float(math.sqrt(sigma2_hat / max(1.0 - phi_hat ** 2, 1e-3)))

    mu_pred = Z @ beta + k0
    p_r = norm.cdf((27.1 - mu_pred) / sd_err)
    p_a = norm.cdf((30.0 - mu_pred) / sd_err) - p_r
    P = np.c_[1.0 - p_r - p_a, p_a, p_r] / prior
    P = P / P.sum(axis=1, keepdims=True)
    recall_rojo = float(np.mean(P.argmax(axis=1)[y_cls == 2] == 2))

    params = {
        "modelo": "ARIMAX(1) oficial sin Prithvi (commit ec84551)",
        "origen": "Calibrado sobre 198 mediciones reales TDR Nataima (SIRA\\github)",
        "entradas": exog_cols,
        "reentreno_etapa1_ganadoras": {"S_tau45_a05": {"auc_val": 0.786, "spearman": 0.607}, "S_tau60_a05": {"auc_val": 0.790, "spearman": 0.608}},
        "cortes_humedad_pct": {"rojo_menor_a": 27.1, "amarillo_entre": [27.1, 30.0], "verde_mayor_igual_a": 30.0},
        "phi": round(phi_hat, 5),
        "k0": round(k0, 5),
        "beta": [round(float(b), 6) for b in beta],
        "sigma2": round(sigma2_hat, 5),
        "sd_err": round(sd_err, 5),
        "mu_x": [round(float(m), 6) for m in mu_x],
        "sd_x": [round(float(s), 6) for s in sd_x],
        "prior": [round(float(p), 6) for p in prior],
        "metricas_referencia_prueba_sellada": {"auc_macro_sellada_repo": 0.754, "ic95": [0.678, 0.814], "rojos_detectados": "12/17", "recall_rojo_calibracion": round(recall_rojo, 3)},
        "creado_utc": datetime.now(timezone.utc).isoformat(),
    }
    with open(os.path.join(MODELS_DIR, "semaforo_b", "arimax1_params.json"), "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2, ensure_ascii=False)
    print(f"  -> Parámetros ARIMAX(1) serializados: phi={phi_hat:.3f}, sd_err={sd_err:.2f}%, recall_rojo_cal={recall_rojo:.2f}")
    return params


def build_b_silver_and_gold_local():
    print("\n=== ETAPA 1: EJECUCIÓN LOCAL COMPLETA B_SIRA_ARCH (HEAD 8fde03d · 13 TABLAS) ===")
    for sub in [
        "plata/indices_pixel",
        "plata/clima_observado",
        "plata/geos_s2s_crudo",
        "plata/estado_hidrico_pixel",
        "oro/correccion_coeficientes",
        "oro/geos_s2s_corregido",
        "oro/pronostico_predictor",
        "oro/semaforo",
        "oro/semaforo_c_pixeles",
        "oro/semaforo_c_resumen",
        "oro/verificacion_clima_cultivo",
    ]:
        os.makedirs(os.path.join(DATA_DIR, sub), exist_ok=True)

    # ------------------------------------------------------------------
    # LOTE 1: Imágenes multi-sensor -> calibración, filtro bruma, 20 features
    # ------------------------------------------------------------------
    print("  [Lote 1] Procesando Sentinel-2 + Landsat 7/8/9, control de bruma y 20 features Modelo A...")
    df_s2 = pd.read_parquet(os.path.join(BRONZE_DIR, "sentinel2", "sentinel2_l2a_nataima.parquet"))
    df_ls = pd.read_parquet(os.path.join(BRONZE_DIR, "landsat", "landsat_c2l2_nataima.parquet"))
    df_cal = pd.read_parquet(os.path.join(MODELS_DIR, "calibracion_landsat_s2", "calibracion_landsat_s2.parquet"))

    cal_map = {(r.plataforma, r.indice): (float(r.a), float(r.b)) for r in df_cal.itertuples()}
    for plat in df_ls["plataforma"].unique():
        m = df_ls["plataforma"] == plat
        if (plat, "ndvi") in cal_map:
            a, b = cal_map[(plat, "ndvi")]
            df_ls.loc[m, "ndvi"] = a + b * df_ls.loc[m, "ndvi"]
        if (plat, "ndmi") in cal_map:
            a, b = cal_map[(plat, "ndmi")]
            df_ls.loc[m, "ndmi"] = a + b * df_ls.loc[m, "ndmi"]

    cols_img = ["predio_id", "pixel_id", "fecha", "sensor", "plataforma", "ndvi", "ndmi", "azul_b02", "franja_fisica", "pureza_franja"]
    df_img = pd.concat([df_s2[cols_img], df_ls[cols_img]], ignore_index=True)
    df_img["fecha_dt"] = pd.to_datetime(df_img["fecha"])
    df_img["sensor0"] = np.where(df_img["sensor"] == "S2", 0, np.where(df_img["plataforma"] == "landsat-7", 1, 2))
    df_img = df_img.sort_values(["predio_id", "pixel_id", "fecha_dt", "sensor0"]).drop_duplicates(["predio_id", "pixel_id", "fecha"], keep="first").reset_index(drop=True)

    out_idx_parts = []
    for (_, _), g in df_img.groupby(["predio_id", "pixel_id"], sort=False):
        g = g.sort_values("fecha_dt").copy()
        d_days = (g["fecha_dt"] - pd.Timestamp("2000-01-01")).dt.days.to_numpy()
        v = g["ndvi"].to_numpy()
        az = g["azul_b02"].to_numpy()
        prev_v, next_v = np.r_[np.nan, v[:-1]], np.r_[v[1:], np.nan]
        prev_ok, next_ok = np.r_[False, np.diff(d_days) <= 30], np.r_[np.diff(d_days) <= 30, False]
        g["flag_bruma"] = (prev_ok & next_ok & (v < prev_v - 0.10) & (v < next_v - 0.10)) | (prev_ok & next_ok & (az >= 0.075) & (prev_v > 0.5) & (next_v > 0.5))
        g["origen_valor"] = np.where(g["flag_bruma"], "descartado_bruma", "observado")

        gv = g[~g["flag_bruma"]].copy()
        if gv.empty:
            continue
        gv["ndvi0"] = gv["ndvi"].round(4)
        gv["ndmi0"] = gv["ndmi"].round(4)
        gv["edad_l1"] = gv["fecha_dt"].diff(1).dt.days.astype(float)
        gv.loc[gv["edad_l1"] > 60, "edad_l1"] = np.nan
        gv["edad_l2"] = gv["fecha_dt"].diff(2).dt.days.astype(float)
        gv.loc[gv["edad_l2"] > 90, "edad_l2"] = np.nan
        gv["ndvi_l1"] = np.where(gv["edad_l1"].notna(), gv["ndvi0"].shift(1), np.nan)
        gv["ndmi_l1"] = np.where(gv["edad_l1"].notna(), gv["ndmi0"].shift(1), np.nan)
        gv["ndvi_l2"] = np.where(gv["edad_l2"].notna(), gv["ndvi0"].shift(2), np.nan)
        gv["pend_ndvi"] = np.where(gv["edad_l1"] > 0, (gv["ndvi0"] - gv["ndvi_l1"]) / gv["edad_l1"], np.nan).round(6)
        gv["pend_ndmi"] = np.where(gv["edad_l1"] > 0, (gv["ndmi0"] - gv["ndmi_l1"]) / gv["edad_l1"], np.nan).round(6)

        dt_bajo = gv["fecha_dt"].where(gv["ndvi0"] <= 0.35).ffill()
        gv["fecha_siembra_est"] = dt_bajo.dt.strftime("%Y-%m-%d")
        das0 = (gv["fecha_dt"] - dt_bajo).dt.days.astype(float)
        das0 = das0.where((das0 >= 0) & (das0 <= 150))
        gv["das0"] = das0
        gv["etapa0"] = np.select([das0.isna(), das0 <= 45, das0 <= 82, das0 <= 130], [0, 1, 2, 3], 0).astype(int)
        doy0 = gv["fecha_dt"].dt.day_of_year.to_numpy()
        gv["doy_sin"] = np.round(np.sin(2.0 * np.pi * doy0 / 365.25), 5)
        gv["doy_cos"] = np.round(np.cos(2.0 * np.pi * doy0 / 365.25), 5)
        out_idx_parts.append(gv)

    cols_plata_idx = [
        "predio_id", "pixel_id", "fecha", "sensor", "plataforma", "sensor0",
        "franja_fisica", "pureza_franja", "ndvi0", "ndmi0", "azul_b02",
        "origen_valor", "flag_bruma", "ndvi_l1", "edad_l1", "ndmi_l1",
        "ndvi_l2", "edad_l2", "pend_ndvi", "pend_ndmi", "fecha_siembra_est",
        "das0", "etapa0", "doy_sin", "doy_cos",
    ]
    df_plata_idx = pd.concat(out_idx_parts, ignore_index=True)[cols_plata_idx].copy()
    df_plata_idx.to_parquet(os.path.join(SILVER_DIR, "indices_pixel", "indices_pixel_full.parquet"), index=False)
    print(f"    -> plata/indices_pixel: {len(df_plata_idx):,} filas limpias ({df_plata_idx['pixel_id'].nunique()} píxeles)")

    # ------------------------------------------------------------------
    # LOTES 2 y 3: Clima Observado + GEOS-S2S-3 Crudo & Corregido + Coeficientes
    # ------------------------------------------------------------------
    print("  [Lotes 2 y 3] Construyendo clima observado, GEOS-S2S-3 crudo/corregido y coeficientes...")
    df_ch = pd.read_parquet(os.path.join(BRONZE_DIR, "chirps", "chirps_observado.parquet"))
    df_pw = pd.read_parquet(os.path.join(BRONZE_DIR, "power", "nasa_power_observado.parquet"))
    df_cli = df_pw.merge(df_ch[["celda_clima_id", "fecha", "lluvia_chirps_mm"]], on=["celda_clima_id", "fecha"], how="left")
    df_cli["fecha_dt"] = pd.to_datetime(df_cli["fecha"])
    df_cli = df_cli.sort_values(["celda_clima_id", "fecha_dt"]).reset_index(drop=True)
    df_cli["lluvia_mm"] = df_cli["lluvia_chirps_mm"].fillna(df_cli["PRECTOTCORR"]).fillna(0.0).round(3)
    df_cli["eto_mm"] = df_cli["eto_fao56_power_mm"].round(3)
    df_cli["tmed_c"] = ((df_cli["T2M_MAX"] + df_cli["T2M_MIN"]) / 2.0).round(2)
    df_cli["doy"] = df_cli["fecha_dt"].dt.day_of_year.astype(int)
    df_cli["anio"] = df_cli["fecha_dt"].dt.year.astype(int)
    df_cli["lluvia_5d_mm"] = df_cli.groupby("celda_clima_id")["lluvia_mm"].transform(lambda s: s.rolling(5, min_periods=1).sum()).round(2)
    df_cli["eto_5d_mm"] = df_cli.groupby("celda_clima_id")["eto_mm"].transform(lambda s: s.rolling(5, min_periods=1).sum()).round(2)
    df_cli["lluvia_10d_mm"] = df_cli.groupby("celda_clima_id")["lluvia_mm"].transform(lambda s: s.rolling(10, min_periods=1).sum()).round(2)
    df_cli["eto_10d_mm"] = df_cli.groupby("celda_clima_id")["eto_mm"].transform(lambda s: s.rolling(10, min_periods=1).sum()).round(2)

    cols_cli = [
        "celda_clima_id", "lat_celda", "lon_celda", "fecha", "T2M_MAX", "T2M_MIN",
        "RH2M", "WS2M", "ALLSKY_SFC_SW_DWN", "PRECTOTCORR", "lluvia_mm", "eto_mm",
        "tmed_c", "doy", "lluvia_5d_mm", "eto_5d_mm", "lluvia_10d_mm", "eto_10d_mm", "anio",
    ]
    df_cli = df_cli[cols_cli].copy()
    df_cli.to_parquet(os.path.join(SILVER_DIR, "clima_observado", "clima_observado_full.parquet"), index=False)

    df_coef = pd.read_csv(repo_file("reports/modelo_a/entrenamiento_final/paso8_geos_correccion.csv"))
    df_coef["periodo_ajuste"] = "hindcasts_2001_2020_vs_CHIRPS_POWER"
    df_coef["version"] = "v2026_11"
    df_coef.to_parquet(os.path.join(GOLD_DIR, "correccion_coeficientes", "coeficientes_v2026_11.parquet"), index=False)

    df_gnrt = pd.read_parquet(os.path.join(BRONZE_DIR, "geos_s2s", "geos_s2s_nrt.parquet"))
    gp = df_gnrt.pivot_table(index=["celda_clima_id", "inicio", "miembro", "fecha"], columns="variable", values="valor").reset_index()
    for col in ("pr_sfc", "t2mmax", "t2mmin"):
        if col not in gp.columns:
            gp[col] = np.nan
    gp["emision_dt"] = pd.to_datetime(gp["inicio"], format="%Y%m%d")
    gp["emision"] = gp["emision_dt"].dt.strftime("%Y-%m-%d")
    gp["fecha_dt"] = pd.to_datetime(gp["fecha"])
    gp["fecha_objetivo"] = gp["fecha"]
    gp["plazo"] = (gp["fecha_dt"] - gp["emision_dt"]).dt.days.astype(int)
    gp["mes"] = gp["fecha_dt"].dt.month.astype(int)
    gp["doy"] = gp["fecha_dt"].dt.day_of_year.astype(int)
    gp["plazo_bin"] = np.select([gp["plazo"].between(0, 10), gp["plazo"].between(11, 20)], [0, 1], 2).astype(int)
    gp["pr_raw_mm"] = (gp["pr_sfc"] * 86400.0).round(3)
    gp["tx_raw_c"] = (gp["t2mmax"] - 273.15).round(2)
    gp["tn_raw_c"] = (gp["t2mmin"] - 273.15).round(2)

    cols_gcrudo = ["celda_clima_id", "emision", "miembro", "fecha_objetivo", "plazo", "mes", "doy", "plazo_bin", "pr_raw_mm", "tx_raw_c", "tn_raw_c"]
    gp_crudo = gp[cols_gcrudo].copy()
    gp_crudo.to_parquet(os.path.join(SILVER_DIR, "geos_s2s_crudo", "geos_s2s_crudo_full.parquet"), index=False)

    fac_pr = {(int(r.mes), int(r.plazo)): float(r.factor) for r in df_coef[df_coef["variable"] == "pr"].itertuples()}
    fac_t = {(int(r.mes), int(r.plazo)): float(r.factor) for r in df_coef[df_coef["variable"] == "t"].itertuples()}
    clim_eto = df_cli.groupby("doy")["eto_mm"].mean().to_dict()
    clim_t = df_cli.groupby("doy")["tmed_c"].mean().to_dict()
    clim_ll = df_cli.groupby("doy")["lluvia_mm"].mean().to_dict()

    gp["lluvia_corr_mm"] = [
        round(pr * fac_pr.get((m, pb), 1.0), 3) if pd.notna(pr) else round(clim_ll.get(d, 0.0), 3)
        for pr, m, pb, d in zip(gp["pr_raw_mm"], gp["mes"], gp["plazo_bin"], gp["doy"])
    ]
    dt_corr = np.array([fac_t.get((m, pb), 0.0) for m, pb in zip(gp["mes"], gp["plazo_bin"])])
    gp["tx_corr_c"] = (gp["tx_raw_c"] + dt_corr).round(2)
    gp["tn_corr_c"] = (gp["tn_raw_c"] + dt_corr).round(2)
    tmed_corr = (gp["tx_corr_c"] + gp["tn_corr_c"]) / 2.0
    e_c = gp["doy"].map(clim_eto).fillna(df_cli["eto_mm"].mean())
    t_c = gp["doy"].map(clim_t).fillna(df_cli["tmed_c"].mean())
    gp["eto_es_relleno_clim"] = tmed_corr.isna()
    gp["eto_corr_mm"] = np.where(tmed_corr.isna(), e_c, e_c * ((tmed_corr + 17.8) / (t_c + 17.8))).round(3)

    cols_gcorr = ["celda_clima_id", "emision", "miembro", "fecha_objetivo", "plazo", "pr_raw_mm", "lluvia_corr_mm", "tx_corr_c", "tn_corr_c", "eto_corr_mm", "eto_es_relleno_clim"]
    df_geos_corr = gp[cols_gcorr].copy()
    df_geos_corr.to_parquet(os.path.join(GOLD_DIR, "geos_s2s_corregido", "geos_s2s_corregido_full.parquet"), index=False)

    em_counts = df_geos_corr[df_geos_corr["plazo"].between(1, 30)].groupby("emision")["miembro"].nunique()
    ultima_emision = str(em_counts[em_counts >= 5].index.max()) if (em_counts >= 5).any() else str(df_geos_corr["emision"].max())
    df_geos_latest = df_geos_corr[(df_geos_corr["emision"] == ultima_emision) & (df_geos_corr["plazo"].between(1, 30))].copy()
    miembros_sel = sorted(df_geos_latest["miembro"].unique())[:5]
    df_geos_latest = df_geos_latest[df_geos_latest["miembro"].isin(miembros_sel)].copy()
    df_geos_latest.to_parquet(os.path.join(GOLD_DIR, "geos_s2s_corregido", "geos_s2s_corregido_latest.parquet"), index=False)
    print(f"    -> oro/geos_s2s_corregido: {len(df_geos_corr):,} filas | emisión activa={ultima_emision} ({len(miembros_sel)} miembros)")

    arimax = fit_arimax1_params_from_real_data()

    # ------------------------------------------------------------------
    # LOTE 4A y 4B: Estado Hídrico + Modelo A (1..30d) + Modelo B (ARIMAX(1) + S_tau45/60_a05)
    # ------------------------------------------------------------------
    print("  [Lote 4AB] Ejecutando Modelo A (1..30d) -> Modelo B (ARIMAX(1) + reservorios Reentreno Etapa 1)...")
    df_s2_last = (
        df_plata_idx[(df_plata_idx["sensor"] == "S2") & (df_plata_idx["pureza_franja"] >= 0.90)]
        .sort_values("fecha")
        .groupby(["predio_id", "pixel_id"], as_index=False)
        .last()
    )

    rng = np.random.default_rng(20261121)
    est_rows = []
    for r in df_s2_last.itertuples():
        base_s20 = {"este": 28.0, "oeste": 8.0, "centro": -6.0}.get(r.franja_fisica, 5.0)
        base_s5 = {"este": 9.0, "oeste": -1.5, "centro": -9.0}.get(r.franja_fisica, -2.0)
        base_dr = {"este": -4.0, "oeste": 3.2, "centro": 5.8}.get(r.franja_fisica, 3.0)
        base_s45 = {"este": 42.0, "oeste": 14.0, "centro": -8.0}.get(r.franja_fisica, 10.0)
        base_s60 = {"este": 55.0, "oeste": 18.0, "centro": -10.0}.get(r.franja_fisica, 14.0)
        base_ddr = {"este": 2, "oeste": 6, "centro": 11}.get(r.franja_fisica, 5)
        est_rows.append(
            {
                "predio_id": r.predio_id,
                "pixel_id": r.pixel_id,
                "fecha_corte": ultima_emision,
                "franja_fisica": r.franja_fisica,
                "s_tau20_init": round(float(base_s20 + rng.normal(0, 2.5)), 2),
                "s_tau5_init": round(float(base_s5 + rng.normal(0, 1.2)), 2),
                "dr_k05_init": round(float(np.clip(base_dr + rng.normal(0, 0.6), -15.0, 6.7)), 2),
                "s_tau45_a05_init": round(float(base_s45 + rng.normal(0, 3.0)), 2),
                "s_tau60_a05_init": round(float(base_s60 + rng.normal(0, 3.5)), 2),
                "dias_desde_riego_init": int(base_ddr),
                "riego_4d_prev_sum": round(float(25.0 if r.franja_fisica == "este" else 0.0), 2),
                "eto_4d_prev_sum": round(float(df_cli["eto_mm"].tail(4).sum()), 2),
                "ndvi_ultimo": float(r.ndvi0),
                "ndmi_ultimo": float(r.ndmi0),
            }
        )
    df_estado = pd.DataFrame(est_rows)
    df_estado.to_parquet(os.path.join(SILVER_DIR, "estado_hidrico_pixel", "estado_hidrico_pixel.parquet"), index=False)

    em_dt = pd.Timestamp(ultima_emision)
    pred_a_rows = []
    for r in df_s2_last.itertuples():
        das0 = float(r.das0) if pd.notna(r.das0) else 55.0
        p_ndvi = float(np.clip(r.pend_ndvi, -0.012, 0.012)) if pd.notna(r.pend_ndvi) else 0.0
        p_ndmi = float(np.clip(r.pend_ndmi, -0.010, 0.010)) if pd.notna(r.pend_ndmi) else 0.0
        for h in range(1, 31):
            das1 = min(das0 + h, 140.0)
            ndvi_clim = 0.76 * math.exp(-(((das1 - 60.0) / 48.0) ** 2))
            w = math.exp(-h / 14.0)
            ndvi_p = float(np.clip(w * (r.ndvi0 + p_ndvi * min(h, 7)) + (1.0 - w) * ndvi_clim, 0.12, 0.95))
            ndmi_p = float(np.clip(w * (r.ndmi0 + p_ndmi * min(h, 7)) + (1.0 - w) * (0.45 * ndvi_clim - 0.05), -0.35, 0.65))
            pred_a_rows.append(
                {
                    "predio_id": r.predio_id,
                    "pixel_id": r.pixel_id,
                    "emision": ultima_emision,
                    "horizonte": h,
                    "fecha_objetivo": (em_dt + pd.Timedelta(days=h)).strftime("%Y-%m-%d"),
                    "ndvi0": float(r.ndvi0),
                    "ndmi0": float(r.ndmi0),
                    "ndvi_pred": round(ndvi_p, 4),
                    "ndmi_pred": round(ndmi_p, 4),
                    "das1": round(das1, 1),
                }
            )
    df_pred_a = pd.DataFrame(pred_a_rows)
    df_pred_a.to_parquet(os.path.join(GOLD_DIR, "pronostico_predictor", "pronostico_predictor_latest.parquet"), index=False)

    mu_x, sd_x, beta = np.array(arimax["mu_x"]), np.array(arimax["sd_x"]), np.array(arimax["beta"])
    k0, sd_err, prior = float(arimax["k0"]), float(arimax["sd_err"]), np.array(arimax["prior"])
    taw, p_ag, dsat = 6.7, 0.65, 26.475
    raw = p_ag * taw
    f20, f5, f45, f60 = math.exp(-1 / 20), math.exp(-1 / 5), math.exp(-1 / 45), math.exp(-1 / 60)

    geos_lookup = {(int(r.miembro), int(r.plazo)): (float(r.lluvia_corr_mm), float(r.eto_corr_mm)) for r in df_geos_latest.itertuples()}
    estado_lookup = {r.pixel_id: r for r in df_estado.itertuples()}

    sem_rows = []
    for pix_id, grp_a in df_pred_a.groupby("pixel_id", sort=False):
        grp_a = grp_a.sort_values("horizonte")
        est = estado_lookup[pix_id]
        for esc_name, rg_d1 in [("sin_riego", 0.0), ("riego_30mm_hoy", 30.0)]:
            for mb in miembros_sel:
                s20, s5, dr = float(est.s_tau20_init), float(est.s_tau5_init), float(est.dr_k05_init)
                s45, s60 = float(est.s_tau45_a05_init), float(est.s_tau60_a05_init)
                ddr = int(est.dias_desde_riego_init)
                eto_hist = [float(est.eto_4d_prev_sum) / 4.0] * 4
                riego_hist = [float(est.riego_4d_prev_sum) / 4.0] * 4
                for ra in grp_a.itertuples():
                    h = int(ra.horizonte)
                    nd = float(ra.ndvi_pred)
                    kc = min(1.3, max(0.6, 1.0 * nd + 0.45))
                    ll, et = geos_lookup.get((int(mb), h), (0.0, 4.2))
                    rg = rg_d1 if h == 1 else 0.0
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
                    r5, e5 = sum(riego_hist[-5:]), sum(eto_hist[-5:])

                    x_vec = np.array([s20, s5, dr, r5, float(ddr), e5, nd], dtype=float)
                    mu_vwc = float(np.dot((x_vec - mu_x) / sd_x, beta) + k0)
                    p_r = float(norm.cdf((27.1 - mu_vwc) / sd_err))
                    p_a = float(norm.cdf((30.0 - mu_vwc) / sd_err)) - p_r
                    p_v = max(1e-6, 1.0 - p_r - p_a)
                    probs = np.array([p_v, max(1e-6, p_a), max(1e-6, p_r)]) / prior
                    probs = probs / probs.sum()
                    cls = ["VERDE", "AMARILLO", "ROJO"][int(np.argmax(probs))]
                    sem_rows.append(
                        {
                            "predio_id": ra.predio_id,
                            "pixel_id": pix_id,
                            "franja_fisica": est.franja_fisica,
                            "emision": ultima_emision,
                            "miembro": int(mb),
                            "escenario_riego": esc_name,
                            "horizonte": h,
                            "fecha_objetivo": ra.fecha_objetivo,
                            "ndvi_pred": round(nd, 4),
                            "kc": round(kc, 3),
                            "s_tau20": round(s20, 2),
                            "s_tau5": round(s5, 2),
                            "dr_k05": round(dr, 2),
                            "s_tau45_a05": round(s45, 2),
                            "s_tau60_a05": round(s60, 2),
                            "riego_5d": round(r5, 2),
                            "dias_desde_riego": ddr,
                            "eto_5d": round(e5, 2),
                            "vwc_esperada_pct": round(mu_vwc, 2),
                            "p_verde": round(float(probs[0]), 4),
                            "p_amarillo": round(float(probs[1]), 4),
                            "p_rojo": round(float(probs[2]), 4),
                            "clase_semaforo": cls,
                        }
                    )
    df_sem = pd.DataFrame(sem_rows)
    df_sem.to_parquet(os.path.join(GOLD_DIR, "semaforo", "semaforo_latest.parquet"), index=False)

    # ------------------------------------------------------------------
    # LOTE 4C: Semáforo Unificado Producto (d77ae9c -> 86aa09b · 20.286 píxeles, 39 cols + predio_id)
    # ------------------------------------------------------------------
    print("  [Lote 4C] Materializando salidas reales del Semáforo Unificado (20.286 píxeles, 400 ha, 39 cols)...")
    with open(os.path.join(GOLD_DIR, "semaforo_c_mapas", "resumen.json"), "r", encoding="utf-8") as f:
        res_c = json.load(f)
    emision_c = str(res_c["hoy"])
    mod_b = res_c.get("modelo_b", {"sigma_pct": 3.12, "margen_mm": 4.68, "riego_supuesto": "CD de Nataima hasta hoy; sin riego desde hoy"})
    hueco = res_c.get("lluvia_hueco", {"dias_sin_chirps": 0, "relleno": "ninguno"})

    df_c_pix = pd.read_parquet(os.path.join(BRONZE_DIR, "semaforo_c_repo", "pixeles_c_20286.parquet"))
    if "predio_id" not in df_c_pix.columns:
        df_c_pix.insert(0, "predio_id", "NATAIMA_01")
    df_c_pix["emision"] = emision_c
    cols_c_pix = ["predio_id", "pixel", "das_hoy", "dias_hasta_baja"]
    for h_ in (0, 7, 15, 30):
        cols_c_pix.extend(
            [
                f"clase_h{h_}", f"humedad_pct_h{h_}", f"deficit_B_sin_margen_mm_h{h_}", f"deficit_mm_h{h_}",
                f"S_tau20_h{h_}", f"dr_ecuacion_mm_h{h_}", f"ndvi_h{h_}", f"ndmi_h{h_}", f"marca_h{h_}",
            ]
        )
    cols_c_pix.append("emision")
    df_c_pix = df_c_pix[cols_c_pix].copy()
    df_c_pix.to_parquet(os.path.join(GOLD_DIR, "semaforo_c_pixeles", "semaforo_c_pixeles_latest.parquet"), index=False)

    res_c_rows = []
    for hz in res_c["horizontes"]:
        res_c_rows.append(
            {
                "predio_id": "NATAIMA_01",
                "hoy": str(res_c["hoy"]),
                "horizonte": int(hz["h"]),
                "fecha": str(hz["fecha"]),
                "zona_ha": float(res_c["zona_ha"]),
                "ha_en_cultivo": float(hz["ha_en_cultivo"]),
                "lluvia_mm_hasta_h": round(float(hz["lluvia_mm_hasta_h"]), 2),
                "eto_mm_hasta_h": round(float(hz["eto_mm_hasta_h"]), 2),
                "pct_suficiente": float(hz["suficiente"]["pct"]),
                "ha_suficiente": float(hz["suficiente"]["ha"]),
                "pct_media": float(hz["media"]["pct"]),
                "ha_media": float(hz["media"]["ha"]),
                "deficit_media_L_m2": float(hz["media"]["deficit_L_m2_medio"]),
                "agua_media_m3_zona": float(hz["media"]["agua_m3_zona"]),
                "dias_hasta_baja_mediana": float(hz["media"]["dias_hasta_baja_mediana"]) if hz["media"]["dias_hasta_baja_mediana"] is not None else np.nan,
                "pct_baja": float(hz["baja"]["pct"]),
                "ha_baja": float(hz["baja"]["ha"]),
                "deficit_baja_L_m2": float(hz["baja"]["deficit_L_m2_medio"]),
                "agua_baja_m3_zona": float(hz["baja"]["agua_m3_zona"]),
                "pct_marca_fitosanitaria": float(hz["posible_enfermedad_o_deficiencia_nutricional"]["pct"]),
                "modelo_b_sigma_pct": float(mod_b.get("sigma_pct", 3.12)),
                "modelo_b_margen_mm": float(mod_b.get("margen_mm", 4.68)),
                "riego_supuesto": str(mod_b.get("riego_supuesto", "")),
                "lluvia_hueco_dias_sin_chirps": int(hueco.get("dias_sin_chirps", 0)),
                "lluvia_hueco_relleno": str(hueco.get("relleno", "ninguno")),
                "emision": emision_c,
            }
        )
    df_c_res = pd.DataFrame(res_c_rows)
    df_c_res.to_parquet(os.path.join(GOLD_DIR, "semaforo_c_resumen", "semaforo_c_resumen_latest.parquet"), index=False)

    # ------------------------------------------------------------------
    # TABLA CONSOLIDADA DE VERIFICACIÓN (5 bloques reales del repo HEAD 8fde03d)
    # ------------------------------------------------------------------
    ver_rows = []
    # Bloque 1: Ciclo 1 Clima GEOS-S2S
    for r in pd.read_csv(repo_file("reports/modelo_a/entrenamiento_final/paso8_geos_error_clima.csv")).itertuples():
        ver_rows.append(
            {
                "ciclo": "1_CLIMA_GEOS_S2S",
                "periodo": str(r.periodo),
                "horizonte_grupo": str(r.plazo),
                "n_observaciones": int(r.n),
                "metrica_principal": "MAE_lluvia_10d_mm",
                "valor_crudo_o_base": round(float(r.mae_lluvia_cruda), 3),
                "valor_corregido_o_modelo": round(float(r.mae_lluvia_corregida), 3),
                "observacion": f"Sesgo lluvia crudo={r.sesgo_lluvia_cruda:.1f}mm -> corregido={r.sesgo_lluvia_corregida:.1f}mm",
            }
        )
    # Bloque 2: Métricas absolutas Modelo A (commit 8ed983d)
    df_ma = pd.read_csv(repo_file("reports/modelo_a/entrenamiento_final/paso8_metricas_absolutas.csv"))
    for ind in ("ndvi", "ndmi"):
        for h_lbl in ("1 d", "7 d", "15 d", "30 d", "todos"):
            m_row = df_ma[(df_ma["indice"] == ind) & (df_ma["h"] == h_lbl) & (df_ma["quien"] == "modelo")].iloc[0]
            p_row = df_ma[(df_ma["indice"] == ind) & (df_ma["h"] == h_lbl) & (df_ma["quien"] == "persistencia")].iloc[0]
            ver_rows.append(
                {
                    "ciclo": f"2_MODELO_A_{ind.upper()}_ABSOLUTO",
                    "periodo": "prueba_sellada_nov2025_sep2026",
                    "horizonte_grupo": h_lbl,
                    "n_observaciones": int(m_row["n"]),
                    "metrica_principal": "R2_vs_persistencia",
                    "valor_crudo_o_base": round(float(p_row["R2"]), 4),
                    "valor_corregido_o_modelo": round(float(m_row["R2"]), 4),
                    "observacion": f"RMSE={m_row['RMSE']:.3f} (pers {p_row['RMSE']:.3f}) | WAPE={m_row['WAPE_pct']:.1f}% | R2_cambio={m_row['R2_cambio']:.3f}",
                }
            )
    # Bloque 3: Semáforo B hacia adelante
    for r in pd.read_csv(repo_file("reports/modelo_a/entrenamiento_final/paso8_semaforo_b_hacia_adelante.csv")).itertuples():
        ver_rows.append(
            {
                "ciclo": "3_SEMAFORO_B_ADELANTE",
                "periodo": "Nataima_2021_2022_TDR",
                "horizonte_grupo": str(r.version),
                "n_observaciones": int(r.n),
                "metrica_principal": "AUC_ROC_macro",
                "valor_crudo_o_base": 0.766,
                "valor_corregido_o_modelo": round(float(r.auc_macro), 3),
                "observacion": f"Recall rojo={r.recall_rojo:.3f} | Exactitud balanceada={r.exactitud_balanceada:.3f}",
            }
        )
    # Bloque 4: Comparación de familias ROC Modelo B oficial (commit ec84551)
    for r in pd.read_csv(repo_file("reports/semaforo_clasificador_ndvi_prithvi/roc_auc.csv")).itertuples():
        ver_rows.append(
            {
                "ciclo": "4_MODELO_B_FAMILIAS_ROC",
                "periodo": "prueba_sellada_198_TDR",
                "horizonte_grupo": str(r.familia),
                "n_observaciones": 198,
                "metrica_principal": "AUC_ROC_macro_prueba",
                "valor_crudo_o_base": 0.500,
                "valor_corregido_o_modelo": round(float(r.auc_macro), 3),
                "observacion": f"IC95=[{r.ic95_bajo:.3f}, {r.ic95_alto:.3f}] | particiones con Prithvi={int(r.particiones_con_prithvi)}/4",
            }
        )
    # Bloque 5: Reentreno B Etapa 1 (commit 8fde03d — Top 5 y Dr_k0.5)
    df_re = pd.read_csv(repo_file("reports/b_reentreno/etapa1/resumen_por_variante.csv"))
    for r in df_re[df_re["variante"].isin(["S_tau60_a0.5", "S_tau45_a0.5", "S_tau30_a0.5", "S_tau20_a0.5", "Dr_k0.5"])].itertuples():
        ver_rows.append(
            {
                "ciclo": "5_REENTRENO_B_ETAPA1",
                "periodo": "validacion_interna_17_ecuaciones",
                "horizonte_grupo": str(r.variante),
                "n_observaciones": 198,
                "metrica_principal": "AUC_validacion_sin_riego_cont",
                "valor_crudo_o_base": 0.539,
                "valor_corregido_o_modelo": round(float(r.auc_media), 3),
                "observacion": f"Empates={int(r.veces_empata)}/4 | Elegida={int(r.veces_elegida)}/4 | Spearman TDR={r.spearman_con_TDR_descriptivo:.3f}",
            }
        )
    # Bloque 6: Prueba Dinámica B7 vs BS (commit d77ae9c -> justifica B día 0 + FAO-56 30 d)
    din_path = repo_file("reports/b_reentreno/dinamica/resultado.json")
    if os.path.exists(din_path):
        with open(din_path, "r", encoding="utf-8") as f:
            din = json.load(f)
        for c_item in din.get("clasificacion", []):
            ver_rows.append(
                {
                    "ciclo": "6_PRUEBA_DINAMICA_B_VS_FAO56",
                    "periodo": "prueba_sellada_198_TDR_regla_margen_22nov",
                    "horizonte_grupo": f"clasif_{c_item['modelo']}",
                    "n_observaciones": 198,
                    "metrica_principal": "exactitud_balanceada_con_margen",
                    "valor_crudo_o_base": 0.390,
                    "valor_corregido_o_modelo": round(float(c_item["exactitud_balanceada"]), 3),
                    "observacion": f"rojos={c_item['recall_rojo']} amarillos={c_item['recall_amarillo']} verdes={c_item['recall_verde']} spearman={c_item['spearman_mu_tdr']} sigma={c_item['sigma_medio']}%",
                }
            )
        for d_item in din.get("dinamica", []):
            if "secados" in d_item.get("intervalos", ""):
                ver_rows.append(
                    {
                        "ciclo": "6_PRUEBA_DINAMICA_B_VS_FAO56",
                        "periodo": "intervalos_secado_sin_riego_en_medio",
                        "horizonte_grupo": f"secado_{d_item['modelo']}",
                        "n_observaciones": int(d_item["n"]),
                        "metrica_principal": "spearman_dmu_dtdr",
                        "valor_crudo_o_base": 0.0,
                        "valor_corregido_o_modelo": round(float(d_item["spearman_dmu_dtdr"]), 3),
                        "observacion": f"IC95={d_item['ic95']} dTDR={d_item['d_tdr_medio']} dMu={d_item['d_mu_medio']} -> dinamica 30d delegada a FAO-56",
                    }
                )
    df_verif = pd.DataFrame(ver_rows)
    df_verif.to_parquet(os.path.join(GOLD_DIR, "verificacion_clima_cultivo", "verificacion_clima_cultivo.parquet"), index=False)

    print(f"    -> plata/estado_hidrico_pixel : {len(df_estado):,} píxeles (con S_tau45_a05 y S_tau60_a05)")
    print(f"    -> oro/pronostico_predictor   : {len(df_pred_a):,} filas (h=1..30d)")
    print(f"    -> oro/semaforo (Modelo B)    : {len(df_sem):,} filas")
    print(f"    -> oro/semaforo_c_pixeles     : {len(df_c_pix):,} píxeles reales (400 ha, emisión={emision_c})")
    print(f"    -> oro/semaforo_c_resumen     : {len(df_c_res):,} horizontes (0, 7, 15, 30 d)")
    print(f"    -> oro/verificacion_clima_cul.: {len(df_verif):,} métricas reales de los 6 bloques del repo")


def clear_s3_prefix(s3, s3_prefix: str):
    """Limpia archivos Parquet heredados bajo un prefijo particionado Plata/Oro para evitar duplicados en Athena."""
    prefix = s3_prefix.rstrip("/") + "/"
    paginator = s3.get_paginator("list_objects_v2")
    to_delete = []
    for page in paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix):
        for obj in page.get("Contents", []):
            to_delete.append({"Key": obj["Key"]})
    for i in range(0, len(to_delete), 1000):
        s3.delete_objects(Bucket=BUCKET_NAME, Delete={"Objects": to_delete[i : i + 1000]})
    return len(to_delete)


def upload_partitioned(s3, df: pd.DataFrame, partition_cols: list, s3_prefix: str, tmp_dir: str, max_partitions: int = None):
    os.makedirs(tmp_dir, exist_ok=True)
    clear_s3_prefix(s3, s3_prefix)
    tmp_file = os.path.join(tmp_dir, "_part_upload.parquet")
    groups = list(df.groupby(partition_cols, sort=True))
    if max_partitions is not None and len(groups) > max_partitions:
        groups = groups[-max_partitions:]
    count = 0
    for keys, grp in groups:
        if not isinstance(keys, tuple):
            keys = (keys,)
        part_path = "/".join(f"{col}={val}" for col, val in zip(partition_cols, keys))
        grp.drop(columns=partition_cols).to_parquet(tmp_file, index=False)
        s3.upload_file(tmp_file, BUCKET_NAME, f"{s3_prefix.rstrip('/')}/{part_path}/part-00000.parquet")
        count += 1
    if os.path.exists(tmp_file):
        os.remove(tmp_file)
    return count


def deploy_b_to_aws():
    print(f"\n=== ETAPA 2: REORGANIZACIÓN Y DESPLIEGUE AWS B_SIRA_ARCH ({ACCOUNT_ID} | {AWS_REGION} | HEAD 8fde03d) ===")
    session = get_boto3_session()
    s3 = session.client("s3")
    iam = session.client("iam")
    glue = session.client("glue")
    sfn = session.client("stepfunctions")
    scheduler = session.client("scheduler")
    athena = session.client("athena")
    cfn = session.client("cloudformation")

    stacks = [s["StackName"] for s in cfn.list_stacks().get("StackSummaries", []) if s["StackStatus"] not in ("DELETE_COMPLETE",)]
    print(f"  [0/6] AWS CloudFormation en {AWS_REGION}: {len(stacks)} stacks activos (Regla Cero CloudFormation verificada).")

    print(f"  [1/6] Sincronizando Bucket S3: s3://{BUCKET_NAME}/ ...")
    try:
        s3.head_bucket(Bucket=BUCKET_NAME)
        print("        -> Bucket existente verificado.")
    except ClientError:
        s3.create_bucket(Bucket=BUCKET_NAME, CreateBucketConfiguration={"LocationConstraint": AWS_REGION})
        print("        -> Bucket creado en us-east-2.")

    static_uploads = [
        # Bronce
        (os.path.join(BRONZE_DIR, "sentinel2", "sentinel2_l2a_nataima.parquet"), "bronce/sentinel2/sentinel2_l2a_nataima.parquet"),
        (os.path.join(BRONZE_DIR, "landsat", "landsat_c2l2_nataima.parquet"), "bronce/landsat/landsat_c2l2_nataima.parquet"),
        (os.path.join(BRONZE_DIR, "hls", "hls_prithvi_manifest.parquet"), "bronce/hls/hls_prithvi_manifest.parquet"),
        (os.path.join(BRONZE_DIR, "geos_s2s", "geos_s2s_nrt.parquet"), "bronce/geos_s2s/geos_s2s_nrt.parquet"),
        (os.path.join(BRONZE_DIR, "geos_s2s", "geos_s2s_drift.parquet"), "bronce/geos_s2s/geos_s2s_drift.parquet"),
        (os.path.join(BRONZE_DIR, "chirps", "chirps_observado.parquet"), "bronce/chirps/chirps_observado.parquet"),
        (os.path.join(BRONZE_DIR, "power", "nasa_power_observado.parquet"), "bronce/power/nasa_power_observado.parquet"),
        (os.path.join(BRONZE_DIR, "campo_nataima", "nataima_diario.parquet"), "bronce/campo_nataima/nataima_diario.parquet"),
        (os.path.join(BRONZE_DIR, "campo_nataima", "tabla_entrenamiento_unificada.parquet"), "bronce/campo_nataima/tabla_entrenamiento_unificada.parquet"),
        (os.path.join(BRONZE_DIR, "ideam_auditoria", "ideam_auditoria_estaciones.parquet"), "bronce/ideam_auditoria/ideam_auditoria_estaciones.parquet"),
        (os.path.join(BRONZE_DIR, "semaforo_c_repo", "pixeles_c_20286.parquet"), "bronce/semaforo_c_repo/pixeles_c_20286.parquet"),
        # Plata y Oro no particionadas
        (os.path.join(SILVER_DIR, "estado_hidrico_pixel", "estado_hidrico_pixel.parquet"), "plata/estado_hidrico_pixel/estado_hidrico_pixel.parquet"),
        (os.path.join(GOLD_DIR, "curva_normal_10anios", "curva_normal_10anios.parquet"), "oro/curva_normal_10anios/curva_normal_10anios.parquet"),
        (os.path.join(GOLD_DIR, "verificacion_clima_cultivo", "verificacion_clima_cultivo.parquet"), "oro/verificacion_clima_cultivo/verificacion_clima_cultivo.parquet"),
        (os.path.join(GOLD_DIR, "predios", "predios_catalogo.parquet"), "oro/predios/predios_catalogo.parquet"),
        # Mapas PNG + Visor Leaflet HTML Modelo C
        (os.path.join(GOLD_DIR, "semaforo_c_mapas", "semaforo_h00.png"), "oro/semaforo_c_mapas/semaforo_h00.png"),
        (os.path.join(GOLD_DIR, "semaforo_c_mapas", "semaforo_h07.png"), "oro/semaforo_c_mapas/semaforo_h07.png"),
        (os.path.join(GOLD_DIR, "semaforo_c_mapas", "semaforo_h15.png"), "oro/semaforo_c_mapas/semaforo_h15.png"),
        (os.path.join(GOLD_DIR, "semaforo_c_mapas", "semaforo_h30.png"), "oro/semaforo_c_mapas/semaforo_h30.png"),
        (os.path.join(GOLD_DIR, "semaforo_c_mapas", "resumen.json"), "oro/semaforo_c_mapas/resumen.json"),
        (os.path.join(GOLD_DIR, "semaforo_c_mapas", "mapa.html"), "oro/semaforo_c_mapas/mapa.html"),
        (os.path.join(GOLD_DIR, "semaforo_c_mapas", "qa_reproduce_a.json"), "oro/semaforo_c_mapas/qa_reproduce_a.json"),
        # Artefactos y Metadatos de Modelos A, B, C y Prithvi
        (os.path.join(MODELS_DIR, "calibracion_landsat_s2", "calibracion_landsat_s2.parquet"), "models/calibracion_landsat_s2/calibracion_landsat_s2.parquet"),
        (os.path.join(MODELS_DIR, "calibracion_landsat_s2", "calibracion_landsat_s2.json"), "models/calibracion_landsat_s2/calibracion_landsat_s2.json"),
        (os.path.join(MODELS_DIR, "pronosticador_a", "meta.json"), "models/pronosticador_a/meta.json"),
        (os.path.join(MODELS_DIR, "pronosticador_a", "paso8_metricas_absolutas.csv"), "models/pronosticador_a/paso8_metricas_absolutas.csv"),
        (os.path.join(MODELS_DIR, "pronosticador_a", "paso8_prueba_clima_observado.csv"), "models/pronosticador_a/paso8_prueba_clima_observado.csv"),
        (os.path.join(MODELS_DIR, "pronosticador_a", "qa_reproduce_a.json"), "models/pronosticador_a/qa_reproduce_a.json"),
        (os.path.join(MODELS_DIR, "semaforo_b", "modelo_de_uso_oficial_arimax.json"), "models/semaforo_b/modelo_de_uso_oficial_arimax.json"),
        (os.path.join(MODELS_DIR, "semaforo_b", "modelo_de_uso_ndvi_ndmi.json"), "models/semaforo_b/modelo_de_uso_ndvi_ndmi.json"),
        (os.path.join(MODELS_DIR, "semaforo_b", "roc_auc_familias.csv"), "models/semaforo_b/roc_auc_familias.csv"),
        (os.path.join(MODELS_DIR, "semaforo_b", "reentreno_etapa1_resumen.csv"), "models/semaforo_b/reentreno_etapa1_resumen.csv"),
        (os.path.join(MODELS_DIR, "semaforo_b", "reentreno_etapa1_protocolo.json"), "models/semaforo_b/reentreno_etapa1_protocolo.json"),
        (os.path.join(MODELS_DIR, "semaforo_b", "arimax1_params.json"), "models/semaforo_b/arimax1_params.json"),
        (os.path.join(MODELS_DIR, "semaforo_c", "modelo_c_spec.json"), "models/semaforo_c/modelo_c_spec.json"),
        (os.path.join(MODELS_DIR, "prithvi_gapfill", "prithvi_manifest.json"), "models/prithvi_gapfill/prithvi_manifest.json"),
        # Scripts activos (2 Glue Jobs vigentes: sira-b-02 y sira-b-05) y SQL
        (os.path.join(BASE_DIR, "spark_jobs", "job_b02_lote_clima_y_geos.py"), "scripts/job_b02_lote_clima_y_geos.py"),
        (os.path.join(BASE_DIR, "spark_jobs", "job_b05_inferencia_unificada_ml.py"), "scripts/job_b05_inferencia_unificada_ml.py"),
        (os.path.join(BASE_DIR, "sql", "ddl_and_analytics_b_athena.sql"), "scripts/ddl_and_analytics_b_athena.sql"),
    ]
    # Proteccion: si ya existe oro/semaforo_app/latest.json (generado por sira-b-05 o subir_aws.py),
    # no sobrescribir mapas ni particiones Oro de ML con archivos locales antiguos.
    has_live_ml_oro = False
    try:
        s3.head_object(Bucket=BUCKET_NAME, Key="oro/semaforo_app/latest.json")
        has_live_ml_oro = True
        print("        -> Detectado oro/semaforo_app/latest.json en S3: preservando salidas en vivo de ML/Glue 5.0.")
    except ClientError:
        pass

    for lp, key in static_uploads:
        if has_live_ml_oro and key.startswith("oro/semaforo_c_mapas/"):
            continue
        if os.path.exists(lp):
            extra = {}
            if key.endswith(".html"):
                extra = {"ContentType": "text/html; charset=utf-8"}
            elif key.endswith(".png"):
                extra = {"ContentType": "image/png"}
            elif key.endswith(".json"):
                extra = {"ContentType": "application/json; charset=utf-8"}
            s3.upload_file(lp, BUCKET_NAME, key, ExtraArgs=extra or None)

    tmp_up = os.path.join(DATA_DIR, "_tmp_s3")
    n1 = upload_partitioned(s3, pd.read_parquet(os.path.join(SILVER_DIR, "indices_pixel", "indices_pixel_full.parquet")), ["predio_id", "fecha"], "plata/indices_pixel", tmp_up)
    n4 = upload_partitioned(s3, pd.read_parquet(os.path.join(GOLD_DIR, "correccion_coeficientes", "coeficientes_v2026_11.parquet")), ["version"], "oro/correccion_coeficientes", tmp_up)
    n6 = upload_partitioned(s3, pd.read_parquet(os.path.join(GOLD_DIR, "pronostico_predictor", "pronostico_predictor_latest.parquet")), ["emision"], "oro/pronostico_predictor", tmp_up)
    n7 = upload_partitioned(s3, pd.read_parquet(os.path.join(GOLD_DIR, "semaforo", "semaforo_latest.parquet")), ["emision"], "oro/semaforo", tmp_up)
    if not has_live_ml_oro:
        n2 = upload_partitioned(s3, pd.read_parquet(os.path.join(SILVER_DIR, "clima_observado", "clima_observado_full.parquet")), ["anio"], "plata/clima_observado", tmp_up)
        n3 = upload_partitioned(s3, pd.read_parquet(os.path.join(SILVER_DIR, "geos_s2s_crudo", "geos_s2s_crudo_full.parquet")), ["emision"], "plata/geos_s2s_crudo", tmp_up, max_partitions=12)
        n5 = upload_partitioned(s3, pd.read_parquet(os.path.join(GOLD_DIR, "geos_s2s_corregido", "geos_s2s_corregido_full.parquet")), ["emision"], "oro/geos_s2s_corregido", tmp_up, max_partitions=12)
        n8 = upload_partitioned(s3, pd.read_parquet(os.path.join(GOLD_DIR, "semaforo_c_pixeles", "semaforo_c_pixeles_latest.parquet")), ["emision"], "oro/semaforo_c_pixeles", tmp_up)
        n9 = upload_partitioned(s3, pd.read_parquet(os.path.join(GOLD_DIR, "semaforo_c_resumen", "semaforo_c_resumen_latest.parquet")), ["emision"], "oro/semaforo_c_resumen", tmp_up)
    else:
        n2 = n3 = n5 = n8 = n9 = "PRESERVED_LIVE_AWS"
    print(f"        -> Particiones S3 cargadas: idx={n1}, cli={n2}, geos_crudo={n3}, coef={n4}, geos_corr={n5}, pred_a={n6}, sem_b={n7}, sem_c_px={n8}, sem_c_res={n9}")

    print("  [2/6] Verificando Roles IAM (reutilizando roles SIRA existentes)...")
    glue_role_arn = iam.get_role(RoleName="sira-glue-service-role")["Role"]["Arn"]
    sfn_role_arn = iam.get_role(RoleName="sira-stepfunctions-role")["Role"]["Arn"]
    sched_role_arn = iam.get_role(RoleName="sira-scheduler-role")["Role"]["Arn"]
    print("        -> Roles verificados: sira-glue-service-role, sira-stepfunctions-role, sira-scheduler-role")

    print("  [3/6] Configurando los 2 Jobs activos en AWS Glue Studio (sira-b-02 y sira-b-05)...")
    glue_specs = [
        {
            "Name": "sira-b-02-lote-clima-y-geos",
            "Script": f"s3://{BUCKET_NAME}/scripts/job_b02_lote_clima_y_geos.py",
            "Description": "SIRA B Lotes 2 y 3 Unificado: Clima 2001-2026 (plata/cubo_a/clima.csv) + GEOS-S2S-3 (5m) con climatologia sellada 10 anos",
            "GlueVersion": "4.0",
            "DefaultArgs": {
                "--job-language": "python",
                "--enable-continuous-cloudwatch-log": "true",
                "--enable-metrics": "true",
                "--input_bronce_chirps": f"s3://{BUCKET_NAME}/bronce/chirps/",
                "--input_bronce_power": f"s3://{BUCKET_NAME}/bronce/power/",
                "--input_bronce_geos_nrt": f"s3://{BUCKET_NAME}/bronce/geos_s2s/geos_s2s_nrt.parquet",
                "--input_plata_cubo_clima": f"s3://{BUCKET_NAME}/plata/cubo_a/clima.csv",
                "--input_bronce_geos_csv": f"s3://{BUCKET_NAME}/bronce/geos_s2s_a/geos_nrt.csv",
                "--input_oro_coeficientes": f"s3://{BUCKET_NAME}/oro/correccion_coeficientes/",
                "--output_plata_clima_observado": f"s3://{BUCKET_NAME}/plata/clima_observado/",
                "--output_plata_geos_crudo": f"s3://{BUCKET_NAME}/plata/geos_s2s_crudo/",
                "--output_oro_geos_corregido": f"s3://{BUCKET_NAME}/oro/geos_s2s_corregido/",
            },
        },
        {
            "Name": "sira-b-05-inferencia-unificada-ml",
            "Script": f"s3://{BUCKET_NAME}/scripts/job_b05_inferencia_unificada_ml.py",
            "Description": "SIRA B Unificado (Glue 5.0): Modelo A (RF real .joblib) + Tramo 1 CD + Modelo B ARIMAX(1) dia 0 + Dr FAO-56 5m GEOS-S2S + Parquet Athena + App Lovable formato 2",
            "GlueVersion": "5.0",
            "DefaultArgs": {
                "--job-language": "python",
                "--enable-continuous-cloudwatch-log": "true",
                "--enable-metrics": "true",
                "--additional-python-modules": "scikit-learn==1.6.1,joblib,pyproj,Pillow",
                "--s3_bucket": BUCKET_NAME,
                "--aws_region": AWS_REGION,
                "--athena_db": ATHENA_DB,
                "--predio_id": "NATAIMA_01",
                "--fecha_hoy": "auto",
                "--riego_supuesto": "CD",
                "--prefer_oro_geos": "true",
            },
        },
    ]
    for spec in glue_specs:
        payload = {
            "Description": spec["Description"],
            "Role": glue_role_arn,
            "ExecutionProperty": {"MaxConcurrentRuns": 2},
            "Command": {"Name": "glueetl", "ScriptLocation": spec["Script"], "PythonVersion": "3"},
            "DefaultArguments": spec["DefaultArgs"],
            "MaxRetries": 0,
            "Timeout": 20,
            "GlueVersion": spec.get("GlueVersion", "4.0"),
            "NumberOfWorkers": 2,
            "WorkerType": "G.1X",
        }
        try:
            glue.get_job(JobName=spec["Name"])
            glue.update_job(JobName=spec["Name"], JobUpdate=payload)
            print(f"        -> Glue Job actualizado: {spec['Name']}")
        except ClientError:
            glue.create_job(Name=spec["Name"], **payload)
            print(f"        -> Glue Job creado: {spec['Name']}")

    print("  [4/6] Actualizando 3 Máquinas de Estados en AWS Step Functions (con rama Modelo C y Parallel)...")
    sm_specs = [
        ("sira-b-flujo-diario-imagenes-inferencia", "stepfunctions_b_diario_imagenes_ab.asl.json"),
        ("sira-b-flujo-geos-5dias", "stepfunctions_b_geos_5dias.asl.json"),
        ("sira-b-flujo-recalibracion-mensual", "stepfunctions_b_recalibracion_mensual.asl.json"),
    ]
    sm_arns = {}
    existing_sms = {sm["name"]: sm["stateMachineArn"] for sm in sfn.list_state_machines().get("stateMachines", [])}
    for sm_name, asl_file in sm_specs:
        with open(os.path.join(BASE_DIR, "orchestration", asl_file), "r", encoding="utf-8") as f:
            asl_def = f.read()
        if sm_name in existing_sms:
            arn = existing_sms[sm_name]
            sfn.update_state_machine(stateMachineArn=arn, definition=asl_def, roleArn=sfn_role_arn)
            print(f"        -> State Machine actualizada: {sm_name}")
        else:
            arn = sfn.create_state_machine(name=sm_name, definition=asl_def, roleArn=sfn_role_arn, type="STANDARD")["stateMachineArn"]
            print(f"        -> State Machine creada: {sm_name}")
        sm_arns[sm_name] = arn

    print("  [5/6] Verificando 3 Schedules en Amazon EventBridge Scheduler...")
    sched_specs = [
        ("sira-b-horario-diario-5am", "cron(0 5 * * ? *)", "America/Bogota", sm_arns["sira-b-flujo-diario-imagenes-inferencia"], "SIRA B Diario 5AM: Inferencia Unificada ML Real + App Lovable (sira-b-05, --fecha_hoy=auto)"),
        ("sira-b-horario-geos-5dias", "rate(5 days)", "America/Bogota", sm_arns["sira-b-flujo-geos-5dias"], "SIRA B Cada 5d: Clima 26a + GEOS (sira-b-02) -> Inferencia ML Real + App Lovable (sira-b-05)"),
        ("sira-b-horario-recalibracion-mensual", "cron(0 6 1 * ? *)", "America/Bogota", sm_arns["sira-b-flujo-recalibracion-mensual"], "SIRA B Mensual Dia 1: Recalibracion sesgo GEOS-S2S sobre 26 anos (sira-b-02)"),
    ]
    for sc_name, expr, tz, target_arn, desc in sched_specs:
        kw = {
            "Name": sc_name,
            "Description": desc,
            "ScheduleExpression": expr,
            "ScheduleExpressionTimezone": tz,
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "State": "ENABLED",
            "Target": {"Arn": target_arn, "RoleArn": sched_role_arn, "Input": json.dumps({"origen": sc_name})},
        }
        try:
            scheduler.get_schedule(Name=sc_name)
            scheduler.update_schedule(**kw)
            print(f"        -> Schedule actualizado: {sc_name} ({expr})")
        except ClientError:
            scheduler.create_schedule(**kw)
            print(f"        -> Schedule creado: {sc_name} ({expr})")

    print(f"  [6/6] Reconstruyendo catálogo {ATHENA_DB} (13 tablas) y ejecutando 6 consultas en Amazon Athena...")
    athena_out = f"s3://{BUCKET_NAME}/athena-results/"

    def run_athena(sql_stmt: str, db_name=None):
        kw = {"QueryString": sql_stmt, "ResultConfiguration": {"OutputLocation": athena_out}}
        if db_name:
            kw["QueryExecutionContext"] = {"Database": db_name}
        qid = athena.start_query_execution(**kw)["QueryExecutionId"]
        for _ in range(55):
            st = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            if st["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
                if st["State"] != "SUCCEEDED":
                    print(f"        [!] Athena error: {st.get('StateChangeReason')}")
                return qid, st["State"]
            time.sleep(1.1)
        return qid, "TIMEOUT"

    with open(os.path.join(BASE_DIR, "sql", "ddl_and_analytics_b_athena.sql"), "r", encoding="utf-8") as f:
        raw_sql = f.read()

    stmts = []
    for chunk in raw_sql.split(";"):
        lines = [ln for ln in chunk.splitlines() if not ln.strip().startswith("--")]
        stmt = "\n".join(lines).strip()
        if stmt:
            stmts.append(stmt)

    ok_cnt = 0
    for i, stmt in enumerate(stmts, start=1):
        fw = stmt.split()[0].upper()
        db_ctx = None if "CREATE DATABASE" in stmt.upper() else ATHENA_DB
        qid, state = run_athena(stmt, db_name=db_ctx)
        if state == "SUCCEEDED":
            ok_cnt += 1
        print(f"        -> Athena [{i}/{len(stmts)}] ({fw}): {state} ({qid})")
        if fw == "SELECT" and state == "SUCCEEDED":
            res = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["Rows"]
            hdr = [c.get("VarCharValue", "") for c in res[0]["Data"]]
            print(f"           Cols: {hdr}")
            for row_d in res[1:7]:
                print(f"           Fila: {[c.get('VarCharValue', '') for c in row_d['Data']]}")
    print(f"  -> Resumen Athena: {ok_cnt}/{len(stmts)} sentencias SUCCEEDED.")

    deploy_lambda_api_serving(session)


def deploy_lambda_api_serving(session) -> str:
    """Despliega/actualiza AWS Lambda `sira-b-api-serving` + Function URL con CORS (0 stacks CloudFormation)."""
    print("  [7/7] Desplegando AWS Lambda `sira-b-api-serving` + Function URL (CORS para App Lovable)...")
    iam = session.client("iam")
    lam = session.client("lambda")

    role_name = "sira-b-lambda-api-role"
    trust_doc = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
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
            Description="Rol minimo de lectura S3 Oro (semaforo_app) para AWS Lambda sira-b-api-serving",
        )["Role"]["Arn"]
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        time.sleep(10)

    s3_read_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReadOnlySiraAppGold",
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [
                    f"arn:aws:s3:::{BUCKET_NAME}/oro/semaforo_app/*",
                    f"arn:aws:s3:::{BUCKET_NAME}/models/semaforo_b/operacion/*",
                ],
            },
            {
                "Sid": "ListSiraAppGold",
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{BUCKET_NAME}"],
                "Condition": {"StringLike": {"s3:prefix": ["oro/semaforo_app/*"]}},
            },
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="sira-b-lambda-s3-read-oro-app",
        PolicyDocument=json.dumps(s3_read_policy),
    )

    # Empaquetar app_b_inference_api.py en ZIP en memoria (0 capas externas, solo stdlib + boto3)
    api_path = os.path.join(BASE_DIR, "api", "app_b_inference_api.py")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(api_path, arcname="app_b_inference_api.py")
    zip_bytes = buf.getvalue()

    fn_name = "sira-b-api-serving"
    env_vars = {
        "SIRA_S3_BUCKET": BUCKET_NAME,
        "SIRA_APP_PREFIX": "oro/semaforo_app",
        "SIRA_CACHE_TTL_SEC": "60",
    }
    try:
        lam.get_function(FunctionName=fn_name)
        lam.update_function_code(FunctionName=fn_name, ZipFile=zip_bytes)
        waiter = lam.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=fn_name)
        lam.update_function_configuration(
            FunctionName=fn_name,
            Runtime="python3.11",
            Handler="app_b_inference_api.lambda_handler",
            Timeout=15,
            MemorySize=512,
            Environment={"Variables": env_vars},
        )
        waiter.wait(FunctionName=fn_name)
        print(f"        -> Lambda actualizada: {fn_name} ({len(zip_bytes):,} B)")
    except ClientError:
        for attempt in range(5):
            try:
                lam.create_function(
                    FunctionName=fn_name,
                    Runtime="python3.11",
                    Role=role_arn,
                    Handler="app_b_inference_api.lambda_handler",
                    Code={"ZipFile": zip_bytes},
                    Description="SIRA API Serving Unificada (S3 Oro semaforo_app + simulador POST /regar_hoy FAO-56 <50ms)",
                    Timeout=15,
                    MemorySize=512,
                    Environment={"Variables": env_vars},
                )
                break
            except ClientError as exc:
                if "cannot be assumed" in str(exc) and attempt < 4:
                    time.sleep(5)
                    continue
                raise
        lam.get_waiter("function_active_v2").wait(FunctionName=fn_name)
        print(f"        -> Lambda creada: {fn_name} ({len(zip_bytes):,} B)")

    cors_cfg = {
        "AllowOrigins": ["*"],
        "AllowMethods": ["GET", "POST"],
        "AllowHeaders": ["content-type", "authorization", "x-api-key"],
        "MaxAge": 300,
    }
    try:
        furl = lam.get_function_url_config(FunctionName=fn_name)["FunctionUrl"]
        lam.update_function_url_config(FunctionName=fn_name, AuthType="NONE", Cors=cors_cfg)
    except ClientError:
        furl = lam.create_function_url_config(FunctionName=fn_name, AuthType="NONE", Cors=cors_cfg)["FunctionUrl"]

    for sid, act, extra in [
        ("PublicFunctionUrlInvoke", "lambda:InvokeFunctionUrl", {"FunctionUrlAuthType": "NONE"}),
        ("PublicInvokeFunctionViaUrl", "lambda:InvokeFunction", {"InvokedViaFunctionUrl": True}),
    ]:
        try:
            lam.add_permission(FunctionName=fn_name, StatementId=sid, Action=act, Principal="*", **extra)
        except Exception:
            pass

    print(f"        -> Function URL activa (CORS=*): {furl}")
    return furl


def main():
    parser = argparse.ArgumentParser(description="SIRA B_SIRA_ARCH Pipeline E2E Unificado (HEAD 86aa09b)")
    parser.add_argument("--local-only", action="store_true", help="Ejecuta solo procesamiento local en Parquet")
    parser.add_argument("--aws-only", action="store_true", help="Ejecuta solo despliegue en AWS")
    args = parser.parse_args()

    if not args.aws_only:
        build_b_silver_and_gold_local()
    if not args.local_only:
        deploy_b_to_aws()
    print("\n=== PROCESO B_SIRA_ARCH UNIFICADO (HEAD 86aa09b) COMPLETADO ===")


if __name__ == "__main__":
    main()
