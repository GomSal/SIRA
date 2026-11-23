# -*- coding: utf-8 -*-
"""
SIRA (Iteracion B Unificada) · AWS Glue PySpark Job B02: Lotes 2 y 3 (Clima 26 anos + GEOS-S2S-3)
==================================================================================================
Conectado directamente a los insumos reales de ML en S3 (`GomSal/SIRA@modelo-humedad`, 86aa09b):
1. Lote 3 (Diario): Lee `plata/cubo_a/clima.csv` (9.453 dias, 2001-2026: CHIRPS + NASA POWER FAO-56,
   con fallback automatico a `bronce/chirps/` + `bronce/power/` si no se pasa `input_plata_cubo_clima`).
   Aplica la regla oficial de relleno de huecos CHIRPS (`lluvia_mm = coalesce(lluvia, PRECTOTCORR, 0.0)`)
   y escribe `plata/clima_observado/` particionada por `anio`.
2. Climatologia de 10 anos sellada (`2015-11-18` a `2025-11-17`): identica a `semaforo_c.py` para
   escalar ETo de GEOS-S2S-3 sin deriva de escala.
3. Lote 2 (Cada 5 dias): Lee `bronce/geos_s2s_a/geos_nrt.csv` (o `bronce/geos_s2s/geos_s2s_nrt.parquet`),
   pivotea los 5 miembros en `plata/geos_s2s_crudo/` particionada por `emision`, aplica la tabla
   versionada `oro/correccion_coeficientes/` por `(variable, mes, plazo_bin)` y calcula ETo escalada:
       ETo_geos = ETo_clim(doy) * ((Tmed_corr + 17.8) / (Tmed_clim(doy) + 17.8))
4. Escribe `oro/geos_s2s_corregido/` particionada por `emision` (consumida por `sira-b-05` y Athena).
"""

import sys
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql import Window
from pyspark.sql import functions as F


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
            "input_bronce_chirps",
            "input_bronce_power",
            "input_bronce_geos_nrt",
            "input_oro_coeficientes",
            "output_plata_clima_observado",
            "output_plata_geos_crudo",
            "output_oro_geos_corregido",
        ],
    )
    input_cubo_clima = parse_optional_arg(
        sys.argv,
        "input_plata_cubo_clima",
        "s3://sira-b-lakehouse-929570730700/plata/cubo_a/clima.csv",
    )
    input_geos_csv = parse_optional_arg(
        sys.argv,
        "input_bronce_geos_csv",
        "s3://sira-b-lakehouse-929570730700/bronce/geos_s2s_a/geos_nrt.csv",
    )

    sc = SparkContext.getOrCreate()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    job = Job(glue_ctx)
    job.init(args["JOB_NAME"], args)
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    # 1. Capa Plata: Clima Observado Diario (2001-2026 desde plata/cubo_a/clima.csv con fallback Bronce)
    use_cubo_clima = False
    if input_cubo_clima:
        try:
            df_raw_cl = spark.read.option("header", "true").option("inferSchema", "true").csv(input_cubo_clima)
            if "eto" in df_raw_cl.columns and "lluvia" in df_raw_cl.columns:
                use_cubo_clima = True
        except Exception as e:
            print(f"[sira-b-02] Aviso leyendo {input_cubo_clima}: {e}; usando bronce/chirps + power")

    if use_cubo_clima:
        df_cli = (
            df_raw_cl.withColumn("celda_clima_id", F.lit("CELDA_4.0_-75.0"))
            .withColumn("lat_celda", F.lit(4.0))
            .withColumn("lon_celda", F.lit(-75.0))
            .withColumn("fecha", F.substring(F.col("fecha").cast("string"), 1, 10))
            .withColumn("fecha_dt", F.to_date("fecha", "yyyy-MM-dd"))
            .withColumn("lluvia_chirps_raw", F.col("lluvia").cast("double"))
            .withColumn("PRECTOTCORR", F.col("PRECTOTCORR").cast("double"))
            .withColumn("lluvia_mm", F.round(F.coalesce("lluvia_chirps_raw", "PRECTOTCORR", F.lit(0.0)), 3))
            .withColumn("eto_mm", F.round(F.col("eto").cast("double"), 3))
            .withColumn("T2M_MAX", F.col("T2M_MAX").cast("double"))
            .withColumn("T2M_MIN", F.col("T2M_MIN").cast("double"))
            .withColumn("RH2M", F.col("RH2M").cast("double"))
            .withColumn("WS2M", F.col("WS2M").cast("double"))
            .withColumn("ALLSKY_SFC_SW_DWN", F.col("ALLSKY_SFC_SW_DWN").cast("double"))
            .withColumn("tmed_c", F.round((F.col("T2M_MAX") + F.col("T2M_MIN")) / F.lit(2.0), 4))
            .withColumn("doy", F.dayofyear("fecha_dt").cast("int"))
            .withColumn("anio", F.year("fecha_dt").cast("int"))
        )
        print("[sira-b-02] Usando serie climatica real 2001-2026 de plata/cubo_a/clima.csv")
    else:
        df_chirps = spark.read.parquet(args["input_bronce_chirps"])
        df_power = spark.read.parquet(args["input_bronce_power"])
        df_cli = (
            df_power.join(
                df_chirps.select("celda_clima_id", "fecha", "lluvia_chirps_mm"),
                on=["celda_clima_id", "fecha"],
                how="left",
            )
            .withColumn("fecha_dt", F.to_date("fecha", "yyyy-MM-dd"))
            .withColumn("lluvia_chirps_raw", F.col("lluvia_chirps_mm").cast("double"))
            .withColumn("lluvia_mm", F.round(F.coalesce("lluvia_chirps_mm", "PRECTOTCORR", F.lit(0.0)), 3))
            .withColumn("eto_mm", F.round(F.col("eto_fao56_power_mm"), 3))
            .withColumn("tmed_c", F.round((F.col("T2M_MAX") + F.col("T2M_MIN")) / F.lit(2.0), 4))
            .withColumn("doy", F.dayofyear("fecha_dt").cast("int"))
            .withColumn("anio", F.year("fecha_dt").cast("int"))
        )

    w_5d = Window.partitionBy("celda_clima_id").orderBy("fecha_dt").rowsBetween(-4, 0)
    w_10d = Window.partitionBy("celda_clima_id").orderBy("fecha_dt").rowsBetween(-9, 0)
    df_cli_out = (
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
            F.round(F.col("tmed_c"), 2).cast("double").alias("tmed_c"),
            F.col("doy").cast("int").alias("doy"),
            F.col("lluvia_5d_mm").cast("double").alias("lluvia_5d_mm"),
            F.col("eto_5d_mm").cast("double").alias("eto_5d_mm"),
            F.col("lluvia_10d_mm").cast("double").alias("lluvia_10d_mm"),
            F.col("eto_10d_mm").cast("double").alias("eto_10d_mm"),
            F.col("anio").cast("int").alias("anio"),
        )
    )

    (
        df_cli_out.write.mode("overwrite")
        .partitionBy("anio")
        .option("compression", "snappy")
        .parquet(args["output_plata_clima_observado"])
    )

    # Climatologia por dia del ano (doy) en ventana sellada 10 anos (2015-11-18..2025-11-17) identica a semaforo_c.py
    df_clim_win = df_cli.filter((F.col("fecha") >= F.lit("2015-11-18")) & (F.col("fecha") <= F.lit("2025-11-17")))
    if df_clim_win.limit(1).count() == 0:
        df_clim_win = df_cli
    df_clim_doy = df_clim_win.groupBy("celda_clima_id", "doy").agg(
        F.avg("eto_mm").alias("eto_clim_doy"),
        F.avg("tmed_c").alias("tmed_clim_doy"),
        F.avg("lluvia_chirps_raw").alias("lluvia_clim_doy"),
    )

    # 2. Capa Plata: GEOS-S2S-3 Crudo (prioriza bronce/geos_s2s_a/geos_nrt.csv de ML)
    df_geos = None
    if input_geos_csv:
        try:
            df_geos = (
                spark.read.option("header", "true")
                .option("inferSchema", "true")
                .csv(input_geos_csv)
                .withColumn("celda_clima_id", F.lit("CELDA_4.0_-75.0"))
                .withColumn("inicio", F.substring(F.col("inicio").cast("string"), 1, 8))
                .withColumn("fecha", F.substring(F.col("fecha").cast("string"), 1, 10))
                .withColumn("miembro", F.col("miembro").cast("int"))
                .withColumn("valor", F.col("valor").cast("double"))
            )
            print(f"[sira-b-02] Leyendo GEOS-S2S NRT directo desde ML: {input_geos_csv}")
        except Exception as e:
            print(f"[sira-b-02] Aviso leyendo {input_geos_csv}: {e}; usando {args['input_bronce_geos_nrt']}")
            df_geos = None
    if df_geos is None:
        df_geos = spark.read.parquet(args["input_bronce_geos_nrt"])

    df_coef = spark.read.parquet(args["input_oro_coeficientes"])

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
            F.when(F.col("plazo") <= 10, F.lit(0))
            .when(F.col("plazo") <= 20, F.lit(1))
            .otherwise(F.lit(2))
            .cast("int"),
        )
        .withColumn("pr_raw_mm", (F.col("pr_sfc") * F.lit(86400.0)).cast("double"))
        .withColumn("tx_raw_c", (F.col("t2mmax") - F.lit(273.15)).cast("double"))
        .withColumn("tn_raw_c", (F.col("t2mmin") - F.lit(273.15)).cast("double"))
    )

    df_piv_out = df_piv.select(
        F.col("celda_clima_id").cast("string").alias("celda_clima_id"),
        F.col("emision").cast("string").alias("emision"),
        F.col("miembro").cast("int").alias("miembro"),
        F.col("fecha_objetivo").cast("string").alias("fecha_objetivo"),
        F.col("plazo").cast("int").alias("plazo"),
        F.col("mes").cast("int").alias("mes"),
        F.col("doy").cast("int").alias("doy"),
        F.col("plazo_bin").cast("int").alias("plazo_bin"),
        F.round(F.col("pr_raw_mm"), 3).cast("double").alias("pr_raw_mm"),
        F.round(F.col("tx_raw_c"), 2).cast("double").alias("tx_raw_c"),
        F.round(F.col("tn_raw_c"), 2).cast("double").alias("tn_raw_c"),
    )

    (
        df_piv_out.write.mode("overwrite")
        .partitionBy("emision")
        .option("compression", "snappy")
        .parquet(args["output_plata_geos_crudo"])
    )

    # 3. Capa Oro: GEOS-S2S-3 Corregido con coeficientes versionados + ETo escalada (paridad 1:1 con semaforo_c.py)
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
        .withColumn(
            "lluvia_corr_mm",
            F.round(F.coalesce(F.col("pr_raw_mm") * F.coalesce("fac_pr", F.lit(1.0)), F.col("lluvia_clim_doy")), 4).cast("double"),
        )
        .withColumn("tx_corr_c", (F.col("tx_raw_c") + F.coalesce("fac_t", F.lit(0.0))).cast("double"))
        .withColumn("tn_corr_c", (F.col("tn_raw_c") + F.coalesce("fac_t", F.lit(0.0))).cast("double"))
        .withColumn("tmed_corr_c", (F.col("tx_corr_c") + F.col("tn_corr_c")) / F.lit(2.0))
        .withColumn(
            "eto_corr_mm",
            F.round(
                F.coalesce(
                    F.col("eto_clim_doy") * ((F.col("tmed_corr_c") + F.lit(17.8)) / (F.col("tmed_clim_doy") + F.lit(17.8))),
                    F.col("eto_clim_doy"),
                ),
                4,
            ).cast("double"),
        )
        .withColumn("eto_es_relleno_clim", F.col("tmed_corr_c").isNull().cast("boolean"))
        .select(
            F.col("celda_clima_id").cast("string").alias("celda_clima_id"),
            F.col("emision").cast("string").alias("emision"),
            F.col("miembro").cast("int").alias("miembro"),
            F.col("fecha_objetivo").cast("string").alias("fecha_objetivo"),
            F.col("plazo").cast("int").alias("plazo"),
            F.round(F.col("pr_raw_mm"), 3).cast("double").alias("pr_raw_mm"),
            F.col("lluvia_corr_mm").cast("double").alias("lluvia_corr_mm"),
            F.round(F.col("tx_corr_c"), 2).cast("double").alias("tx_corr_c"),
            F.round(F.col("tn_corr_c"), 2).cast("double").alias("tn_corr_c"),
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
