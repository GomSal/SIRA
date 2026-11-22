# -*- coding: utf-8 -*-
"""
SIRA (Iteración B — B_SIRA_ARCH) · AWS Glue PySpark Job B02: Lotes 2 y 3 (Clima + GEOS-S2S-3)
=============================================================================================
Alineado a `GomSal/SIRA@modelo-humedad` (HEAD 8fde03d):
1. Lote 3 (Diario): Une CHIRPS (lluvia primaria, con fallback PRECTOTCORR) + NASA POWER
   (ETo FAO-56) y escribe `plata/clima_observado/` particionada por `anio`.
2. Lote 2 (Cada 5 días): Pivotea GEOS-S2S-3 (5 miembros, 30-81 días) en `plata/geos_s2s_crudo/`
   particionada por `emision`, convierte unidades (`pr_sfc * 86400`, `T - 273.15`) y aplica
   la tabla versionada `oro/correccion_coeficientes/` por `(variable, mes, plazo_bin)`.
3. Calcula ETo pronosticada escalada por temperatura media sobre la climatología FAO-56
   de NASA POWER:
       ETo_geos = ETo_clim(doy) * ((Tmed_corr + 17.8) / (Tmed_clim(doy) + 17.8))
4. Escribe `oro/geos_s2s_corregido/` particionada por `emision` con tipos 100% exactos para Athena.
"""

import sys
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
            "input_bronce_chirps",
            "input_bronce_power",
            "input_bronce_geos_nrt",
            "input_oro_coeficientes",
            "output_plata_clima_observado",
            "output_plata_geos_crudo",
            "output_oro_geos_corregido",
        ],
    )

    sc = SparkContext.getOrCreate()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    job = Job(glue_ctx)
    job.init(args["JOB_NAME"], args)
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    df_chirps = spark.read.parquet(args["input_bronce_chirps"])
    df_power = spark.read.parquet(args["input_bronce_power"])
    df_geos = spark.read.parquet(args["input_bronce_geos_nrt"])
    df_coef = spark.read.parquet(args["input_oro_coeficientes"])

    # 1. Capa Plata: Clima Observado Diario (CHIRPS + NASA POWER FAO-56)
    df_cli = (
        df_power.join(
            df_chirps.select("celda_clima_id", "fecha", "lluvia_chirps_mm"),
            on=["celda_clima_id", "fecha"],
            how="left",
        )
        .withColumn("fecha_dt", F.to_date("fecha", "yyyy-MM-dd"))
        .withColumn("lluvia_mm", F.round(F.coalesce("lluvia_chirps_mm", "PRECTOTCORR", F.lit(0.0)), 3))
        .withColumn("eto_mm", F.round(F.col("eto_fao56_power_mm"), 3))
        .withColumn("tmed_c", F.round((F.col("T2M_MAX") + F.col("T2M_MIN")) / F.lit(2.0), 2))
        .withColumn("doy", F.dayofyear("fecha_dt").cast("int"))
        .withColumn("anio", F.year("fecha_dt").cast("int"))
    )

    w_5d = Window.partitionBy("celda_clima_id").orderBy("fecha_dt").rowsBetween(-4, 0)
    w_10d = Window.partitionBy("celda_clima_id").orderBy("fecha_dt").rowsBetween(-9, 0)
    df_cli = (
        df_cli.withColumn("lluvia_5d_mm", F.round(F.sum("lluvia_mm").over(w_5d), 2))
        .withColumn("eto_5d_mm", F.round(F.sum("eto_mm").over(w_5d), 2))
        .withColumn("lluvia_10d_mm", F.round(F.sum("lluvia_mm").over(w_10d), 2))
        .withColumn("eto_10d_mm", F.round(F.sum("eto_mm").over(w_10d), 2))
        .select(
            F.col("celda_clima_id").cast("string").alias("celda_clima_id"),
            F.col("lat_celda").cast("double").alias("lat_celda"),
            F.col("lon_celda").cast("double").alias("lon_celda"),
            F.col("fecha").cast("string").alias("fecha"),
            F.col("T2M_MAX").cast("double").alias("T2M_MAX"),
            F.col("T2M_MIN").cast("double").alias("T2M_MIN"),
            F.col("RH2M").cast("double").alias("RH2M"),
            F.col("WS2M").cast("double").alias("WS2M"),
            F.col("ALLSKY_SFC_SW_DWN").cast("double").alias("ALLSKY_SFC_SW_DWN"),
            F.col("PRECTOTCORR").cast("double").alias("PRECTOTCORR"),
            F.col("lluvia_mm").cast("double").alias("lluvia_mm"),
            F.col("eto_mm").cast("double").alias("eto_mm"),
            F.col("tmed_c").cast("double").alias("tmed_c"),
            F.col("doy").cast("int").alias("doy"),
            F.col("lluvia_5d_mm").cast("double").alias("lluvia_5d_mm"),
            F.col("eto_5d_mm").cast("double").alias("eto_5d_mm"),
            F.col("lluvia_10d_mm").cast("double").alias("lluvia_10d_mm"),
            F.col("eto_10d_mm").cast("double").alias("eto_10d_mm"),
            F.col("anio").cast("int").alias("anio"),
        )
    )

    (
        df_cli.write.mode("overwrite")
        .partitionBy("anio")
        .option("compression", "snappy")
        .parquet(args["output_plata_clima_observado"])
    )

    # Climatología por día del año (doy) para escalar ETo de GEOS-S2S
    df_clim_doy = df_cli.groupBy("celda_clima_id", "doy").agg(
        F.avg("eto_mm").alias("eto_clim_doy"),
        F.avg("tmed_c").alias("tmed_clim_doy"),
        F.avg("lluvia_mm").alias("lluvia_clim_doy"),
    )

    # 2. Capa Plata: GEOS-S2S-3 Crudo (pivot por emision, miembro, fecha_objetivo)
    df_piv = (
        df_geos.groupBy("celda_clima_id", "inicio", "miembro", "fecha")
        .pivot("variable", ["pr_sfc", "t2mmax", "t2mmin"])
        .agg(F.first("valor"))
        .withColumn("emision", F.to_date(F.col("inicio").cast("string"), "yyyyMMdd").cast("string"))
        .withColumn("fecha_objetivo", F.col("fecha").cast("string"))
        .withColumn("plazo", F.datediff(F.to_date("fecha_objetivo", "yyyy-MM-dd"), F.to_date("emision", "yyyy-MM-dd")).cast("int"))
        .withColumn("mes", F.month(F.to_date("fecha_objetivo", "yyyy-MM-dd")).cast("int"))
        .withColumn("doy", F.dayofyear(F.to_date("fecha_objetivo", "yyyy-MM-dd")).cast("int"))
        .withColumn(
            "plazo_bin",
            F.when(F.col("plazo").between(0, 10), F.lit(0))
            .when(F.col("plazo").between(11, 20), F.lit(1))
            .otherwise(F.lit(2))
            .cast("int"),
        )
        .withColumn("pr_raw_mm", F.round(F.col("pr_sfc") * F.lit(86400.0), 3).cast("double"))
        .withColumn("tx_raw_c", F.round(F.col("t2mmax") - F.lit(273.15), 2).cast("double"))
        .withColumn("tn_raw_c", F.round(F.col("t2mmin") - F.lit(273.15), 2).cast("double"))
        .select(
            F.col("celda_clima_id").cast("string").alias("celda_clima_id"),
            F.col("emision").cast("string").alias("emision"),
            F.col("miembro").cast("int").alias("miembro"),
            F.col("fecha_objetivo").cast("string").alias("fecha_objetivo"),
            F.col("plazo").cast("int").alias("plazo"),
            F.col("mes").cast("int").alias("mes"),
            F.col("doy").cast("int").alias("doy"),
            F.col("plazo_bin").cast("int").alias("plazo_bin"),
            F.col("pr_raw_mm").cast("double").alias("pr_raw_mm"),
            F.col("tx_raw_c").cast("double").alias("tx_raw_c"),
            F.col("tn_raw_c").cast("double").alias("tn_raw_c"),
        )
    )

    (
        df_piv.write.mode("overwrite")
        .partitionBy("emision")
        .option("compression", "snappy")
        .parquet(args["output_plata_geos_crudo"])
    )

    # 3. Capa Oro: GEOS-S2S-3 Corregido con coeficientes versionados + ETo escalada
    coef_pr = df_coef.filter(F.col("variable") == "pr").select(
        F.col("mes").cast("int").alias("mes"),
        F.col("plazo").cast("int").alias("plazo_bin"),
        F.col("factor").cast("double").alias("fac_pr"),
    )
    coef_t = df_coef.filter(F.col("variable") == "t").select(
        F.col("mes").cast("int").alias("mes"),
        F.col("plazo").cast("int").alias("plazo_bin"),
        F.col("factor").cast("double").alias("fac_t"),
    )

    df_corr = (
        df_piv.join(F.broadcast(coef_pr), on=["mes", "plazo_bin"], how="left")
        .join(F.broadcast(coef_t), on=["mes", "plazo_bin"], how="left")
        .join(F.broadcast(df_clim_doy), on=["celda_clima_id", "doy"], how="left")
        .withColumn("lluvia_corr_mm", F.round(F.coalesce(F.col("pr_raw_mm") * F.coalesce("fac_pr", F.lit(1.0)), F.col("lluvia_clim_doy")), 3).cast("double"))
        .withColumn("tx_corr_c", F.round(F.col("tx_raw_c") + F.coalesce("fac_t", F.lit(0.0)), 2).cast("double"))
        .withColumn("tn_corr_c", F.round(F.col("tn_raw_c") + F.coalesce("fac_t", F.lit(0.0)), 2).cast("double"))
        .withColumn("tmed_corr_c", (F.col("tx_corr_c") + F.col("tn_corr_c")) / F.lit(2.0))
        .withColumn(
            "eto_corr_mm",
            F.round(
                F.coalesce(
                    F.col("eto_clim_doy") * ((F.col("tmed_corr_c") + F.lit(17.8)) / (F.col("tmed_clim_doy") + F.lit(17.8))),
                    F.col("eto_clim_doy"),
                ),
                3,
            ).cast("double"),
        )
        .withColumn("eto_es_relleno_clim", F.col("tmed_corr_c").isNull().cast("boolean"))
        .select(
            F.col("celda_clima_id").cast("string").alias("celda_clima_id"),
            F.col("emision").cast("string").alias("emision"),
            F.col("miembro").cast("int").alias("miembro"),
            F.col("fecha_objetivo").cast("string").alias("fecha_objetivo"),
            F.col("plazo").cast("int").alias("plazo"),
            F.col("pr_raw_mm").cast("double").alias("pr_raw_mm"),
            F.col("lluvia_corr_mm").cast("double").alias("lluvia_corr_mm"),
            F.col("tx_corr_c").cast("double").alias("tx_corr_c"),
            F.col("tn_corr_c").cast("double").alias("tn_corr_c"),
            F.col("eto_corr_mm").cast("double").alias("eto_corr_mm"),
            F.col("eto_es_relleno_clim").cast("boolean").alias("eto_es_relleno_clim"),
        )
    )

    (
        df_corr.write.mode("overwrite")
        .partitionBy("emision")
        .option("compression", "snappy")
        .parquet(args["output_oro_geos_corregido"])
    )

    job.commit()


if __name__ == "__main__":
    main()
