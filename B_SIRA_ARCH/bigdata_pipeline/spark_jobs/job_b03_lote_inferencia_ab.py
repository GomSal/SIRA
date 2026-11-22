# -*- coding: utf-8 -*-
"""
SIRA (Iteración B — B_SIRA_ARCH) · AWS Glue PySpark Job B03: Lote 4A/4B Inferencia A -> B
=========================================================================================
Alineado a `GomSal/SIRA@modelo-humedad` (HEAD 8fde03d, 21-nov-2026):
1. Paso 4A (Modelo A — Predictor NDVI/NDMI h = 1..30d):
   - Recibe únicamente las 20 features espectrales y fenológicas de `plata/indices_pixel/`
     (SIN clima, SIN Prithvi). Escribe `oro/pronostico_predictor/` particionada por `emision`.
   - Soporta `--skip_modelo_a=true` para el flujo cada 5 días (`sira-b-flujo-geos-5dias`),
     donde el Modelo A NO depende del clima y se lee el pronóstico ya materializado sin
     sobrescribir `oro/pronostico_predictor/` (evita carrera S3 con `sira-b-04` en `Parallel`).
2. Paso 4B (Unión Broadcast + Balance Hídrico Recursivo con Riego Registrado):
   - Broadcast de `oro/geos_s2s_corregido/` (5 miembros, celda 55 km = 0 shuffle de píxeles).
   - Propaga desde `plata/estado_hidrico_pixel/` con `Kc(t) = clip(1.0 * ndvi_pred + 0.45, 0.6, 1.3)`:
     a) Las 7 exógenas del **Modelo B oficial (`ARIMAX(1)` sin Prithvi, commit ec84551)**:
        `S_tau20`, `S_tau5`, `Dr_k0.5`, `riego_5d`, `dias_desde_riego`, `eto_5d`, `ndvi`.
     b) Los 2 reservorios de memoria larga con alpha = 0.5 ganadores del **Reentreno B Etapa 1
        (commit 8fde03d)**: `S_tau45_a05` y `S_tau60_a05`.
3. Paso 4C (Clasificación Semáforo B `ARIMAX(1)`):
   - Evalúa probabilidades gaussianas ajustadas por frecuencia (`p_verde`, `p_amarillo`, `p_rojo`)
     y escribe `oro/semaforo/` particionada por `emision`.
   - Todos los campos string/int/double se normalizan explícitamente (`cast("string")` en particiones
     de fecha como `emision` y `fecha`) para evitar `ArrowTypeError` con `datetime.date` en PyArrow.
"""

import sys
import json
import math
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql import Window
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
            "input_plata_indices",
            "input_plata_estado",
            "input_oro_geos_corregido",
            "input_oro_predios",
            "input_arimax_params_json",
            "output_oro_pronostico_a",
            "output_oro_semaforo",
        ],
    )
    skip_modelo_a = parse_optional_arg(sys.argv, "skip_modelo_a", "false").strip().lower() == "true"

    sc = SparkContext.getOrCreate()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    job = Job(glue_ctx)
    job.init(args["JOB_NAME"], args)
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    # Cast inmediato de particiones tipo fecha (emision, fecha) a STRING para evitar datetime.date en PyArrow
    df_idx = spark.read.parquet(args["input_plata_indices"]).withColumn("fecha", F.col("fecha").cast("string"))
    df_estado = spark.read.parquet(args["input_plata_estado"])
    df_geos = spark.read.parquet(args["input_oro_geos_corregido"]).withColumn("emision", F.col("emision").cast("string"))
    df_predios = spark.read.parquet(args["input_oro_predios"])

    params_text = "\n".join(sc.textFile(args["input_arimax_params_json"]).collect())
    bc_arimax = sc.broadcast(json.loads(params_text))

    ultima_emision = str(df_geos.agg(F.max("emision").alias("em")).collect()[0]["em"])
    df_geos_em = (
        df_geos.filter((F.col("emision") == F.lit(ultima_emision)) & (F.col("plazo").between(1, 30)))
        .select(
            F.col("celda_clima_id").cast("string").alias("celda_clima_id"),
            F.col("emision").cast("string").alias("emision"),
            F.col("miembro").cast("int").alias("miembro"),
            F.col("plazo").cast("int").alias("plazo"),
            F.col("fecha_objetivo").cast("string").alias("fecha_objetivo"),
            F.col("lluvia_corr_mm").cast("double").alias("lluvia_corr_mm"),
            F.col("eto_corr_mm").cast("double").alias("eto_corr_mm"),
        )
    )

    if skip_modelo_a:
        # En el flujo cada 5 dias (Parallel con sira-b-04), NO se sobrescribe oro/pronostico_predictor
        df_a_prev = spark.read.parquet(args["output_oro_pronostico_a"]).withColumn("emision", F.col("emision").cast("string"))
        em_a = str(df_a_prev.agg(F.max("emision").alias("em")).collect()[0]["em"])
        df_a = (
            df_a_prev.filter(F.col("emision") == F.lit(em_a))
            .withColumn("emision", F.lit(ultima_emision).cast("string"))
            .withColumn("horizonte", F.col("horizonte").cast("int"))
            .withColumn("fecha_objetivo", F.date_add(F.to_date(F.lit(ultima_emision), "yyyy-MM-dd"), F.col("horizonte")).cast("string"))
            .select(
                F.col("predio_id").cast("string").alias("predio_id"),
                F.col("pixel_id").cast("string").alias("pixel_id"),
                F.col("emision").cast("string").alias("emision"),
                F.col("horizonte").cast("int").alias("horizonte"),
                F.col("fecha_objetivo").cast("string").alias("fecha_objetivo"),
                F.col("ndvi0").cast("double").alias("ndvi0"),
                F.col("ndmi0").cast("double").alias("ndmi0"),
                F.col("ndvi_pred").cast("double").alias("ndvi_pred"),
                F.col("ndmi_pred").cast("double").alias("ndmi_pred"),
                F.col("das1").cast("double").alias("das1"),
            )
        )
    else:
        w_last_px = Window.partitionBy("predio_id", "pixel_id").orderBy(F.col("fecha").desc())
        df_px_hoy = (
            df_idx.withColumn("rn", F.row_number().over(w_last_px))
            .filter(F.col("rn") == 1)
            .drop("rn")
        )

        df_h = spark.range(1, 31).select(F.col("id").cast("int").alias("horizonte"))
        df_a = (
            df_px_hoy.crossJoin(F.broadcast(df_h))
            .withColumn("emision", F.lit(ultima_emision).cast("string"))
            .withColumn("fecha_objetivo", F.date_add(F.to_date(F.lit(ultima_emision), "yyyy-MM-dd"), F.col("horizonte")).cast("string"))
            .withColumn("das1", F.least(F.coalesce("das0", F.lit(55.0)) + F.col("horizonte"), F.lit(140.0)).cast("double"))
            .withColumn("pend_ndvi_lim", F.coalesce(F.greatest(F.lit(-0.012), F.least(F.lit(0.012), F.col("pend_ndvi"))), F.lit(0.0)))
            .withColumn("pend_ndmi_lim", F.coalesce(F.greatest(F.lit(-0.010), F.least(F.lit(0.010), F.col("pend_ndmi"))), F.lit(0.0)))
            .withColumn("ndvi_clim_target", F.lit(0.76) * F.exp(-F.pow((F.col("das1") - F.lit(60.0)) / F.lit(48.0), 2)))
            .withColumn("w_decay", F.exp(-F.col("horizonte") / F.lit(14.0)))
            .withColumn(
                "ndvi_pred",
                F.round(
                    F.greatest(
                        F.lit(0.12),
                        F.least(
                            F.lit(0.95),
                            F.col("w_decay") * (F.col("ndvi0") + F.col("pend_ndvi_lim") * F.least(F.col("horizonte"), F.lit(7)))
                            + (F.lit(1.0) - F.col("w_decay")) * F.col("ndvi_clim_target"),
                        ),
                    ),
                    4,
                ).cast("double"),
            )
            .withColumn(
                "ndmi_pred",
                F.round(
                    F.greatest(
                        F.lit(-0.35),
                        F.least(
                            F.lit(0.65),
                            F.col("w_decay") * (F.col("ndmi0") + F.col("pend_ndmi_lim") * F.least(F.col("horizonte"), F.lit(7)))
                            + (F.lit(1.0) - F.col("w_decay")) * (F.lit(0.45) * F.col("ndvi_clim_target") - F.lit(0.05)),
                        ),
                    ),
                    4,
                ).cast("double"),
            )
            .select(
                F.col("predio_id").cast("string").alias("predio_id"),
                F.col("pixel_id").cast("string").alias("pixel_id"),
                F.col("emision").cast("string").alias("emision"),
                F.col("horizonte").cast("int").alias("horizonte"),
                F.col("fecha_objetivo").cast("string").alias("fecha_objetivo"),
                F.col("ndvi0").cast("double").alias("ndvi0"),
                F.col("ndmi0").cast("double").alias("ndmi0"),
                F.col("ndvi_pred").cast("double").alias("ndvi_pred"),
                F.col("ndmi_pred").cast("double").alias("ndmi_pred"),
                F.col("das1").cast("double").alias("das1"),
            )
        )

        (
            df_a.write.mode("overwrite")
            .partitionBy("emision")
            .option("compression", "snappy")
            .parquet(args["output_oro_pronostico_a"])
        )

    df_esc = spark.createDataFrame([("sin_riego", 0.0), ("riego_30mm_hoy", 30.0)], ["escenario_riego", "riego_dia1_mm"])

    df_join = (
        df_a.join(F.broadcast(df_predios.select("predio_id", "celda_clima_id", "taw_mm", "p_agotamiento", "dsat_mm")), on="predio_id", how="inner")
        .join(
            df_estado.select(
                "predio_id", "pixel_id", "franja_fisica",
                "s_tau20_init", "s_tau5_init", "dr_k05_init",
                "s_tau45_a05_init", "s_tau60_a05_init",
                "dias_desde_riego_init", "riego_4d_prev_sum", "eto_4d_prev_sum",
            ),
            on=["predio_id", "pixel_id"],
            how="left",
        )
        .join(F.broadcast(df_geos_em.withColumnRenamed("plazo", "horizonte").drop("fecha_objetivo")), on=["celda_clima_id", "emision", "horizonte"], how="inner")
        .crossJoin(F.broadcast(df_esc))
    )

    out_schema = T.StructType(
        [
            T.StructField("predio_id", T.StringType()),
            T.StructField("pixel_id", T.StringType()),
            T.StructField("franja_fisica", T.StringType()),
            T.StructField("emision", T.StringType()),
            T.StructField("miembro", T.IntegerType()),
            T.StructField("escenario_riego", T.StringType()),
            T.StructField("horizonte", T.IntegerType()),
            T.StructField("fecha_objetivo", T.StringType()),
            T.StructField("ndvi_pred", T.DoubleType()),
            T.StructField("kc", T.DoubleType()),
            T.StructField("s_tau20", T.DoubleType()),
            T.StructField("s_tau5", T.DoubleType()),
            T.StructField("dr_k05", T.DoubleType()),
            T.StructField("s_tau45_a05", T.DoubleType()),
            T.StructField("s_tau60_a05", T.DoubleType()),
            T.StructField("riego_5d", T.DoubleType()),
            T.StructField("dias_desde_riego", T.IntegerType()),
            T.StructField("eto_5d", T.DoubleType()),
            T.StructField("vwc_esperada_pct", T.DoubleType()),
            T.StructField("p_verde", T.DoubleType()),
            T.StructField("p_amarillo", T.DoubleType()),
            T.StructField("p_rojo", T.DoubleType()),
            T.StructField("clase_semaforo", T.StringType()),
        ]
    )

    def propagate_and_score(pdf):
        import numpy as np
        import pandas as pd

        par = bc_arimax.value
        mu_x = np.array(par["mu_x"], dtype=float)
        sd_x = np.array(par["sd_x"], dtype=float)
        beta = np.array(par["beta"], dtype=float)
        k0 = float(par["k0"])
        sd_err = float(par["sd_err"])
        prior = np.array(par["prior"], dtype=float)

        pdf = pdf.sort_values("horizonte").reset_index(drop=True)
        r0 = pdf.iloc[0]
        s20 = float(r0["s_tau20_init"]) if pd.notna(r0["s_tau20_init"]) else 10.0
        s5 = float(r0["s_tau5_init"]) if pd.notna(r0["s_tau5_init"]) else 0.0
        dr = float(r0["dr_k05_init"]) if pd.notna(r0["dr_k05_init"]) else 2.5
        s45 = float(r0["s_tau45_a05_init"]) if pd.notna(r0["s_tau45_a05_init"]) else 12.0
        s60 = float(r0["s_tau60_a05_init"]) if pd.notna(r0["s_tau60_a05_init"]) else 15.0
        ddr = int(r0["dias_desde_riego_init"]) if pd.notna(r0["dias_desde_riego_init"]) else 5
        taw = float(r0["taw_mm"])
        p_ag = float(r0["p_agotamiento"])
        raw = p_ag * taw
        dsat = float(r0["dsat_mm"])

        f20, f5 = math.exp(-1.0 / 20.0), math.exp(-1.0 / 5.0)
        f45, f60 = math.exp(-1.0 / 45.0), math.exp(-1.0 / 60.0)
        eto_hist = [float(r0["eto_4d_prev_sum"]) / 4.0] * 4 if pd.notna(r0["eto_4d_prev_sum"]) else [4.2] * 4
        riego_hist = [float(r0["riego_4d_prev_sum"]) / 4.0] * 4 if pd.notna(r0["riego_4d_prev_sum"]) else [0.0] * 4

        def norm_cdf(z):
            return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

        rows = []
        for _, row in pdf.iterrows():
            h = int(row["horizonte"])
            nd = float(row["ndvi_pred"])
            kc = min(1.3, max(0.6, 1.0 * nd + 0.45))
            ll = float(row["lluvia_corr_mm"]) if pd.notna(row["lluvia_corr_mm"]) else 0.0
            et = float(row["eto_corr_mm"]) if pd.notna(row["eto_corr_mm"]) else 4.2
            rg = float(row["riego_dia1_mm"]) if h == 1 else 0.0

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
            p_r = norm_cdf((27.1 - mu_vwc) / sd_err)
            p_a = norm_cdf((30.0 - mu_vwc) / sd_err) - p_r
            p_v = max(1e-6, 1.0 - p_r - p_a)
            probs = np.array([p_v, max(1e-6, p_a), max(1e-6, p_r)]) / prior
            probs = probs / probs.sum()
            cls_name = ["VERDE", "AMARILLO", "ROJO"][int(np.argmax(probs))]

            rows.append(
                {
                    "predio_id": str(row["predio_id"]),
                    "pixel_id": str(row["pixel_id"]),
                    "franja_fisica": str(row["franja_fisica"]) if pd.notna(row["franja_fisica"]) else "lote",
                    "emision": str(row["emision"]),
                    "miembro": int(row["miembro"]),
                    "escenario_riego": str(row["escenario_riego"]),
                    "horizonte": int(h),
                    "fecha_objetivo": str(row["fecha_objetivo"]),
                    "ndvi_pred": float(round(nd, 4)),
                    "kc": float(round(kc, 3)),
                    "s_tau20": float(round(s20, 2)),
                    "s_tau5": float(round(s5, 2)),
                    "dr_k05": float(round(dr, 2)),
                    "s_tau45_a05": float(round(s45, 2)),
                    "s_tau60_a05": float(round(s60, 2)),
                    "riego_5d": float(round(r5, 2)),
                    "dias_desde_riego": int(ddr),
                    "eto_5d": float(round(e5, 2)),
                    "vwc_esperada_pct": float(round(mu_vwc, 2)),
                    "p_verde": float(round(float(probs[0]), 4)),
                    "p_amarillo": float(round(float(probs[1]), 4)),
                    "p_rojo": float(round(float(probs[2]), 4)),
                    "clase_semaforo": str(cls_name),
                }
            )
        return pd.DataFrame(rows)

    df_sem = df_join.groupBy("predio_id", "pixel_id", "miembro", "escenario_riego").applyInPandas(
        propagate_and_score, schema=out_schema
    )

    (
        df_sem.write.mode("overwrite")
        .partitionBy("emision")
        .option("compression", "snappy")
        .parquet(args["output_oro_semaforo"])
    )

    job.commit()


if __name__ == "__main__":
    main()
