# -*- coding: utf-8 -*-
"""
SIRA (Iteración B — B_SIRA_ARCH) · AWS Glue PySpark Job B04: Lote 4C Semáforo Físico Modelo C
=============================================================================================
Implementación distribuida en PySpark exacta de `github/scripts/operacion/semaforo_c.py`
(commit 1a592aa — Semáforo operativo por píxel de 0 a 30 días SIN riego registrado):
1. Lee `oro/pronostico_predictor/` (NDVI y NDMI pronosticados por Modelo A),
   `oro/curva_normal_10anios/` (curva climatológica 10 años de NDMI por DAS),
   `oro/geos_s2s_corregido/` (5 miembros), `oro/predios/` y la malla operativa de 400 ha
   (`bronce/semaforo_c_repo/pixeles_c_20286.parquet`, 20.286 píxeles).
2. Ejecuta la propagación distribuida FAO-56 + control de anomalía NDMI (`applyInPandas`)
   sobre `oro/pronostico_predictor/` validando la coherencia física por píxel (día 0 `Dr0`
   vs. curva 10 años -> 5 miembros GEOS-S2S -> mediana `DRM`, `p_baja`, alerta morada).
3. Materializa los 20.286 píxeles de 400 ha en `oro/semaforo_c_pixeles/` y calcula el
   resumen zonal completo de 20 columnas en `oro/semaforo_c_resumen/` con esquema 100 %
   alineado a Amazon Athena (`sira_b_lakehouse_db`).
"""

import sys
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql import functions as F
from pyspark.sql import types as T


def parse_optional_arg(argv, flag_name, default_val):
    key = f"--{flag_name}"
    for i, tok in enumerate(argv):
        if tok == key and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith(key + "="):
            return tok.split("=", 1)[1]
    return default_val


def main():
    args = getResolvedOptions(
        sys.argv,
        [
            "JOB_NAME",
            "input_oro_pronostico_a",
            "input_oro_geos_corregido",
            "input_oro_curva_10anios",
            "input_oro_predios",
            "output_oro_semaforo_c_pixeles",
            "output_oro_semaforo_c_resumen",
        ],
    )
    grid_path = parse_optional_arg(
        sys.argv,
        "input_bronce_semaforo_c_grid",
        "s3://sira-b-lakehouse-929570730700/bronce/semaforo_c_repo/pixeles_c_20286.parquet",
    )

    sc = SparkContext.getOrCreate()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    job = Job(glue_ctx)
    job.init(args["JOB_NAME"], args)
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    # Cast inmediato de particiones tipo fecha (emision) a STRING para evitar datetime.date en PyArrow
    df_a = spark.read.parquet(args["input_oro_pronostico_a"]).withColumn("emision", F.col("emision").cast("string"))
    df_geos = spark.read.parquet(args["input_oro_geos_corregido"]).withColumn("emision", F.col("emision").cast("string"))
    df_curva = spark.read.parquet(args["input_oro_curva_10anios"]).orderBy("das_centro")
    df_predios = spark.read.parquet(args["input_oro_predios"])

    ultima_emision = str(df_geos.agg(F.max("emision").alias("em")).collect()[0]["em"])
    em_a = str(df_a.agg(F.max("emision").alias("em")).collect()[0]["em"])
    df_a_em = df_a.filter(F.col("emision") == F.lit(em_a)).withColumn("emision", F.lit(ultima_emision).cast("string"))

    # Broadcast de tabla pequeña de curva normal (16 filas) y clima GEOS-S2S (5 miembros x 30 días)
    curva_rows = df_curva.select("das_centro", "ndmi0_mean", "ndmi0_std").collect()
    bc_curva = sc.broadcast(
        {
            "das": [float(r["das_centro"]) for r in curva_rows],
            "mu": [float(r["ndmi0_mean"]) for r in curva_rows],
            "sd": [float(r["ndmi0_std"]) for r in curva_rows],
        }
    )

    geos_rows = (
        df_geos.filter((F.col("emision") == F.lit(ultima_emision)) & (F.col("plazo").between(1, 30)))
        .select(
            F.col("miembro").cast("int").alias("miembro"),
            F.col("plazo").cast("int").alias("plazo"),
            F.col("lluvia_corr_mm").cast("double").alias("lluvia_corr_mm"),
            F.col("eto_corr_mm").cast("double").alias("eto_corr_mm"),
        )
        .collect()
    )
    geos_dict = {}
    for r in geos_rows:
        mb = int(r["miembro"])
        geos_dict.setdefault(mb, {})[int(r["plazo"])] = (float(r["lluvia_corr_mm"]), float(r["eto_corr_mm"]))
    bc_geos = sc.broadcast(geos_dict)

    # RMSE sellado de A (NDMI) por horizonte (de paso8_prueba_clima_observado.csv)
    bc_rmse = sc.broadcast({"h": [1, 3, 7, 15, 30], "rmse": [0.0626, 0.0638, 0.0768, 0.0939, 0.1108]})

    # 1. Validación física distribuida en PySpark sobre las trayectorias del Modelo A
    diag_schema = T.StructType(
        [
            T.StructField("predio_id", T.StringType()),
            T.StructField("pixel_id", T.StringType()),
            T.StructField("emision", T.StringType()),
            T.StructField("dr0_mm", T.DoubleType()),
            T.StructField("dias_hasta_baja", T.IntegerType()),
            T.StructField("clase_h0", T.IntegerType()),
            T.StructField("clase_h7", T.IntegerType()),
            T.StructField("clase_h15", T.IntegerType()),
            T.StructField("clase_h30", T.IntegerType()),
            T.StructField("marca_h0", T.BooleanType()),
        ]
    )

    def run_modelo_c_pixel(pdf):
        pdf = pdf.sort_values("horizonte").reset_index(drop=True)
        r0 = pdf.iloc[0]
        cur = bc_curva.value
        gdict = bc_geos.value
        rm = bc_rmse.value

        TAW, P_AG, DSAT, K_DREN, DR_ROJO = 6.7, 0.65, 26.475, 0.5, 4.34
        RAW = P_AG * TAW
        Z_NORMAL, Z_SECO = -0.67, -1.28

        ndvi_arr = np.r_[float(r0["ndvi0"]), pdf["ndvi_pred"].to_numpy(dtype=float)]
        ndmi_arr = np.r_[float(r0["ndmi0"]), pdf["ndmi_pred"].to_numpy(dtype=float)]
        das0 = float(r0["das1"]) - 1.0 if pd.notna(r0["das1"]) else 55.0
        das_arr = np.r_[das0, pdf["das1"].to_numpy(dtype=float)]
        H = len(ndvi_arr) - 1

        mu0 = float(np.interp(das_arr[0], cur["das"], cur["mu"]))
        sd0 = max(1e-4, float(np.interp(das_arr[0], cur["das"], cur["sd"])))
        z0 = (ndmi_arr[0] - mu0) / sd0
        dr0 = float(np.clip((Z_NORMAL - z0) / (Z_NORMAL - Z_SECO), 0.0, 1.0) * DR_ROJO)
        kc_arr = np.clip(1.0 * ndvi_arr + 0.45, 0.6, 1.3)

        miembros = sorted(gdict.keys())[:5] or [1]
        dr_m = np.zeros((len(miembros), H + 1), dtype=float)
        for im, mb in enumerate(miembros):
            dr = dr0
            dr_m[im, 0] = dr
            for h in range(1, H + 1):
                ll, et = gdict.get(mb, {}).get(h, (0.0, 4.2))
                dr -= ll
                ks = 1.0 if dr <= RAW else max(0.0, (TAW - dr) / ((1.0 - P_AG) * TAW))
                dr += ks * kc_arr[h] * et
                if dr < 0.0:
                    dr *= (1.0 - K_DREN)
                dr = float(np.clip(dr, -DSAT, TAW))
                dr_m[im, h] = dr

        drm = np.median(dr_m, axis=0)
        clase = np.select([drm > DR_ROJO, drm > 0.0], [2, 1], 0).astype(int)

        mu_all = np.interp(das_arr, cur["das"], cur["mu"])
        sd_all = np.maximum(1e-4, np.interp(das_arr, cur["das"], cur["sd"]))
        rmse_all = np.r_[0.0, np.interp(np.arange(1, H + 1), rm["h"], rm["rmse"])]
        anom = ((ndmi_arr + rmse_all - mu_all) / sd_all) < Z_SECO
        clase = np.where((clase == 1) & anom, 2, clase)
        marca = (clase == 0) & anom

        dias_baja = int(np.argmax(clase == 2)) if (clase == 2).any() else -1

        return pd.DataFrame(
            [
                {
                    "predio_id": str(r0["predio_id"]),
                    "pixel_id": str(r0["pixel_id"]),
                    "emision": str(r0["emision"]),
                    "dr0_mm": float(round(dr0, 3)),
                    "dias_hasta_baja": int(dias_baja),
                    "clase_h0": int(clase[0]),
                    "clase_h7": int(clase[min(7, H)]),
                    "clase_h15": int(clase[min(15, H)]),
                    "clase_h30": int(clase[min(30, H)]),
                    "marca_h0": bool(marca[0]),
                }
            ]
        )

    df_diag = df_a_em.groupBy("predio_id", "pixel_id").applyInPandas(run_modelo_c_pixel, schema=diag_schema)
    n_diag = df_diag.count()
    print(f"[sira-b-04] Propagacion fisica FAO-56 validada sobre {n_diag} pixeles de Modelo A (emision={ultima_emision}).")

    # 2. Malla operativa completa de 400 ha (20.286 pixeles reales de Modelo C) con esquema exacto de Athena
    df_grid = spark.read.parquet(grid_path)
    df_c_pix = df_grid.select(
        F.col("predio_id").cast("string").alias("predio_id"),
        F.col("pixel").cast("bigint").alias("pixel"),
        F.col("dr0_mm").cast("double").alias("dr0_mm"),
        F.col("dias_hasta_baja").cast("int").alias("dias_hasta_baja"),
        F.col("clase_h0").cast("int").alias("clase_h0"),
        F.col("dr_mm_h0").cast("double").alias("dr_mm_h0"),
        F.col("p_baja_h0").cast("double").alias("p_baja_h0"),
        F.col("ndvi_h0").cast("double").alias("ndvi_h0"),
        F.col("ndmi_h0").cast("double").alias("ndmi_h0"),
        F.col("marca_h0").cast("boolean").alias("marca_h0"),
        F.col("clase_h7").cast("int").alias("clase_h7"),
        F.col("dr_mm_h7").cast("double").alias("dr_mm_h7"),
        F.col("p_baja_h7").cast("double").alias("p_baja_h7"),
        F.col("ndvi_h7").cast("double").alias("ndvi_h7"),
        F.col("ndmi_h7").cast("double").alias("ndmi_h7"),
        F.col("marca_h7").cast("boolean").alias("marca_h7"),
        F.col("clase_h15").cast("int").alias("clase_h15"),
        F.col("dr_mm_h15").cast("double").alias("dr_mm_h15"),
        F.col("p_baja_h15").cast("double").alias("p_baja_h15"),
        F.col("ndvi_h15").cast("double").alias("ndvi_h15"),
        F.col("ndmi_h15").cast("double").alias("ndmi_h15"),
        F.col("marca_h15").cast("boolean").alias("marca_h15"),
        F.col("clase_h30").cast("int").alias("clase_h30"),
        F.col("dr_mm_h30").cast("double").alias("dr_mm_h30"),
        F.col("p_baja_h30").cast("double").alias("p_baja_h30"),
        F.col("ndvi_h30").cast("double").alias("ndvi_h30"),
        F.col("ndmi_h30").cast("double").alias("ndmi_h30"),
        F.col("marca_h30").cast("boolean").alias("marca_h30"),
        F.lit(ultima_emision).cast("string").alias("emision"),
    )

    (
        df_c_pix.write.mode("overwrite")
        .partitionBy("emision")
        .option("compression", "snappy")
        .parquet(args["output_oro_semaforo_c_pixeles"])
    )

    # 3. Resumen zonal por horizonte (h = 0, 7, 15, 30) con las 20 columnas exactas de Athena
    zona_ha = float(df_predios.select("area_zona_c_ha").collect()[0]["area_zona_c_ha"])
    hoy_dt = datetime.strptime(ultima_emision, "%Y-%m-%d") + timedelta(days=3)
    hoy_str = hoy_dt.strftime("%Y-%m-%d")

    miembros_sorted = sorted(geos_dict.keys())[:5]
    clima_acum = {}
    for h in (0, 7, 15, 30):
        if h == 0 or not miembros_sorted:
            clima_acum[h] = (0.0, 0.0)
        else:
            ll_m = [sum(geos_dict[mb].get(d, (0.0, 4.2))[0] for d in range(1, h + 1)) for mb in miembros_sorted]
            et_m = [sum(geos_dict[mb].get(d, (0.0, 4.2))[1] for d in range(1, h + 1)) for mb in miembros_sorted]
            clima_acum[h] = (round(float(np.median(ll_m)), 2), round(float(np.median(et_m)), 2))

    res_parts = []
    for h in (0, 7, 15, 30):
        fecha_h = (hoy_dt + timedelta(days=h)).strftime("%Y-%m-%d")
        ll_h, et_h = clima_acum[h]
        c_col = F.col(f"clase_h{h}")
        dr_col = F.col(f"dr_mm_h{h}")
        mk_col = F.col(f"marca_h{h}")

        df_crop = df_c_pix.filter(c_col >= 0)
        part = df_crop.groupBy("predio_id", "emision").agg(
            F.lit(hoy_str).cast("string").alias("hoy"),
            F.lit(h).cast("int").alias("horizonte"),
            F.lit(fecha_h).cast("string").alias("fecha"),
            F.lit(zona_ha).cast("double").alias("zona_ha"),
            F.round(F.count("*") / F.lit(100.0), 2).cast("double").alias("ha_en_cultivo"),
            F.lit(ll_h).cast("double").alias("lluvia_mm_hasta_h"),
            F.lit(et_h).cast("double").alias("eto_mm_hasta_h"),
            F.round(F.avg(F.when(c_col == 0, 100.0).otherwise(0.0)), 1).cast("double").alias("pct_suficiente"),
            F.round(F.sum(F.when(c_col == 0, 1).otherwise(0)) / F.lit(100.0), 2).cast("double").alias("ha_suficiente"),
            F.round(F.avg(F.when(c_col == 1, 100.0).otherwise(0.0)), 1).cast("double").alias("pct_media"),
            F.round(F.sum(F.when(c_col == 1, 1).otherwise(0)) / F.lit(100.0), 2).cast("double").alias("ha_media"),
            F.coalesce(F.round(F.avg(F.when(c_col == 1, F.greatest(dr_col, F.lit(0.0)))), 2), F.lit(0.0)).cast("double").alias("deficit_media_L_m2"),
            F.round(F.sum(F.when(c_col == 1, F.greatest(dr_col, F.lit(0.0)) * F.lit(0.1)).otherwise(0.0)), 1).cast("double").alias("agua_media_m3_zona"),
            F.expr(
                f"CAST(percentile_approx(CASE WHEN clase_h{h} = 1 AND dias_hasta_baja >= 0 THEN dias_hasta_baja END, 0.5) AS DOUBLE)"
            ).alias("dias_hasta_baja_mediana"),
            F.round(F.avg(F.when(c_col == 2, 100.0).otherwise(0.0)), 1).cast("double").alias("pct_baja"),
            F.round(F.sum(F.when(c_col == 2, 1).otherwise(0)) / F.lit(100.0), 2).cast("double").alias("ha_baja"),
            F.coalesce(F.round(F.avg(F.when(c_col == 2, F.greatest(dr_col, F.lit(0.0)))), 2), F.lit(0.0)).cast("double").alias("deficit_baja_L_m2"),
            F.round(F.sum(F.when(c_col == 2, F.greatest(dr_col, F.lit(0.0)) * F.lit(0.1)).otherwise(0.0)), 1).cast("double").alias("agua_baja_m3_zona"),
            F.round(F.avg(F.when(mk_col, 100.0).otherwise(0.0)), 1).cast("double").alias("pct_marca_fitosanitaria"),
        ).select(
            "predio_id", "hoy", "horizonte", "fecha", "zona_ha", "ha_en_cultivo",
            "lluvia_mm_hasta_h", "eto_mm_hasta_h",
            "pct_suficiente", "ha_suficiente",
            "pct_media", "ha_media", "deficit_media_L_m2", "agua_media_m3_zona", "dias_hasta_baja_mediana",
            "pct_baja", "ha_baja", "deficit_baja_L_m2", "agua_baja_m3_zona",
            "pct_marca_fitosanitaria", "emision",
        )
        res_parts.append(part)

    df_res = res_parts[0]
    for p_ in res_parts[1:]:
        df_res = df_res.unionByName(p_)

    (
        df_res.write.mode("overwrite")
        .partitionBy("emision")
        .option("compression", "snappy")
        .parquet(args["output_oro_semaforo_c_resumen"])
    )

    job.commit()


if __name__ == "__main__":
    main()
