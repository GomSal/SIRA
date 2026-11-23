-- =========================================================================================
-- SIRA (Iteracion B Unificada) · DDL Completo y Consultas Analiticas en Amazon Athena
-- Base de datos: sira_b_lakehouse_db | Region AWS: us-east-2 (Ohio)
-- Bucket S3: s3://sira-b-lakehouse-929570730700/
-- Conectado a `GomSal/SIRA@modelo-humedad` (commits d77ae9c -> 876be25 + Glue 5.0 auto):
--   * Modelo A real (RF 200 arb. .joblib, sklearn 1.6.1) + Tramo 1 (CD, CHIRPS+POWER)
--   * Modelo B ARIMAX(1) dia 0 (arimax.json, sigma=3.12%, margen 4.68 mm)
--   * Propagacion Dr_modificada FAO-56 (hoy -> +30 d, 5m GEOS-S2S, sin riego, sin margen)
--   * 20.286 pixeles agricolas (6.761 en cultivo al 2026-11-19) + exportacion App Lovable (formato 2, 31 d)
-- =========================================================================================

CREATE DATABASE IF NOT EXISTS sira_b_lakehouse_db
COMMENT 'Lakehouse Medallion SIRA Iteracion B Unificada (A RF real + B ARIMAX dia 0 + Dr FAO-56 + App Lovable formato 2)';

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
-- 2. PLATA: Clima Observado Diario (2001-2026: CHIRPS + NASA POWER FAO-56 con relleno POWER)
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
-- 7. ORO: Curva Normal de 10 Anos por DAS (Control anomalia NDMI fitosanitaria)
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
-- 9. ORO: Semaforo Modelo B Historico Nataima 1 ha (Trazabilidad ARIMAX(1) + S_tau45/60_a05)
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
-- 10. ORO: Semaforo Unificado Producto (A RF + B ARIMAX dia 0 + Dr FAO-56) — 20.286 Pixeles
--          Esquema oficial 40 columnas (predio_id + 39 columnas de d77ae9c / sira-b-05)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_semaforo_c_pixeles;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_semaforo_c_pixeles (
    predio_id                   string,
    pixel                       bigint,
    das_hoy                     double,
    dias_hasta_baja             int,
    clase_h0                    int,
    humedad_pct_h0              double,
    deficit_B_sin_margen_mm_h0  double,
    deficit_mm_h0               double,
    S_tau20_h0                  double,
    dr_ecuacion_mm_h0           double,
    ndvi_h0                     double,
    ndmi_h0                     double,
    marca_h0                    boolean,
    clase_h7                    int,
    humedad_pct_h7              double,
    deficit_B_sin_margen_mm_h7  double,
    deficit_mm_h7               double,
    S_tau20_h7                  double,
    dr_ecuacion_mm_h7           double,
    ndvi_h7                     double,
    ndmi_h7                     double,
    marca_h7                    boolean,
    clase_h15                   int,
    humedad_pct_h15             double,
    deficit_B_sin_margen_mm_h15 double,
    deficit_mm_h15              double,
    S_tau20_h15                 double,
    dr_ecuacion_mm_h15          double,
    ndvi_h15                    double,
    ndmi_h15                    double,
    marca_h15                   boolean,
    clase_h30                   int,
    humedad_pct_h30             double,
    deficit_B_sin_margen_mm_h30 double,
    deficit_mm_h30              double,
    S_tau20_h30                 double,
    dr_ecuacion_mm_h30          double,
    ndvi_h30                    double,
    ndmi_h30                    double,
    marca_h30                   boolean
)
PARTITIONED BY (emision string)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/semaforo_c_pixeles/';
MSCK REPAIR TABLE sira_b_lakehouse_db.oro_semaforo_c_pixeles;

-- -----------------------------------------------------------------------------------------
-- 11. ORO: Semaforo Unificado Resumen Zonal por Horizonte (h = 0, 7, 15, 30 dias · 400 ha)
-- -----------------------------------------------------------------------------------------
DROP TABLE IF EXISTS sira_b_lakehouse_db.oro_semaforo_c_resumen;
CREATE EXTERNAL TABLE sira_b_lakehouse_db.oro_semaforo_c_resumen (
    predio_id                    string,
    hoy                          string,
    horizonte                    int,
    fecha                        string,
    zona_ha                      double,
    ha_en_cultivo                double,
    lluvia_mm_hasta_h            double,
    eto_mm_hasta_h               double,
    pct_suficiente               double,
    ha_suficiente                double,
    pct_media                    double,
    ha_media                     double,
    deficit_media_L_m2           double,
    agua_media_m3_zona           double,
    dias_hasta_baja_mediana      double,
    pct_baja                     double,
    ha_baja                      double,
    deficit_baja_L_m2            double,
    agua_baja_m3_zona            double,
    pct_marca_fitosanitaria      double,
    modelo_b_sigma_pct           double,
    modelo_b_margen_mm           double,
    riego_supuesto               string,
    lluvia_hueco_dias_sin_chirps int,
    lluvia_hueco_relleno         string
)
PARTITIONED BY (emision string)
STORED AS PARQUET
LOCATION 's3://sira-b-lakehouse-929570730700/oro/semaforo_c_resumen/';
MSCK REPAIR TABLE sira_b_lakehouse_db.oro_semaforo_c_resumen;

-- -----------------------------------------------------------------------------------------
-- 12. ORO: Verificacion Completa (6 bloques: GEOS, A abs, B adel, B ROC, Etapa 1, Dinamica)
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
-- 6 CONSULTAS ANALITICAS VERIFICADAS EN VIVO (SIRA B UNIFICADO · ML <-> BIG DATA)
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

-- CONSULTA 3: Serie climatica observada 2001-2026 (CHIRPS + relleno POWER -> plata_clima_observado)
SELECT
    anio,
    COUNT(*) AS dias,
    ROUND(SUM(lluvia_mm), 1) AS lluvia_anual_mm,
    ROUND(AVG(eto_mm), 2) AS eto_media_mm_d,
    ROUND(AVG(tmed_c), 2) AS tmed_c
FROM sira_b_lakehouse_db.plata_clima_observado
GROUP BY anio
ORDER BY anio DESC
LIMIT 6;

-- CONSULTA 4: Semaforo Unificado (A RF + B ARIMAX dia 0 + Dr FAO-56) — Resumen Zonal 400 ha
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
    pct_marca_fitosanitaria,
    modelo_b_margen_mm,
    lluvia_hueco_dias_sin_chirps,
    lluvia_hueco_relleno
FROM sira_b_lakehouse_db.oro_semaforo_c_resumen
ORDER BY emision DESC, horizonte;

-- CONSULTA 5: Semaforo Unificado — Distribucion de 20.286 pixeles por dias hasta humedad baja
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
    ROUND(AVG(das_hoy), 1) AS das_hoy_medio,
    ROUND(AVG(humedad_pct_h0), 2) AS humedad_hoy_pct,
    ROUND(AVG(deficit_mm_h0), 2) AS deficit_hoy_con_margen_mm,
    ROUND(AVG(deficit_B_sin_margen_mm_h0), 2) AS deficit_B_sin_margen_mm,
    ROUND(AVG(ndmi_h0), 3) AS ndmi_hoy_medio
FROM sira_b_lakehouse_db.oro_semaforo_c_pixeles
WHERE emision = (SELECT MAX(emision) FROM sira_b_lakehouse_db.oro_semaforo_c_pixeles)
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

-- CONSULTA 6: Tablero Consolidado de Verificacion (6 bloques: GEOS, A, B ROC, Etapa 1 y Prueba Dinamica)
SELECT
    ciclo,
    horizonte_grupo,
    metrica_principal,
    ROUND(valor_crudo_o_base, 3) AS base_o_crudo,
    ROUND(valor_corregido_o_modelo, 3) AS modelo_o_corregido,
    observacion
FROM sira_b_lakehouse_db.oro_verificacion_clima_cultivo
ORDER BY ciclo, horizonte_grupo;
