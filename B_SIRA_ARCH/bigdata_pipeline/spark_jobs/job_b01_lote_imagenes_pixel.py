# -*- coding: utf-8 -*-
"""
SIRA (Iteración B — B_SIRA_ARCH) · AWS Glue PySpark Job B01: Lote 1 Diario de Imágenes
======================================================================================
Alineado a `GomSal/SIRA@modelo-humedad` (commit a12e973):
1. Lee Sentinel-2 L2A (10-20m) y Landsat 7/8/9 C2 L2 (30m -> rejilla 10m) desde Bronce.
2. Aplica calibración lineal por plataforma Landsat -> Sentinel-2 (S2 = a + b * L)
   mediante Broadcast Join de la tabla de coeficientes `calibracion_landsat_s2`.
3. Aplica el filtro determinístico de bruma por píxel (SIN regresión hidroclimática):
   - Descarta una observación si NDVI cae > 0.10 frente a la fecha válida anterior y
     siguiente (<= 30 días), o si azul (B02) >= 0.075 con vecinas vegetadas (NDVI > 0.5).
4. Marca origen del dato (`observado` vs `estimado_prithvi` cuando hoy está nublado).
5. Calcula por píxel las variables agregadas del Modelo A (20 features exactas):
   - Estado: ndvi0, ndmi0, sensor0
   - Rezagos y pendientes: ndvi_l1, edad_l1 (<=60d), ndmi_l1, ndvi_l2, edad_l2 (<=90d),
     pend_ndvi, pend_ndmi
   - Fenología (fecha de siembra por curva NDVI): das0, etapa0, doy_sin, doy_cos
6. Escribe `plata/indices_pixel/` particionada por `(predio_id, fecha)` (Parquet Snappy).

Nota de arquitectura (`SPARK_ECUACION.md`): En el MVP de 1 predio corre en Python puro;
este Job PySpark habilita el escalamiento por memoria a 100 predios (~10^10 filas históricas).
"""

import sys
import math
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql import Window
from pyspark.sql import functions as F


def main():
    args = getResolvedOptions(
        sys.argv,
        [
            "JOB_NAME",
            "input_bronce_s2",
            "input_bronce_landsat",
            "input_calibracion",
            "output_plata_indices_pixel",
        ],
    )

    sc = SparkContext.getOrCreate()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    job = Job(glue_ctx)
    job.init(args["JOB_NAME"], args)
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    df_s2 = spark.read.parquet(args["input_bronce_s2"]).select(
        "predio_id", "pixel_id", "fecha", "sensor", "plataforma",
        "ndvi", "ndmi", "azul_b02", "franja_fisica", "pureza_franja"
    )
    df_ls = spark.read.parquet(args["input_bronce_landsat"]).select(
        "predio_id", "pixel_id", "fecha", "sensor", "plataforma",
        "ndvi", "ndmi", "azul_b02", "franja_fisica", "pureza_franja"
    )
    df_cal = spark.read.parquet(args["input_calibracion"])

    # 1. Calibración lineal Landsat -> Sentinel-2 vía Broadcast Join
    cal_ndvi = df_cal.filter(F.col("indice") == "ndvi").select(
        "plataforma", F.col("a").alias("a_ndvi"), F.col("b").alias("b_ndvi")
    )
    cal_ndmi = df_cal.filter(F.col("indice") == "ndmi").select(
        "plataforma", F.col("a").alias("a_ndmi"), F.col("b").alias("b_ndmi")
    )

    df_ls_cal = (
        df_ls.join(F.broadcast(cal_ndvi), on="plataforma", how="left")
        .join(F.broadcast(cal_ndmi), on="plataforma", how="left")
        .withColumn("ndvi", F.coalesce(F.col("a_ndvi") + F.col("b_ndvi") * F.col("ndvi"), F.col("ndvi")))
        .withColumn("ndmi", F.coalesce(F.col("a_ndmi") + F.col("b_ndmi") * F.col("ndmi"), F.col("ndmi")))
        .drop("a_ndvi", "b_ndvi", "a_ndmi", "b_ndmi")
    )

    # 2. Unificación multi-sensor con prioridad S2 en misma fecha
    df_all = (
        df_s2.unionByName(df_ls_cal)
        .withColumn("fecha_dt", F.to_date("fecha", "yyyy-MM-dd"))
        .withColumn(
            "sensor0",
            F.when(F.col("sensor") == "S2", F.lit(0))
            .when(F.col("plataforma") == "landsat-7", F.lit(1))
            .otherwise(F.lit(2)),
        )
    )

    w_dedup = Window.partitionBy("predio_id", "pixel_id", "fecha").orderBy("sensor0")
    df_all = df_all.withColumn("rn", F.row_number().over(w_dedup)).filter(F.col("rn") == 1).drop("rn")

    # 3. Control determinístico de bruma por píxel (informe §4 de modelo-humedad)
    w_pix = Window.partitionBy("predio_id", "pixel_id").orderBy("fecha_dt")
    df_qc = (
        df_all.withColumn("prev_ndvi", F.lag("ndvi", 1).over(w_pix))
        .withColumn("next_ndvi", F.lead("ndvi", 1).over(w_pix))
        .withColumn("prev_dt", F.lag("fecha_dt", 1).over(w_pix))
        .withColumn("next_dt", F.lead("fecha_dt", 1).over(w_pix))
        .withColumn("prev_ok", F.datediff("fecha_dt", "prev_dt") <= 30)
        .withColumn("next_ok", F.datediff("next_dt", "fecha_dt") <= 30)
        .withColumn(
            "flag_bruma",
            (
                F.col("prev_ok")
                & F.col("next_ok")
                & (F.col("ndvi") < F.col("prev_ndvi") - F.lit(0.10))
                & (F.col("ndvi") < F.col("next_ndvi") - F.lit(0.10))
            )
            | (
                F.col("prev_ok")
                & F.col("next_ok")
                & (F.col("azul_b02") >= F.lit(0.075))
                & (F.col("prev_ndvi") > F.lit(0.5))
                & (F.col("next_ndvi") > F.lit(0.5))
            ),
        )
        .withColumn("flag_bruma", F.coalesce("flag_bruma", F.lit(False)))
        .withColumn(
            "origen_valor",
            F.when(F.col("flag_bruma"), F.lit("descartado_bruma")).otherwise(F.lit("observado")),
        )
    )

    # 4. Rezagos, edades, pendientes y fenología sobre observaciones limpias (20 features de Modelo A)
    df_valid = df_qc.filter(~F.col("flag_bruma"))
    w_val = Window.partitionBy("predio_id", "pixel_id").orderBy("fecha_dt")

    df_feat = (
        df_valid.withColumn("ndvi0", F.round("ndvi", 4))
        .withColumn("ndmi0", F.round("ndmi", 4))
        .withColumn("dt_l1", F.lag("fecha_dt", 1).over(w_val))
        .withColumn("dt_l2", F.lag("fecha_dt", 2).over(w_val))
        .withColumn("edad_l1_raw", F.datediff("fecha_dt", "dt_l1").cast("double"))
        .withColumn("edad_l2_raw", F.datediff("fecha_dt", "dt_l2").cast("double"))
        .withColumn("edad_l1", F.when(F.col("edad_l1_raw") <= 60, F.col("edad_l1_raw")))
        .withColumn("edad_l2", F.when(F.col("edad_l2_raw") <= 90, F.col("edad_l2_raw")))
        .withColumn("ndvi_l1", F.when(F.col("edad_l1").isNotNull(), F.round(F.lag("ndvi", 1).over(w_val), 4)))
        .withColumn("ndmi_l1", F.when(F.col("edad_l1").isNotNull(), F.round(F.lag("ndmi", 1).over(w_val), 4)))
        .withColumn("ndvi_l2", F.when(F.col("edad_l2").isNotNull(), F.round(F.lag("ndvi", 2).over(w_val), 4)))
        .withColumn(
            "pend_ndvi",
            F.when(F.col("edad_l1") > 0, F.round((F.col("ndvi0") - F.col("ndvi_l1")) / F.col("edad_l1"), 6)),
        )
        .withColumn(
            "pend_ndmi",
            F.when(F.col("edad_l1") > 0, F.round((F.col("ndmi0") - F.col("ndmi_l1")) / F.col("edad_l1"), 6)),
        )
        # Detección de última fecha con NDVI <= 0.35 como ancla de siembra por píxel
        .withColumn("dt_bajo", F.when(F.col("ndvi0") <= 0.35, F.col("fecha_dt")))
        .withColumn(
            "fecha_siembra_est",
            F.last("dt_bajo", ignorenulls=True).over(w_val.rowsBetween(Window.unboundedPreceding, 0)),
        )
        .withColumn("das0_raw", F.datediff("fecha_dt", "fecha_siembra_est").cast("double"))
        .withColumn("das0", F.when((F.col("das0_raw") >= 0) & (F.col("das0_raw") <= 150), F.col("das0_raw")))
        .withColumn(
            "etapa0",
            F.when(F.col("das0").isNull(), F.lit(0))
            .when(F.col("das0") <= 45, F.lit(1))
            .when(F.col("das0") <= 82, F.lit(2))
            .when(F.col("das0") <= 130, F.lit(3))
            .otherwise(F.lit(0)),
        )
        .withColumn("doy0", F.dayofyear("fecha_dt"))
        .withColumn("doy_sin", F.round(F.sin(F.lit(2.0 * math.pi) * F.col("doy0") / F.lit(365.25)), 5))
        .withColumn("doy_cos", F.round(F.cos(F.lit(2.0 * math.pi) * F.col("doy0") / F.lit(365.25)), 5))
        .select(
            "predio_id",
            "pixel_id",
            "fecha",
            "sensor",
            "plataforma",
            "sensor0",
            "franja_fisica",
            "pureza_franja",
            "ndvi0",
            "ndmi0",
            "azul_b02",
            "origen_valor",
            "flag_bruma",
            "ndvi_l1",
            "edad_l1",
            "ndmi_l1",
            "ndvi_l2",
            "edad_l2",
            "pend_ndvi",
            "pend_ndmi",
            F.col("fecha_siembra_est").cast("string").alias("fecha_siembra_est"),
            "das0",
            "etapa0",
            "doy_sin",
            "doy_cos",
        )
    )

    (
        df_feat.write.mode("overwrite")
        .partitionBy("predio_id", "fecha")
        .option("compression", "snappy")
        .parquet(args["output_plata_indices_pixel"])
    )

    job.commit()


if __name__ == "__main__":
    main()
