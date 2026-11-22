-- =========================================================================================
-- SIRA (Equipo 10) — DDL y Consultas Analíticas en Amazon Athena (Base de datos: sira_clima_db)
-- Región AWS: us-east-2 (Ohio) | Bucket S3: s3://sira-lakehouse-clima-929570730700/
-- =========================================================================================

CREATE DATABASE IF NOT EXISTS sira_clima_db
COMMENT 'Lakehouse Medallion de SIRA: Tratamiento de Clima por Dia (CHIRPS + NASA POWER + IDEAM), Tuning de Bruma e Inferencia por Pixel de 10m';

-- -----------------------------------------------------------------------------------------
-- 1. CAPA PLATA: Tabla principal "Clima por día" (Pasos 2c y 2d de sira_flujo_entrenamiento.html)
-- -----------------------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS sira_clima_db.silver_clima_dia (
    grid_id                         string,
    municipio                       string,
    fecha                           string,
    lluvia_1d_mm                    double,
    calidad_chirps_flag             string,
    departamento                    string,
    latitud_centro                  double,
    longitud_centro                 double,
    altitud_msnm                    double,
    temp_media_2m_c                 double,
    temp_max_2m_c                   double,
    temp_min_2m_c                   double,
    rad_solar_mj_m2_d               double,
    rad_cielo_despejado_mj          double,
    viento_2m_ms                    double,
    humedad_rel_2m_pct              double,
    presion_superficie_kpa          double,
    calidad_power_flag              string,
    lluvia_3d_mm                    double,
    lluvia_5d_mm                    double,
    lluvia_7d_mm                    double,
    lluvia_15d_mm                   double,
    lluvia_media_5d_mm              double,
    swi_lluvia_exp_mm               double,
    deficit_presion_vapor           double,
    constante_psicrometrica_gamma   double,
    indice_claridad_kt              double,
    eto_fao56_mm                    double,
    eto_media_5d_mm                 double,
    atwd_5d_mm                      double,
    balance_climatico_1d_mm         double,
    balance_climatico_5d_mm         double,
    gdd_arroz_c                     double
)
PARTITIONED BY (anio int, mes int)
STORED AS PARQUET
LOCATION 's3://sira-lakehouse-clima-929570730700/silver/clima_dia/';

MSCK REPAIR TABLE sira_clima_db.silver_clima_dia;

-- -----------------------------------------------------------------------------------------
-- 2. CAPA PLATA: Auditoría de Estaciones IDEAM (Demostración de vacíos en GS2 y sesgo)
-- -----------------------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS sira_clima_db.silver_auditoria_ideam (
    codigo_estacion                 string,
    nombre_estacion                 string,
    municipio                       string,
    distancia_nataima_km            double,
    temporada_evaluada              string,
    lluvia_nataima_campo_mm         double,
    lluvia_chirps_mm                double,
    lluvia_estacion_ideam_mm        double,
    dias_con_huecos_pct             double,
    sesgo_ideam_vs_campo_pct        double,
    sesgo_chirps_vs_campo_pct       double,
    veredicto_tecnico               string
)
STORED AS PARQUET
LOCATION 's3://sira-lakehouse-clima-929570730700/silver/auditoria_ideam/';

-- -----------------------------------------------------------------------------------------
-- 3. CAPA ORO: Tabla Maestra de Entrenamiento (Paso 4: Emparejar Parcela x Fecha +-1 día)
-- -----------------------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS sira_clima_db.gold_tabla_entrenamiento (
    tratamiento_riego               string,
    fecha                           string,
    medicion_id                     string,
    temporada                       string,
    fecha_siembra                   string,
    das                             bigint,
    etapa_fenologica                string,
    kc_fao56                        double,
    humedad_tdr_vwc_pct             double,
    error_estandar_tdr              double,
    riego_aplicado_mm               double,
    riego_acumulado_temporada_mm    double,
    clase_hidrica_real              string,
    franja_id                       string,
    distrito_riego                  string,
    municipio                       string,
    ancho_franja_m                  double,
    resolucion_pixel_m              double,
    capacidad_campo_fc_pct          double,
    punto_marchitez_pwp_pct         double,
    umbral_deficit_critico_pct      double,
    grid_id                         string,
    escena_id                       string,
    b3_verde_10m                    double,
    b4_rojo_10m                     double,
    b8_nir_10m                      double,
    b8a_nir_20m                     double,
    b11_swir1_20m                   double,
    b12_swir2_20m                   double,
    ndmi_observado                  double,
    ndmi_sin_bruma_ref              double,
    ndwi_observado                  double,
    ndvi_observado                  double,
    mndwi_observado                 double,
    aot_espesor_bruma               double,
    nubes_porcentaje                double,
    scl_clase_calidad               bigint,
    flag_bruma_detectada            boolean,
    es_campana_o_2026               boolean,
    lluvia_1d_mm                    double,
    lluvia_3d_mm                    double,
    lluvia_5d_mm                    double,
    lluvia_15d_mm                   double,
    swi_lluvia_exp_mm               double,
    eto_fao56_mm                    double,
    atwd_5d_mm                      double,
    deficit_presion_vapor           double,
    humedad_rel_2m_pct              double,
    presion_superficie_kpa          double,
    indice_claridad_kt              double,
    gdd_arroz_c                     double,
    ndmi_esperado_clima             double,
    residuo_bruma_ndmi              double,
    peso_confianza_optica           double,
    ndmi_tuned_clima                double,
    ndwi_tuned_clima                double,
    bloque_id                       string,
    resolucion_hls_m                double,
    tamano_parche_m                 double,
    prithvi_pca_01                  double,
    prithvi_pca_02                  double,
    prithvi_pca_03                  double,
    prithvi_pca_04                  double,
    prithvi_pca_05                  double,
    swi_total_lluvia_mas_riego_mm   double
)
STORED AS PARQUET
LOCATION 's3://sira-lakehouse-clima-929570730700/gold/tabla_entrenamiento/';

-- -----------------------------------------------------------------------------------------
-- 4. CAPA ORO: Predicción Operativa Diaria por Píxel de 10 m y Semáforo de Riego
-- -----------------------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS sira_clima_db.gold_prediccion_pixel_dia (
    pixel_id                        string,
    parcela_id                      string,
    grid_id                         string,
    distrito_riego                  string,
    municipio                       string,
    franja_id                       string,
    tratamiento_riego               string,
    replica                         bigint,
    lat_pixel                       double,
    lon_pixel                       double,
    fecha                           string,
    humedad_predicha_vwc_hoy_pct    double,
    humedad_proyectada_7d_pct       double,
    clase_semaforo_riego            string,
    lamina_recomendada_l_m2         double,
    volumen_recomendado_m3_ha       double,
    lluvia_5d_mm                    double,
    eto_fao56_mm                    double,
    atwd_5d_mm                      double,
    humedad_rel_2m_pct              double,
    presion_superficie_kpa          double,
    lluvia_pron_7d_mm               double,
    eto_pron_7d_mm                  double,
    confianza_7d_pct                double
)
STORED AS PARQUET
LOCATION 's3://sira-lakehouse-clima-929570730700/gold/prediccion_pixel_dia/';

-- -----------------------------------------------------------------------------------------
-- 5. CAPA ORO: Verificación Continua del Tuning contra Bruma Atmosférica
-- -----------------------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS sira_clima_db.gold_verificacion_diaria (
    medicion_id                     string,
    temporada                       string,
    fecha                           string,
    tratamiento_riego               string,
    etapa_fenologica                string,
    humedad_tdr_vwc_pct             double,
    ndmi_observado                  double,
    ndmi_esperado_clima             double,
    ndmi_tuned_clima                double,
    ndmi_sin_bruma_ref              double,
    flag_bruma_detectada            boolean,
    aot_espesor_bruma               double,
    indice_claridad_kt              double,
    lluvia_5d_mm                    double,
    eto_fao56_mm                    double,
    atwd_5d_mm                      double,
    error_abs_sin_tuning_bruma      double,
    error_abs_con_tuning_clima      double
)
STORED AS PARQUET
LOCATION 's3://sira-lakehouse-clima-929570730700/gold/verificacion_diaria/';

-- =========================================================================================
-- CONSULTAS DE DEMOSTRACIÓN PARA LA SUSTENTACIÓN (SI7006)
-- =========================================================================================

-- CONSULTA 1: Resumen Hidroclimático ("Clima por día": Lluvia, Humedad Relativa, Presión, ETo, AtWD)
SELECT
    municipio,
    anio,
    COUNT(*) AS dias_procesados,
    ROUND(SUM(lluvia_1d_mm), 1) AS lluvia_anual_chirps_mm,
    ROUND(AVG(humedad_rel_2m_pct), 1) AS humedad_relativa_prom_pct,
    ROUND(AVG(presion_superficie_kpa), 2) AS presion_superficial_prom_kpa,
    ROUND(AVG(eto_fao56_mm), 2) AS eto_fao56_diaria_prom_mm,
    ROUND(AVG(atwd_5d_mm), 2) AS atwd_5d_prom_mm
FROM sira_clima_db.silver_clima_dia
WHERE anio BETWEEN 2021 AND 2025
GROUP BY municipio, anio
ORDER BY municipio, anio;

-- CONSULTA 2: Demostración del Tuning contra Bruma Atmosférica en Nataima
SELECT
    temporada,
    flag_bruma_detectada,
    COUNT(*) AS total_pares_imagen_suelo,
    ROUND(AVG(aot_espesor_bruma), 3) AS aot_bruma_prom,
    ROUND(AVG(error_abs_sin_tuning_bruma), 4) AS mae_ndmi_sin_tuning,
    ROUND(AVG(error_abs_con_tuning_clima), 4) AS mae_ndmi_con_tuning_clima,
    ROUND((1.0 - AVG(error_abs_con_tuning_clima) / NULLIF(AVG(error_abs_sin_tuning_bruma), 0)) * 100.0, 1) AS reduccion_error_bruma_pct
FROM sira_clima_db.gold_verificacion_diaria
GROUP BY temporada, flag_bruma_detectada
ORDER BY temporada, flag_bruma_detectada DESC;

-- CONSULTA 3: Estado del Semáforo de Riego Hoy por Franja de Tratamiento (Píxeles de 10 m)
SELECT
    franja_id,
    tratamiento_riego,
    clase_semaforo_riego,
    COUNT(*) AS pixeles_10m,
    ROUND(AVG(humedad_predicha_vwc_hoy_pct), 2) AS vwc_predicha_hoy_pct,
    ROUND(AVG(humedad_proyectada_7d_pct), 2) AS vwc_proyectada_7d_pct,
    ROUND(AVG(volumen_recomendado_m3_ha), 1) AS agua_recomendada_m3_ha
FROM sira_clima_db.gold_prediccion_pixel_dia
GROUP BY franja_id, tratamiento_riego, clase_semaforo_riego
ORDER BY franja_id, clase_semaforo_riego;
