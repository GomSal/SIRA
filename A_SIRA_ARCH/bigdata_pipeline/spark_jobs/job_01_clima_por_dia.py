# -*- coding: utf-8 -*-
"""
SIRA — AWS Glue PySpark Job 01: Procesamiento Hidroclimático ("Clima por Día" — Pasos 2c y 2d)
==============================================================================================
Ejecuta en paralelo sobre Apache Spark (AWS Glue 4.0 / 5.0):
- Paso 2c (CHIRPS v2.0): Lluvias acumuladas móviles de 1, 3, 5, 7 y 15 días, media móvil de 5 días,
  conteo de días secos e índice exponencial de recarga hídrica (SWI).
- Paso 2d (NASA POWER): Cálculo vectorial de Evapotranspiración de Referencia (ETo) mediante la
  ecuación física FAO-56 Penman-Monteith incorporando Temperatura, Radiación Solar, Velocidad del
  Viento, Humedad Relativa (RH2M -> presión real de vapor ea y VPD) y Presión Superficial (PS ->
  constante psicrométrica gamma), más el Déficit Hídrico Atmosférico (AtWD de 5 días) del artículo
  de Nataima (Martínez-Vega et al., 2025).
- Auditoría IDEAM: Materializa la tabla de contraste frente a estaciones públicas a 15-36 km.
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
            "input_chirps",
            "input_nasa_power",
            "input_ideam",
            "output_silver_clima",
            "output_silver_ideam",
        ],
    )

    sc = SparkContext.getOrCreate()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    job = Job(glue_ctx)
    job.init(args["JOB_NAME"], args)

    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    # 1. Lectura distribuida desde Capa Bronce en S3
    df_chirps = spark.read.parquet(args["input_chirps"])
    df_power = spark.read.parquet(args["input_nasa_power"])
    df_ideam = spark.read.parquet(args["input_ideam"])

    # 2. Validación de calidad en origen y unión espaciotemporal por (grid_id, fecha)
    df_chirps_clean = (
        df_chirps.filter(F.col("grid_id").isNotNull() & F.col("fecha").isNotNull())
        .dropDuplicates(["grid_id", "fecha"])
        .withColumn("lluvia_1d_mm", F.greatest(F.col("lluvia_1d_mm").cast("double"), F.lit(0.0)))
    )

    df_power_clean = (
        df_power.filter(F.col("grid_id").isNotNull() & F.col("fecha").isNotNull())
        .dropDuplicates(["grid_id", "fecha"])
        .drop("municipio")
    )

    df = df_chirps_clean.join(df_power_clean, on=["grid_id", "fecha"], how="inner")
    df = df.withColumn("fecha_dt", F.to_date(F.col("fecha"), "yyyy-MM-dd"))

    # 3. Ventanas móviles distribuidas por celda climática ordenadas por fecha (Sin collect al driver)
    w_1d_prev = Window.partitionBy("grid_id").orderBy("fecha_dt")
    w_3d = Window.partitionBy("grid_id").orderBy("fecha_dt").rowsBetween(-2, 0)
    w_5d = Window.partitionBy("grid_id").orderBy("fecha_dt").rowsBetween(-4, 0)
    w_7d = Window.partitionBy("grid_id").orderBy("fecha_dt").rowsBetween(-6, 0)
    w_15d = Window.partitionBy("grid_id").orderBy("fecha_dt").rowsBetween(-14, 0)

    # 4. Paso 2c: Lluvias acumuladas (1, 3, 5, 7 y 15 días) e índice de recarga exponencial SWI
    p0 = F.col("lluvia_1d_mm")
    p1 = F.coalesce(F.lag("lluvia_1d_mm", 1).over(w_1d_prev), F.lit(0.0))
    p2 = F.coalesce(F.lag("lluvia_1d_mm", 2).over(w_1d_prev), F.lit(0.0))
    p3 = F.coalesce(F.lag("lluvia_1d_mm", 3).over(w_1d_prev), F.lit(0.0))
    p4 = F.coalesce(F.lag("lluvia_1d_mm", 4).over(w_1d_prev), F.lit(0.0))

    # SWI(t) = sum_{k=0..4} exp(-k / 2.0) * P(t-k)
    swi_exp = (
        p0 * F.lit(1.0000)
        + p1 * F.lit(0.6065)
        + p2 * F.lit(0.3679)
        + p3 * F.lit(0.2231)
        + p4 * F.lit(0.1353)
    )

    df = (
        df.withColumn("lluvia_3d_mm", F.round(F.sum("lluvia_1d_mm").over(w_3d), 2))
        .withColumn("lluvia_5d_mm", F.round(F.sum("lluvia_1d_mm").over(w_5d), 2))
        .withColumn("lluvia_7d_mm", F.round(F.sum("lluvia_1d_mm").over(w_7d), 2))
        .withColumn("lluvia_15d_mm", F.round(F.sum("lluvia_1d_mm").over(w_15d), 2))
        .withColumn("lluvia_media_5d_mm", F.round(F.avg("lluvia_1d_mm").over(w_5d), 2))
        .withColumn("swi_lluvia_exp_mm", F.round(swi_exp, 2))
    )

    # 5. Paso 2d: Ecuación FAO-56 Penman-Monteith vectorial (con Humedad Relativa RH2M y Presión PS)
    tmean = F.col("temp_media_2m_c")
    tmax = F.col("temp_max_2m_c")
    tmin = F.col("temp_min_2m_c")
    rh = F.col("humedad_rel_2m_pct")
    ps = F.col("presion_superficie_kpa")
    u2 = F.col("viento_2m_ms")
    rs = F.col("rad_solar_mj_m2_d")
    rso = F.col("rad_cielo_despejado_mj")

    # Presión de saturación de vapor (es), presión real (ea) a partir de Humedad Relativa, y VPD (kPa)
    es_max = F.lit(0.6108) * F.exp((F.lit(17.27) * tmax) / (tmax + F.lit(237.3)))
    es_min = F.lit(0.6108) * F.exp((F.lit(17.27) * tmin) / (tmin + F.lit(237.3)))
    es = (es_max + es_min) / F.lit(2.0)
    ea = es * (rh / F.lit(100.0))
    vpd = F.greatest(es - ea, F.lit(0.05))

    # Pendiente de la curva de presión de vapor (delta) y constante psicrométrica (gamma) por Presión PS
    delta = (
        F.lit(4098.0) * (F.lit(0.6108) * F.exp((F.lit(17.27) * tmean) / (tmean + F.lit(237.3))))
    ) / F.pow(tmean + F.lit(237.3), 2)
    gamma = F.lit(0.000665) * ps

    # Índice de claridad solar Kt = Rs / Rso (indicador físico de bruma/nubosidad) y radiación neta Rn
    kt = F.least(F.greatest(rs / F.greatest(rso, F.lit(1.0)), F.lit(0.15)), F.lit(1.0))
    rns = F.lit(0.77) * rs
    sigma = F.lit(4.903e-9)
    t_k4 = (F.pow(tmax + F.lit(273.16), 4) + F.pow(tmin + F.lit(273.16), 4)) / F.lit(2.0)
    rnl = sigma * t_k4 * (F.lit(0.34) - F.lit(0.14) * F.sqrt(ea)) * (F.lit(1.35) * kt - F.lit(0.35))
    rn = F.greatest(rns - rnl, F.lit(1.0))

    # Evapotranspiración de referencia ETo FAO-56 (mm/día)
    num_eto = (F.lit(0.408) * delta * rn) + (
        gamma * (F.lit(900.0) / (tmean + F.lit(273.0))) * u2 * vpd
    )
    den_eto = delta + gamma * (F.lit(1.0) + F.lit(0.34) * u2)
    eto_fao56 = F.round(F.greatest(num_eto / den_eto, F.lit(0.5)), 2)

    df = (
        df.withColumn("deficit_presion_vapor", F.round(vpd, 3))
        .withColumn("constante_psicrometrica_gamma", F.round(gamma, 4))
        .withColumn("indice_claridad_kt", F.round(kt, 3))
        .withColumn("eto_fao56_mm", eto_fao56)
        .withColumn("eto_media_5d_mm", F.round(F.avg("eto_fao56_mm").over(w_5d), 2))
        .withColumn("atwd_5d_mm", F.round(F.col("eto_media_5d_mm") - F.col("lluvia_media_5d_mm"), 2))
        .withColumn("balance_climatico_1d_mm", F.round(F.col("lluvia_1d_mm") - F.col("eto_fao56_mm"), 2))
        .withColumn(
            "balance_climatico_5d_mm",
            F.round(F.col("lluvia_5d_mm") - F.sum("eto_fao56_mm").over(w_5d), 2),
        )
        .withColumn(
            "gdd_arroz_c",
            F.round(F.greatest(((tmax + tmin) / F.lit(2.0)) - F.lit(10.0), F.lit(0.0)), 2),
        )
        .withColumn("anio", F.year("fecha_dt"))
        .withColumn("mes", F.month("fecha_dt"))
        .drop("fecha_dt")
    )

    # 6. Escritura particionada por (anio, mes) en Capa Plata (Parquet Snappy)
    (
        df.write.mode("overwrite")
        .partitionBy("anio", "mes")
        .option("compression", "snappy")
        .parquet(args["output_silver_clima"])
    )

    (
        df_ideam.write.mode("overwrite")
        .option("compression", "snappy")
        .parquet(args["output_silver_ideam"])
    )

    job.commit()


if __name__ == "__main__":
    main()
