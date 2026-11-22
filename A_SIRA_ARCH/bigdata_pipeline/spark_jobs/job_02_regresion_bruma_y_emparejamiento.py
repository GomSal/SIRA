# -*- coding: utf-8 -*-
"""
SIRA — AWS Glue PySpark Job 02: Regresión Clima-Satélite (Tuning de Bruma) y Emparejamiento (Paso 4)
=====================================================================================================
Ejecuta en paralelo sobre Apache Spark:
1. Cruza la serie histórica de "Clima por día" (silver/clima_dia) con las observaciones de Sentinel-2 L2A
   (10-20 m) para estimar el índice de humedad esperado según el estado hidroclimático (lluvias 3d/5d,
   ETo FAO-56, AtWD, VPD, humedad relativa y presión).
2. Aplica el ajuste de errores por bruma atmosférica (De-hazing estadístico): pondera el índice
   observado por satélite frente al esperado por la regresión climática usando el espesor óptico de
   aerosoles (AOT) y el índice de claridad solar (Kt).
3. Ejecuta el Paso 4 del diagrama sira_flujo_entrenamiento.html ("Emparejar"): realiza un Broadcast Join
   con el catálogo de parcelas/franjas y une la verdad de campo (198 mediciones TDR de Nataima) con
   los índices corregidos, los 15 componentes PCA de Prithvi-EO-2.0 y el Clima por día.
"""

import sys
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql import functions as F


def main():
    args = getResolvedOptions(
        sys.argv,
        [
            "JOB_NAME",
            "input_silver_clima",
            "input_bronze_s2",
            "input_bronze_prithvi",
            "input_bronze_campo",
            "input_bronze_catalog",
            "output_silver_s2",
            "output_silver_prithvi",
            "output_silver_campo",
            "output_gold_regresion",
            "output_gold_entrenamiento",
        ],
    )

    sc = SparkContext.getOrCreate()
    glue_ctx = GlueContext(sc)
    spark = glue_ctx.spark_session
    job = Job(glue_ctx)
    job.init(args["JOB_NAME"], args)

    # 1. Lectura de fuentes desde S3
    df_clima = spark.read.parquet(args["input_silver_clima"])
    df_s2 = spark.read.parquet(args["input_bronze_s2"])
    df_prithvi = spark.read.parquet(args["input_bronze_prithvi"])
    df_campo = spark.read.parquet(args["input_bronze_campo"])
    df_catalog = spark.read.parquet(args["input_bronze_catalog"])

    # 2. Cruce Clima por día + Sentinel-2 L2A para Regresión Hidroclimática y Corrección de Bruma
    df_s2_clima = df_s2.join(
        df_clima.select(
            "grid_id",
            "fecha",
            "lluvia_1d_mm",
            "lluvia_3d_mm",
            "lluvia_5d_mm",
            "lluvia_15d_mm",
            "swi_lluvia_exp_mm",
            "eto_fao56_mm",
            "atwd_5d_mm",
            "deficit_presion_vapor",
            "humedad_rel_2m_pct",
            "presion_superficie_kpa",
            "indice_claridad_kt",
            "gdd_arroz_c",
        ),
        on=["grid_id", "fecha"],
        how="inner",
    )

    # Ecuación de regresión hidroclimática calibrada sobre días despejados (sin bruma):
    # Predice el NDMI esperado a partir de la recarga hídrica (SWI y lluvia 5d), el déficit atmosférico (AtWD),
    # el VPD y el tratamiento/manejo de lámina de agua.
    trat_offset = (
        F.when(F.col("tratamiento_riego") == "CD", F.lit(0.085))
        .when(F.col("tratamiento_riego") == "MIRI", F.lit(0.025))
        .otherwise(F.lit(-0.045))
    )

    ndmi_esperado_clima = F.round(
        F.lit(0.165)
        + F.lit(0.0022) * F.least(F.col("lluvia_5d_mm"), F.lit(80.0))
        + F.lit(0.0018) * F.least(F.col("swi_lluvia_exp_mm"), F.lit(60.0))
        - F.lit(0.0145) * F.col("atwd_5d_mm")
        - F.lit(0.0320) * F.col("deficit_presion_vapor")
        + F.lit(0.0012) * (F.col("humedad_rel_2m_pct") - F.lit(70.0))
        + trat_offset,
        4,
    )

    # Peso de confianza óptica w_opt en función del espesor de bruma (AOT) y claridad solar (Kt)
    w_opt = F.when(
        F.col("flag_bruma_detectada") == F.lit(True),
        F.greatest(F.lit(0.22), F.lit(1.0) - F.lit(1.45) * F.col("aot_espesor_bruma")) * F.col("indice_claridad_kt"),
    ).otherwise(F.lit(0.92))

    df_s2_tuned = (
        df_s2_clima.withColumn("ndmi_esperado_clima", ndmi_esperado_clima)
        .withColumn("residuo_bruma_ndmi", F.round(F.col("ndmi_observado") - F.col("ndmi_esperado_clima"), 4))
        .withColumn("peso_confianza_optica", F.round(w_opt, 3))
        .withColumn(
            "ndmi_tuned_clima",
            F.round(
                F.col("peso_confianza_optica") * F.col("ndmi_observado")
                + (F.lit(1.0) - F.col("peso_confianza_optica")) * F.col("ndmi_esperado_clima"),
                4,
            ),
        )
        .withColumn(
            "ndwi_tuned_clima",
            F.round(
                F.col("ndwi_observado")
                + (F.lit(1.0) - F.col("peso_confianza_optica")) * F.lit(0.45) * (F.col("ndmi_esperado_clima") - F.col("ndmi_observado")),
                4,
            ),
        )
        .withColumn("anio", F.year(F.to_date("fecha", "yyyy-MM-dd")))
        .withColumn("mes", F.month(F.to_date("fecha", "yyyy-MM-dd")))
    )

    # 3. Persistir tablas en Capa Plata (Silver) y tabla de Regresión en Oro (Gold)
    df_s2_tuned.write.mode("overwrite").partitionBy("anio", "mes").option("compression", "snappy").parquet(args["output_silver_s2"])
    df_prithvi.write.mode("overwrite").option("compression", "snappy").parquet(args["output_silver_prithvi"])
    df_campo.write.mode("overwrite").option("compression", "snappy").parquet(args["output_silver_campo"])
    df_s2_tuned.write.mode("overwrite").option("compression", "snappy").parquet(args["output_gold_regresion"])

    # 4. Paso 4: Emparejar (Broadcast Join con geometrías de franja + Unión Verdad de Campo + Satélite + Prithvi + Clima)
    df_franjas = (
        df_catalog.select(
            "tratamiento_riego",
            "franja_id",
            "distrito_riego",
            "municipio",
            "ancho_franja_m",
            "resolucion_pixel_m",
            "capacidad_campo_fc_pct",
            "punto_marchitez_pwp_pct",
            "umbral_deficit_critico_pct",
        )
        .dropDuplicates(["tratamiento_riego"])
    )

    df_train = (
        df_campo.join(F.broadcast(df_franjas), on="tratamiento_riego", how="inner")
        .join(
            df_s2_tuned.drop("anio", "mes"),
            on=["fecha", "tratamiento_riego"],
            how="inner",
        )
        .join(
            df_prithvi.drop("grid_id"),
            on=["fecha"],
            how="inner",
        )
    )

    # Calcular SWI total (Lluvia + Riego) en la tabla maestra de entrenamiento
    df_train = df_train.withColumn(
        "swi_total_lluvia_mas_riego_mm",
        F.round(F.col("swi_lluvia_exp_mm") + F.col("riego_aplicado_mm"), 2),
    )

    df_train.write.mode("overwrite").option("compression", "snappy").parquet(args["output_gold_entrenamiento"])

    job.commit()


if __name__ == "__main__":
    main()
