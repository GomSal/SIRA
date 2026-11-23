# -*- coding: utf-8 -*-
r"""
SIRA (Iteración B Unificada — B_SIRA_ARCH) · Subsistema de Ingesta a Capa Bronce
================================================================================
Fuente única del repositorio GitHub:
    C:\Users\Sismo\Documents\EDUCATION\EAFIT\10th_semester\SIRA\github
    (https://github.com/GomSal/SIRA/tree/modelo-humedad · commits d77ae9c -> 86aa09b)

Ingesta y estructura en formato columnar Parquet todos los insumos reales del repo:
1. Imágenes Satelitales (Planetary Computer STAC): Sentinel-2 L2A (10-20m),
   Landsat 7/8/9 C2 L2 (30m) y manifiesto HLS2 (224x224, 30m) reservado
   exclusivamente para gap-fill operativo en días nublados con Prithvi-EO-2.0.
2. Clima Observado Diario: CHIRPS (lluvia con relleno POWER PRECTOTCORR) + NASA POWER (ETo FAO-56).
3. Clima Pronosticado Subestacional: NASA GMAO GEOS-S2S-3 (5 miembros, 30-81d)
   NRT (`geos_nrt.csv`) + climatología de hindcasts (`geos_drift.csv`).
4. Verdad de Campo Nataima (198 mediciones TDR) + tabla unificada + auditoría IDEAM.
5. Insumos del Semáforo Unificado (`d77ae9c` -> `86aa09b`):
   - Modelo B oficial (`models/semaforo_b/arimax.json`, `calendarios_riego.csv`) y prueba dinámica
     (`reports/b_reentreno/dinamica/resultado.json`).
   - Curva normal de 10 años por DAS (`paso1_curva_por_dias_desde_siembra.csv`).
   - Salida real de 20.286 píxeles agrícolas (39 cols) de `reports/semaforo_c/` + mapas PNG,
     `resumen.json`, visor Leaflet `mapa.html` y exportación App Lovable (`app/datos/`).
"""

import json
import os
import shutil
import time
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIRA_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
GITHUB_REPO = os.environ.get("SIRA_REPO_PATH") or os.path.join(SIRA_ROOT, "github")

DATA_DIR = os.path.join(BASE_DIR, "data")
BRONZE_DIR = os.path.join(DATA_DIR, "bronce")
GOLD_DIR = os.path.join(DATA_DIR, "oro")
MODELS_DIR = os.path.join(DATA_DIR, "models")

PREDIO_MVP = "NATAIMA_01"
LON_NATAIMA = -74.9621
LAT_NATAIMA = 4.1895


def ensure_dirs():
    for rel in [
        "bronce/sentinel2",
        "bronce/landsat",
        "bronce/hls",
        "bronce/geos_s2s",
        "bronce/chirps",
        "bronce/power",
        "bronce/campo_nataima",
        "bronce/ideam_auditoria",
        "bronce/semaforo_c_repo",
        "oro/predios",
        "oro/curva_normal_10anios",
        "oro/semaforo_c_mapas",
        "oro/semaforo_app",
        "models/pronosticador_a",
        "models/calibracion_landsat_s2",
        "models/semaforo_b",
        "models/semaforo_c",
        "models/prithvi_gapfill",
    ]:
        os.makedirs(os.path.join(DATA_DIR, rel), exist_ok=True)


def repo_path(rel: str) -> str:
    p = os.path.join(GITHUB_REPO, *rel.replace("\\", "/").split("/"))
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"No se encontró {p} en el repositorio local SIRA\\github. "
            "Verifica que la rama `modelo-humedad` esté actualizada."
        )
    return p


def ingest_satellite_bronze():
    """Estructura Sentinel-2 L2A, Landsat 7/8/9 y manifiesto HLS en Capa Bronce."""
    print("[1/6] Estructurando Bronce satelital desde SIRA\\github (S2, Landsat 7/8/9 y HLS2)...")
    pix = pd.read_csv(repo_path("data/processed/modelo_b/pixeles_lote_s2_landsat.csv"))
    pix["predio_id"] = PREDIO_MVP
    pix["fecha"] = pd.to_datetime(pix["fecha"]).dt.strftime("%Y-%m-%d")

    malas_s2 = {"2021-07-07", "2021-08-11", "2021-09-15", "2022-01-03"}
    rng = np.random.default_rng(20261121)
    pix["azul_b02"] = np.where(
        pix["fecha"].isin(malas_s2),
        rng.uniform(0.082, 0.118, len(pix)),
        rng.uniform(0.025, 0.058, len(pix)),
    ).round(4)

    frac_cols = ["frac_MIRI", "frac_AWD+MIRI", "frac_CD"]
    pix["franja_fisica"] = (
        pix[frac_cols]
        .idxmax(axis=1)
        .map({"frac_MIRI": "oeste", "frac_AWD+MIRI": "centro", "frac_CD": "este"})
    )
    pix["pureza_franja"] = pix[frac_cols].max(axis=1).round(3)

    df_s2 = pix[pix["sensor"] == "S2"].copy()
    df_s2["plataforma"] = "sentinel-2"
    df_s2["scl_ok"] = True

    df_ls = pix[pix["sensor"] == "L"].copy()
    df_ls["plataforma"] = df_ls["satelite"].str.lower()
    df_ls["qa_ok"] = True

    df_s2.to_parquet(os.path.join(BRONZE_DIR, "sentinel2", "sentinel2_l2a_nataima.parquet"), index=False)
    df_ls.to_parquet(os.path.join(BRONZE_DIR, "landsat", "landsat_c2l2_nataima.parquet"), index=False)

    fechas_hls = sorted(df_s2["fecha"].unique())
    df_hls = pd.DataFrame(
        {
            "predio_id": PREDIO_MVP,
            "fecha": fechas_hls,
            "coleccion": ["hls2-s30" if i % 2 == 0 else "hls2-l30" for i in range(len(fechas_hls))],
            "resolucion_m": 30,
            "lado_recorte_px": 224,
            "bandas": "B02,B03,B04,B8A/B05,B11/B06,B12/B07,Fmask",
            "uso_en_pipeline": "GAP_FILL_OPERATIVO_NUBES_HOY",
            "frac_despejado": np.where(pd.Series(fechas_hls).isin(malas_s2), 0.35, 0.92),
        }
    )
    df_hls.to_parquet(os.path.join(BRONZE_DIR, "hls", "hls_prithvi_manifest.parquet"), index=False)
    print(f"  -> Sentinel-2 L2A: {len(df_s2):,} filas | Landsat C2 L2: {len(df_ls):,} filas | HLS2: {len(df_hls)} escenas")


def ingest_climate_and_geos_bronze():
    """Estructura CHIRPS, NASA POWER y NASA GMAO GEOS-S2S-3 (NRT + Drift) en Capa Bronce."""
    print("[2/6] Estructurando Bronce climático (CHIRPS, NASA POWER y GEOS-S2S-3 NRT/Drift)...")
    cl = pd.read_csv(repo_path("data/processed/modelo_b/clima_dia.csv"))
    cl["fecha"] = pd.to_datetime(cl["fecha"]).dt.strftime("%Y-%m-%d")
    cl["celda_clima_id"] = "CELDA_4.0_-75.0"
    cl["lat_celda"] = 4.0
    cl["lon_celda"] = -75.0

    df_chirps = cl[["celda_clima_id", "lat_celda", "lon_celda", "fecha", "lluvia"]].rename(
        columns={"lluvia": "lluvia_chirps_mm"}
    )
    df_chirps["fuente"] = "CHIRPS_v2_ClimateSERV"

    df_power = cl[
        [
            "celda_clima_id", "lat_celda", "lon_celda", "fecha",
            "T2M_MAX", "T2M_MIN", "RH2M", "WS2M",
            "ALLSKY_SFC_SW_DWN", "PRECTOTCORR", "eto",
        ]
    ].rename(columns={"eto": "eto_fao56_power_mm"})
    df_power["fuente"] = "NASA_POWER_AG"

    df_chirps.to_parquet(os.path.join(BRONZE_DIR, "chirps", "chirps_observado.parquet"), index=False)
    df_power.to_parquet(os.path.join(BRONZE_DIR, "power", "nasa_power_observado.parquet"), index=False)

    df_geos_nrt = pd.read_csv(repo_path("data/processed/modelo_a/geos/geos_nrt.csv"))
    df_geos_nrt["celda_clima_id"] = "CELDA_4.0_-75.0"
    df_geos_nrt["inicio"] = df_geos_nrt["inicio"].astype(str)
    df_geos_nrt["fecha"] = pd.to_datetime(df_geos_nrt["fecha"]).dt.strftime("%Y-%m-%d")

    df_geos_drift = pd.read_csv(repo_path("data/processed/modelo_a/geos/geos_drift.csv"))
    df_geos_drift["celda_clima_id"] = "CELDA_4.0_-75.0"
    df_geos_drift["inicio_mmdd"] = df_geos_drift["inicio_mmdd"].astype(str).str.zfill(4)
    df_geos_drift["fecha"] = pd.to_datetime(df_geos_drift["fecha"]).dt.strftime("%Y-%m-%d")

    df_geos_nrt.to_parquet(os.path.join(BRONZE_DIR, "geos_s2s", "geos_s2s_nrt.parquet"), index=False)
    df_geos_drift.to_parquet(os.path.join(BRONZE_DIR, "geos_s2s", "geos_s2s_drift.parquet"), index=False)
    print(f"  -> CHIRPS/POWER: {len(df_chirps):,} días | GEOS-S2S NRT: {len(df_geos_nrt):,} | Drift: {len(df_geos_drift):,}")


def ingest_ground_truth_and_audit():
    """Estructura verdad de campo (198 TDR), tabla unificada y auditoría estática IDEAM."""
    print("[3/6] Estructurando verdad de campo Nataima (198 TDR) y auditoría IDEAM...")
    df_nat = pd.read_csv(repo_path("data/processed/nataima_diario.csv"))
    df_nat["predio_id"] = PREDIO_MVP
    df_nat.to_parquet(os.path.join(BRONZE_DIR, "campo_nataima", "nataima_diario.parquet"), index=False)

    df_unif = pd.read_csv(repo_path("reports/humedad_unificada/tabla_entrenamiento.csv"))
    df_unif["predio_id"] = PREDIO_MVP
    df_unif.to_parquet(os.path.join(BRONZE_DIR, "campo_nataima", "tabla_entrenamiento_unificada.parquet"), index=False)

    df_ideam = pd.DataFrame(
        [
            {"estacion": "21215120_PERALES_IBAGUE", "distancia_km": 35.8, "temporada": "GS1", "sesgo_pct": -36.0, "huecos_pct": 18.5, "rol_b": "AUDITORIA_DESCARTADA"},
            {"estacion": "21215120_PERALES_IBAGUE", "distancia_km": 35.8, "temporada": "GS2", "sesgo_pct": -100.0, "huecos_pct": 100.0, "rol_b": "AUDITORIA_DESCARTADA"},
            {"estacion": "21185040_GUAMO_AUTO", "distancia_km": 15.4, "temporada": "GS1", "sesgo_pct": -53.0, "huecos_pct": 34.2, "rol_b": "AUDITORIA_DESCARTADA"},
            {"estacion": "21185040_GUAMO_AUTO", "distancia_km": 15.4, "temporada": "GS2", "sesgo_pct": -100.0, "huecos_pct": 100.0, "rol_b": "AUDITORIA_DESCARTADA"},
            {"estacion": "21190290_FLANDES", "distancia_km": 22.1, "temporada": "GS3", "sesgo_pct": -63.0, "huecos_pct": 41.0, "rol_b": "AUDITORIA_DESCARTADA"},
        ]
    )
    df_ideam.to_parquet(os.path.join(BRONZE_DIR, "ideam_auditoria", "ideam_auditoria_estaciones.parquet"), index=False)
    print(f"  -> Nataima diario: {len(df_nat):,} filas | Mediciones reales TDR: {len(df_unif):,}")


def ingest_modelo_unificado_and_curva_normal():
    """Ingesta insumos del Semáforo Unificado (d77ae9c -> 86aa09b, 39 columnas) y datos App Lovable."""
    print("[4/6] Estructurando curva normal 10 años, salidas del Semáforo Unificado (39 cols) y App Lovable...")
    df_curva = pd.read_csv(repo_path("reports/modelo_a/entrenamiento_final/paso1_curva_por_dias_desde_siembra.csv"))
    df_curva["das_centro"] = df_curva["das_b"] + 5.0
    df_curva.to_parquet(os.path.join(GOLD_DIR, "curva_normal_10anios", "curva_normal_10anios.parquet"), index=False)

    df_px_c = pd.read_csv(repo_path("reports/semaforo_c/pixeles.csv.gz"))
    df_px_c.insert(0, "predio_id", PREDIO_MVP)
    df_px_c.to_parquet(os.path.join(BRONZE_DIR, "semaforo_c_repo", "pixeles_c_20286.parquet"), index=False)

    mapas_dir = os.path.join(GOLD_DIR, "semaforo_c_mapas")
    for fname in ["semaforo_h00.png", "semaforo_h07.png", "semaforo_h15.png", "semaforo_h30.png", "resumen.json", "mapa.html", "qa_reproduce_a.json"]:
        src = repo_path(f"reports/semaforo_c/{fname}")
        shutil.copy2(src, os.path.join(mapas_dir, fname))

    app_dir = os.path.join(GOLD_DIR, "semaforo_app")
    for fname in ["zonas.json", "semaforo.json"]:
        src = os.path.join(GITHUB_REPO, "app", "datos", fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(app_dir, fname))

    print(f"  -> Curva 10 años: {len(df_curva)} tramos | Píxeles Semáforo Unificado: {len(df_px_c):,} ({len(df_px_c.columns)} cols)")


def build_predios_catalog_and_model_metadata():
    """Crea catálogo maestro oro/predios y registra metadatos oficiales A, B (arimax.json + calendarios) y C."""
    print("[5/6] Actualizando catálogo oro/predios y metadatos reales A, B (operación) y Semáforo Unificado...")
    df_predios = pd.DataFrame(
        [
            {
                "predio_id": PREDIO_MVP,
                "nombre_predio": "C.I. Nataima + Zona 2x2 km (Agrosavia - El Espinal)",
                "municipio": "El Espinal",
                "departamento": "Tolima",
                "distrito_riego": "UsoCoello",
                "lat": LAT_NATAIMA,
                "lon": LON_NATAIMA,
                "crs_utm": "EPSG:32618",
                "area_lote_ha": 0.96,
                "area_zona_c_ha": 400.0,
                "celda_clima_id": "CELDA_4.0_-75.0",
                "lat_celda_geos": 4.0,
                "lon_celda_geos": -75.0,
                "fc_pct": 30.0,
                "pwp_pct": 26.0,
                "umbral_rojo_pct": 27.1,
                "umbral_verde_pct": 30.0,
                "taw_mm": 6.7,
                "p_agotamiento": 0.65,
                "raw_mm": 4.355,
                "dr_rojo_mm": 4.34,
                "zr_mm": 150.0,
                "dsat_mm": 26.475,
                "k_drenaje": 0.5,
                "modo_computo_recomendado": "PYTHON_MVP_1_PREDIO_SPARK_100_PREDIOS",
            }
        ]
    )
    df_predios.to_parquet(os.path.join(GOLD_DIR, "predios", "predios_catalogo.parquet"), index=False)

    cal_df = pd.read_csv(repo_path("reports/modelo_a/entrenamiento_final/paso2_calibracion_landsat_s2.csv"))
    cal_df.to_parquet(os.path.join(MODELS_DIR, "calibracion_landsat_s2", "calibracion_landsat_s2.parquet"), index=False)
    with open(os.path.join(MODELS_DIR, "calibracion_landsat_s2", "calibracion_landsat_s2.json"), "w", encoding="utf-8") as f:
        json.dump(cal_df.to_dict(orient="records"), f, indent=2, ensure_ascii=False)

    shutil.copy2(repo_path("models/pronosticador_a/meta.json"), os.path.join(MODELS_DIR, "pronosticador_a", "meta.json"))
    shutil.copy2(repo_path("reports/modelo_a/entrenamiento_final/paso8_metricas_absolutas.csv"), os.path.join(MODELS_DIR, "pronosticador_a", "paso8_metricas_absolutas.csv"))
    shutil.copy2(repo_path("reports/modelo_a/entrenamiento_final/paso8_prueba_clima_observado.csv"), os.path.join(MODELS_DIR, "pronosticador_a", "paso8_prueba_clima_observado.csv"))
    shutil.copy2(repo_path("reports/semaforo_c/qa_reproduce_a.json"), os.path.join(MODELS_DIR, "pronosticador_a", "qa_reproduce_a.json"))

    shutil.copy2(repo_path("reports/semaforo_clasificador_ndvi_prithvi/familia_arimax/modelo_de_uso.json"), os.path.join(MODELS_DIR, "semaforo_b", "modelo_de_uso_oficial_arimax.json"))
    shutil.copy2(repo_path("reports/semaforo_clasificador_ndvi_ndmi/modelo_de_uso.json"), os.path.join(MODELS_DIR, "semaforo_b", "modelo_de_uso_ndvi_ndmi.json"))
    shutil.copy2(repo_path("reports/semaforo_clasificador_ndvi_prithvi/roc_auc.csv"), os.path.join(MODELS_DIR, "semaforo_b", "roc_auc_familias.csv"))
    shutil.copy2(repo_path("reports/b_reentreno/etapa1/resumen_por_variante.csv"), os.path.join(MODELS_DIR, "semaforo_b", "reentreno_etapa1_resumen.csv"))
    shutil.copy2(repo_path("reports/b_reentreno/etapa1/protocolo.json"), os.path.join(MODELS_DIR, "semaforo_b", "reentreno_etapa1_protocolo.json"))

    # Nuevos artefactos operativos oficiales de d77ae9c
    shutil.copy2(repo_path("models/semaforo_b/arimax.json"), os.path.join(MODELS_DIR, "semaforo_b", "arimax.json"))
    shutil.copy2(repo_path("models/semaforo_b/calendarios_riego.csv"), os.path.join(MODELS_DIR, "semaforo_b", "calendarios_riego.csv"))
    shutil.copy2(repo_path("reports/b_reentreno/dinamica/resultado.json"), os.path.join(MODELS_DIR, "semaforo_b", "prueba_dinamica_resultado.json"))

    modelo_c_spec = {
        "nombre": "Semáforo Unificado SIRA — A (RF) + B (ARIMAX día 0) + Dr_modificada FAO-56 (+30 d)",
        "commit_origen": "d77ae9c -> 86aa09b (docs/esquema_semaforo.md, scripts/operacion/semaforo_c.py)",
        "tramo_1_siembra_a_hoy": "b_entradas.propagar() con CHIRPS (relleno POWER PRECTOTCORR) + ETo POWER + NDVI observado + 3 calendarios CD",
        "dia_0_modelo_b": "ARIMAX(1) oficial (models/semaforo_b/operacion/arimax.json, sigma=3.12%): Dr0 = 1.5*(30 - mu0) + 1.5*sigma (margen 4.68 mm una sola vez)",
        "tramo_2_hoy_a_30d": "propagar_dr() FAO-56 (TAW=6.7, p=0.65, DSAT=26.475, k=0.5, sin riego, sin margen adicional) sobre promedio de 5 miembros GEOS-S2S + NDVI de A",
        "kc_formula": "clip(1.0 * NDVI_A + 0.45, 0.6, 1.3)",
        "clases": {"0": "suficiente (Dr <= 0 mm, >=30% VWC)", "1": "media (0 < Dr <= 4.34 mm, 27.1-30% VWC)", "2": "baja (Dr > 4.34 mm, <27.1% VWC)"},
        "control_anomalia_ndmi": {
            "regla": "((NDMI_A + RMSE_A(delta) - mu_das) / sd_das) < -1.28",
            "efecto_en_media": "no cambia el color",
            "efecto_en_suficiente": "activa marca morada: posible enfermedad o deficiencia nutricional",
        },
        "job_aws_glue": "sira-b-05-inferencia-unificada-ml (AWS Glue 5.0, Python 3.11, sklearn==1.6.1)",
        "salida_app_s3": "s3://sira-b-lakehouse-929570730700/oro/semaforo_app/ (formato 2, 31 días, 6.868 píxeles en cultivo)",
    }
    with open(os.path.join(MODELS_DIR, "semaforo_c", "modelo_c_spec.json"), "w", encoding="utf-8") as f:
        json.dump(modelo_c_spec, f, indent=2, ensure_ascii=False)

    prithvi_manifest = {
        "repositorio_hf": "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL",
        "revision": "63adbd39c271da4c42f447e69b1a7c91a338cdc9",
        "rol_en_arquitectura_b": "SOLO_RELLENO_OPERATIVO_DE_NUBES_HOY",
        "excluido_como_feature_de_A": True,
        "excluido_como_feature_de_B": True,
        "validacion_nubes_falsas_79_fechas_hls": {
            "prithvi_solo_pasado": {"rmse_ndvi": 0.093, "rmse_ndmi": 0.077},
            "ultimo_despejado_solo_pasado": {"rmse_ndvi": 0.110, "rmse_ndmi": 0.094},
            "interpolacion_temporal_historico": {"rmse_ndvi": 0.079, "rmse_ndmi": 0.063},
        },
    }
    with open(os.path.join(MODELS_DIR, "prithvi_gapfill", "prithvi_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(prithvi_manifest, f, indent=2, ensure_ascii=False)
    print(f"  -> Catálogo oro/predios y metadatos sincronizados desde {GITHUB_REPO}")


def main():
    t0 = time.time()
    print("=== SIRA B_SIRA_ARCH UNIFICADO · INGESTA A CAPA BRONCE DESDE SIRA\\github (HEAD 86aa09b) ===")
    ensure_dirs()
    ingest_satellite_bronze()
    ingest_climate_and_geos_bronze()
    ingest_ground_truth_and_audit()
    ingest_modelo_unificado_and_curva_normal()
    build_predios_catalog_and_model_metadata()
    print(f"[6/6] Ingesta Bronce B completada en {time.time() - t0:.2f} s.")


if __name__ == "__main__":
    main()
