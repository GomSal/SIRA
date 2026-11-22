-- =========================================================================================
-- SIRA (Iteracion B — B_SIRA_ARCH) · DDL Completo y Consultas Analiticas en Amazon Athena
-- Base de datos: sira_b_lakehouse_db | Region AWS: us-east-2 (Ohio)
-- Bucket S3: s3://sira-b-lakehouse-929570730700/
-- Alineado a extremo detalle a https://github.com/GomSal/SIRA/tree/modelo-humedad (HEAD 8fde03d)
-- Incluye: Modelo A (RF 20 features), Modelo B oficial (ARIMAX(1) + Reentreno Etapa 1 S_tau45/60_a0.5)
--          y Modelo C operativo sin riego registrado (20.286 pixeles, 400 ha, control NDMI fitosanitario).
-- =========================================================================================

CREATE DATABASE IF NOT EXISTS sira_b_lakehouse_db
COMMENT 'Lakehouse Medallion SIRA Iteracion B (HEAD 8fde03d): Modelo A (RF NDVI/NDMI 1-30d) + GEOS-S2S-3 (5m) + Modelo B (ARIMAX(1) + S_tau45_a05) + Modelo C operativo (20.286 pixeles)';

-- -----------------------------------------------------------------------------------------
-- 1. PLATA: Series de indices y 20 features agregados por pixel (Lote 1 Diario)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.plata_indices_pixel;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.plata_indices_pixel (
    pixel_id                string,
    sensor                  string,
    plataforma              string,
    sensor0                 int,
    franja_fisica           string,
    pureza_franja           double,
    ndvi0                   double,
    ndmi0                   double,
    azul_b02                double,
    origen_valor            string,
    flag_bruma              boolean,
    ndvi_l1                 double,
    edad_l1                 double,
    ndmi_l1                 double,
    ndvi_l2                 double,
    edad_l2                 double,
    pend_ndvi               double,
    pend_ndmi               double,
    fecha_siembra_est       string,
    das0                    double,
    etapa0                  int,
    doy_sin                 double,
    doy_cos                 double
)
PARTITIONED BY (predio_id string, fecha string)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/plata/indices_pixel/';
MSCK REPAIR TABLE sira_b_lakehouse_db.plata_indices_pixel;

-- -----------------------------------------------------------------------------------------
-- 2. PLATA: Clima Observado Diario (CHIRPS + NASA POWER FAO-56)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.plata_clima_observado;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.plata_clima_observado (
    celda_clima_id          string,
    lat_celda               double,
    lon_celda               double,
    fecha                   string,
    T2M_MAX                 double,
    T2M_MIN                 double,
    RH2M                    double,
    WS2M                    double,
    ALLSKY_SFC_SW_DWN       double,
    PRECTOTCORR             double,
    lluvia_mm               double,
    eto_mm                  double,
    tmed_c                  double,
    doy                     int,
    lluvia_5d_mm            double,
    eto_5d_mm               double,
    lluvia_10d_mm           double,
    eto_10d_mm              double
)
PARTITIONED BY (anio int)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/plata/clima_observado/';
MSCK REPAIR TABLE sira_b_lakehouse_db.plata_clima_observado;

-- -----------------------------------------------------------------------------------------
-- 3. PLATA: GEOS-S2S-3 Crudo pivoteado (antes de correccion de sesgo)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.plata_geos_s2s_crudo;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.plata_geos_s2s_crudo (
    celda_clima_id          string,
    miembro                 int,
    fecha_objetivo          string,
    plazo                   int,
    mes                     int,
    doy                     int,
    plazo_bin               int,
    pr_raw_mm               double,
    tx_raw_c                double,
    tn_raw_c                double
)
PARTITIONED BY (emision string)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/plata/geos_s2s_crudo/';
MSCK REPAIR TABLE sira_b_lakehouse_db.plata_geos_s2s_crudo;

-- -----------------------------------------------------------------------------------------
-- 4. PLATA: Estado Hidrico Diario Arrastrado por Pixel (ARIMAX B + Reentreno S_tau45/60_a05)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.plata_estado_hidrico_pixel;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.plata_estado_hidrico_pixel (
    predio_id               string,
    pixel_id                string,
    fecha_corte             string,
    franja_fisica           string,
    s_tau20_init            double,
    s_tau5_init             double,
    dr_k05_init             double,
    s_tau45_a05_init        double,
    s_tau60_a05_init        double,
    dias_desde_riego_init   int,
    riego_4d_prev_sum       double,
    eto_4d_prev_sum         double,
    ndvi_ultimo             double,
    ndmi_ultimo             double
)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/plata/estado_hidrico_pixel/';

-- -----------------------------------------------------------------------------------------
-- 5. ORO: Coeficientes Versionados de Correccion de Sesgo GEOS-S2S-3 (Lote 3 Mensual)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_correccion_coeficientes;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_correccion_coeficientes (
    variable                string,
    mes                     int,
    plazo                   int,
    factor                  double,
    periodo_ajuste          string
)
PARTITIONED BY (version string)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/correccion_coeficientes/';
MSCK REPAIR TABLE sira_b_lakehouse_db.oro_correccion_coeficientes;

-- -----------------------------------------------------------------------------------------
-- 6. ORO: Clima Pronosticado GEOS-S2S-3 Corregido (5 miembros x 1-81d, Lote 2)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_geos_s2s_corregido;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_geos_s2s_corregido (
    celda_clima_id          string,
    miembro                 int,
    fecha_objetivo          string,
    plazo                   int,
    pr_raw_mm               double,
    lluvia_corr_mm          double,
    tx_corr_c               double,
    tn_corr_c               double,
    eto_corr_mm             double,
    eto_es_relleno_clim     boolean
)
PARTITIONED BY (emision string)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/geos_s2s_corregido/';
MSCK REPAIR TABLE sira_b_lakehouse_db.oro_geos_s2s_corregido;

-- -----------------------------------------------------------------------------------------
-- 7. ORO: Curva Normal de 10 Anos por DAS (Insumo dia 0 y control anomalia NDMI de Modelo C)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_curva_normal_10anios;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_curva_normal_10anios (
    das_b                   double,
    ndvi0_mean              double,
    ndvi0_std               double,
    ndvi0_size              bigint,
    ndmi0_mean              double,
    ndmi0_std               double,
    ndmi0_size              bigint,
    das_centro              double
)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/curva_normal_10anios/';

-- -----------------------------------------------------------------------------------------
-- 8. ORO: Pronostico Modelo A (NDVI y NDMI por pixel, h = 1..30d, sin clima/Prithvi)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_pronostico_predictor;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_pronostico_predictor (
    predio_id               string,
    pixel_id                string,
    horizonte               int,
    fecha_objetivo          string,
    ndvi0                   double,
    ndmi0                   double,
    ndvi_pred               double,
    ndmi_pred               double,
    das1                    double
)
PARTITIONED BY (emision string)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/pronostico_predictor/';
MSCK REPAIR TABLE sira_b_lakehouse_db.oro_pronostico_predictor;

-- -----------------------------------------------------------------------------------------
-- 9. ORO: Semaforo Modelo B Oficial (ARIMAX(1) sin Prithvi + S_tau45_a05 / S_tau60_a05)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_semaforo;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_semaforo (
    predio_id               string,
    pixel_id                string,
    franja_fisica           string,
    miembro                 int,
    escenario_riego         string,
    horizonte               int,
    fecha_objetivo          string,
    ndvi_pred               double,
    kc                      double,
    s_tau20                 double,
    s_tau5                  double,
    dr_k05                  double,
    s_tau45_a05             double,
    s_tau60_a05             double,
    riego_5d                double,
    dias_desde_riego        int,
    eto_5d                  double,
    vwc_esperada_pct        double,
    p_verde                 double,
    p_amarillo              double,
    p_rojo                  double,
    clase_semaforo          string
)
PARTITIONED BY (emision string)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/semaforo/';
MSCK REPAIR TABLE sira_b_lakehouse_db.oro_semaforo;

-- -----------------------------------------------------------------------------------------
-- 10. ORO: Semaforo C Operativo sin Riego — 20.286 Pixeles Reales (commit 1a592aa, 400 ha)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_semaforo_c_pixeles;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_semaforo_c_pixeles (
    predio_id               string,
    pixel                   bigint,
    dr0_mm                  double,
    dias_hasta_baja         int,
    clase_h0                int,
    dr_mm_h0                double,
    p_baja_h0               double,
    ndvi_h0                 double,
    ndmi_h0                 double,
    marca_h0                boolean,
    clase_h7                int,
    dr_mm_h7                double,
    p_baja_h7               double,
    ndvi_h7                 double,
    ndmi_h7                 double,
    marca_h7                boolean,
    clase_h15               int,
    dr_mm_h15               double,
    p_baja_h15              double,
    ndvi_h15                double,
    ndmi_h15                double,
    marca_h15               boolean,
    clase_h30               int,
    dr_mm_h30               double,
    p_baja_h30              double,
    ndvi_h30                double,
    ndmi_h30                double,
    marca_h30               boolean
)
PARTITIONED BY (emision string)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/semaforo_c_pixeles/';
MSCK REPAIR TABLE sira_b_lakehouse_db.oro_semaforo_c_pixeles;

-- -----------------------------------------------------------------------------------------
-- 11. ORO: Semaforo C Resumen Zonal por Horizonte (h = 0, 7, 15, 30 dias)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_semaforo_c_resumen;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_semaforo_c_resumen (
    predio_id               string,
    hoy                     string,
    horizonte               int,
    fecha                   string,
    zona_ha                 double,
    ha_en_cultivo           double,
    lluvia_mm_hasta_h       double,
    eto_mm_hasta_h          double,
    pct_suficiente          double,
    ha_suficiente           double,
    pct_media               double,
    ha_media                double,
    deficit_media_L_m2      double,
    agua_media_m3_zona      double,
    dias_hasta_baja_mediana double,
    pct_baja                double,
    ha_baja                 double,
    deficit_baja_L_m2       double,
    agua_baja_m3_zona       double,
    pct_marca_fitosanitaria double
)
PARTITIONED BY (emision string)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/semaforo_c_resumen/';
MSCK REPAIR TABLE sira_b_lakehouse_db.oro_semaforo_c_resumen;

-- -----------------------------------------------------------------------------------------
-- 12. ORO: Verificacion Completa (A absolutas, B familias ROC, Reentreno B Etapa 1, GEOS, C)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_verificacion_clima_cultivo;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_verificacion_clima_cultivo (
    ciclo                   string,
    periodo                 string,
    horizonte_grupo         string,
    n_observaciones         int,
    metrica_principal       string,
    valor_crudo_o_base      double,
    valor_corregido_o_modelo double,
    observacion             string
)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/verificacion_clima_cultivo/';

-- -----------------------------------------------------------------------------------------
-- 13. ORO: Catalogo Maestro de Predios (1 predio Nataima -> 100 predios)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_predios;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_predios (
    predio_id               string,
    nombre_predio           string,
    municipio               string,
    departamento            string,
    distrito_riego          string,
    lat                     double,
    lon                     double,
    crs_utm                 string,
    area_lote_ha            double,
    area_zona_c_ha          double,
    celda_clima_id          string,
    lat_celda_geos          double,
    lon_celda_geos          double,
    fc_pct                  double,
    pwp_pct                 double,
    umbral_rojo_pct         double,
    umbral_verde_pct        double,
    taw_mm                  double,
    p_agotamiento           double,
    raw_mm                  double,
    dr_rojo_mm              double,
    zr_mm                   double,
    dsat_mm                 double,
    k_drenaje               double,
    modo_computo_recomendado string
)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/predios/';

-- =========================================================================================
-- 6 CONSULTAS ANALITICAS VERIFICADAS EN VIVO (B_SIRA_ARCH · HEAD 8fde03d)
-- =========================================================================================

-- CONSULTA 1: Calidad satelital y 20 features Modelo A (Lote 1 -> plata_indices_pixel)
SELECT
    predio_id,
    sensor,
    origen_valor,
    COUNT(*) AS registros_pixel_fecha,
    ROUND(AVG(ndvi0), 3) AS ndvi_prom,
    ROUND(AVG(ndmi0), 3) AS ndmi_prom,
    ROUND(AVG(edad_l1), 1) AS edad_l1_dias_prom
FROM sira_b_lakehouse_db.plata_indices_pixel
GROUP BY predio_id, sensor, origen_valor
ORDER BY sensor, origen_valor;

-- CONSULTA 2: Correccion de sesgo GEOS-S2S-3 sobre 5 miembros (Lote 2 -> oro_geos_s2s_corregido)
SELECT
    emision,
    COUNT(DISTINCT miembro) AS miembros,
    COUNT(*) AS filas_pronostico,
    ROUND(AVG(pr_raw_mm), 2) AS lluvia_cruda_mm_d,
    ROUND(AVG(lluvia_corr_mm), 2) AS lluvia_corregida_mm_d,
    ROUND(AVG(eto_corr_mm), 2) AS eto_corregida_mm_d
FROM sira_b_lakehouse_db.oro_geos_s2s_corregido
GROUP BY emision
ORDER BY emision DESC
LIMIT 5;

-- CONSULTA 3: Modelo B Oficial (ARIMAX(1)) + Reservorios Reentreno Etapa 1 (S_tau45_a05 / S_tau60_a05)
SELECT
    escenario_riego,
    CASE
        WHEN horizonte <= 5 THEN '01-05 dias'
        WHEN horizonte <= 15 THEN '06-15 dias'
        ELSE '16-30 dias'
    END AS ventana_horizonte,
    ROUND(AVG(vwc_esperada_pct), 2) AS vwc_esperada_pct,
    ROUND(AVG(p_rojo), 3) AS prob_rojo_media,
    ROUND(AVG(s_tau45_a05), 1) AS s_tau45_a05_prom,
    ROUND(AVG(s_tau60_a05), 1) AS s_tau60_a05_prom
FROM sira_b_lakehouse_db.oro_semaforo
GROUP BY escenario_riego,
    CASE
        WHEN horizonte <= 5 THEN '01-05 dias'
        WHEN horizonte <= 15 THEN '06-15 dias'
        ELSE '16-30 dias'
    END
ORDER BY escenario_riego, ventana_horizonte;

-- CONSULTA 4: Modelo C Operativo sin Riego — Resumen Zonal 400 ha (h = 0, 7, 15, 30 dias)
SELECT
    emision,
    horizonte,
    fecha,
    ha_en_cultivo,
    pct_suficiente,
    pct_media,
    pct_baja,
    deficit_baja_L_m2,
    agua_baja_m3_zona,
    pct_marca_fitosanitaria
FROM sira_b_lakehouse_db.oro_semaforo_c_resumen
ORDER BY horizonte;

-- CONSULTA 5: Modelo C Operativo — Distribucion de 20.286 pixeles por dias hasta humedad baja
SELECT
    CASE
        WHEN clase_h0 < 0 THEN 'fuera_de_cultivo'
        WHEN dias_hasta_baja = 0 THEN '0_ya_hoy'
        WHEN dias_hasta_baja BETWEEN 1 AND 2 THEN '1_de_1_a_2_dias'
        WHEN dias_hasta_baja BETWEEN 3 AND 5 THEN '2_de_3_a_5_dias'
        WHEN dias_hasta_baja >= 6 THEN '3_de_6_a_30_dias'
        ELSE '4_no_llega_en_30d'
    END AS urgencia_riego,
    COUNT(*) AS pixeles_10m,
    ROUND(COUNT(*) / 100.0, 2) AS hectareas,
    ROUND(AVG(dr0_mm), 2) AS dr0_medio_mm,
    ROUND(AVG(ndmi_h0), 3) AS ndmi_hoy_medio
FROM sira_b_lakehouse_db.oro_semaforo_c_pixeles
GROUP BY
    CASE
        WHEN clase_h0 < 0 THEN 'fuera_de_cultivo'
        WHEN dias_hasta_baja = 0 THEN '0_ya_hoy'
        WHEN dias_hasta_baja BETWEEN 1 AND 2 THEN '1_de_1_a_2_dias'
        WHEN dias_hasta_baja BETWEEN 3 AND 5 THEN '2_de_3_a_5_dias'
        WHEN dias_hasta_baja >= 6 THEN '3_de_6_a_30_dias'
        ELSE '4_no_llega_en_30d'
    END
ORDER BY urgencia_riego;

-- CONSULTA 6: Tablero Consolidado de Verificacion (A absolutas, B ROC, Reentreno B Etapa 1, GEOS)
SELECT
    ciclo,
    horizonte_grupo,
    metrica_principal,
    ROUND(valor_crudo_o_base, 3) AS base_o_crudo,
    ROUND(valor_corregido_o_modelo, 3) AS modelo_o_corregido,
    observacion
FROM sira_b_lakehouse_db.oro_verificacion_clima_cultivo
ORDER BY ciclo, horizonte_grupo;
