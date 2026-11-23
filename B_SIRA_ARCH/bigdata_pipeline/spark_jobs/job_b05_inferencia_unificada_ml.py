# -*- coding: utf-8 -*-
"""
SIRA (Iteracion B Unificada) · AWS Glue 5.0 Job B05: Inferencia Unificada ML (A + B + FAO-56 + App)
====================================================================================================
Conecta directamente la arquitectura Big Data en AWS (s3://sira-b-lakehouse-929570730700/) con los
artefactos reales de Machine Learning de `GomSal/SIRA@modelo-humedad` (commits d77ae9c -> 86aa09b):

Entradas reales desde S3 (prefijos oficiales de ML + Capa Oro Big Data):
  1. `models/pronosticador_a/rf/`   : predictor_ndvi.joblib, predictor_ndmi.joblib (RF 200 arb., sklearn 1.6.1),
                                      meta.json, paso1_curva_por_dias_desde_siembra.csv,
                                      paso2_calibracion_landsat_s2.csv, paso8_geos_correccion.csv,
                                      paso8_prueba_clima_observado.csv.
  2. `models/semaforo_b/operacion/` : arimax.json (sigma = 3.12 %), calendarios_riego.csv (CD/MIRI/AWD+MIRI).
  3. `plata/cubo_a/`                : cubo.npz (400 ha, 10 m), cubo_fechas.csv, clima.csv (CHIRPS + POWER).
  4. `oro/geos_s2s_corregido/`      : GEOS-S2S-3 corregido producido por `sira-b-02` (con fallback a
                                      `bronce/geos_s2s_a/geos_nrt.csv` + `paso8_geos_correccion.csv`).

Logica exacta ejecutada por pixel (400 ha, 20.286 pixeles agricolas, 6.868 en cultivo):
  - Modelo A (RF real .joblib)      : detecta siembra por curva NDVI y pronostica NDVI/NDMI h = 0..30 d.
  - Tramo 1 (siembra -> hoy)        : b_entradas.propagar() con CHIRPS (relleno POWER PRECTOTCORR en huecos),
                                      ETo NASA POWER, NDVI observado diario y 3 calendarios CD de Nataima.
  - Dia 0 (hoy) — Modelo B ARIMAX(1): corrige humedad esperada mu0 y aplica margen 1 vez:
                                      Dr0 = 1.5*(30 - mu0) + 1.5*sigma (margen 4.68 mm).
  - Tramo 2 (hoy -> +30 d)          : Dr_modificada FAO-56 (TAW=6.7, p=0.65, k=0.5, sin riego, sin margen)
                                      promediada sobre 5 miembros de GEOS-S2S-3 + NDVI de A.
  - Alarma tardia NDMI              : marca morada sobre pixeles verdes (no altera media -> baja).

Salidas escritas atomicamente en S3:
  1. Parquet Snappy particionado para Athena (`sira_b_lakehouse_db`):
     - `oro/semaforo_c_pixeles/emision=<hoy>/part-00000.parquet` (40 cols: predio_id + 39 cols ML)
     - `oro/semaforo_c_resumen/emision=<hoy>/part-00000.parquet` (4 horizontes + metadatos Modelo B y hueco lluvia)
  2. Artefactos para App Lovable (`oro/semaforo_app/`):
     - `emision=<hoy>/semaforo.json` (formato: 2, 31 dias h=0..30, 6.868 pixeles en cultivo con lat/lon)
     - `emision=<hoy>/simulador_riego.json` (insumo compacto para Lambda POST /regar_hoy en <50 ms)
     - `emision=<hoy>/resumen.json`, `pixeles.csv.gz`, `mapa.html`, `semaforo_h{00,07,15,30}.png`
     - `latest.json` + `manifiestos/corrida_glue_<ts>.json` (con SHA-256)
  3. Espejo de mapas actualizados en `oro/semaforo_c_mapas/`.
"""

import hashlib
import json
import os
import sys
import tempfile
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import boto3
import joblib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image
from pyproj import Transformer

warnings.filterwarnings("ignore")

# Constantes fisicas y agronomicas oficiales (Martinez-Vega et al. 2025 + docs/esquema_semaforo.md)
BASE = pd.Timestamp("2000-01-01")
SELLO = pd.Timestamp("2025-11-18")
ANIOS_CLIM = 10
TAW, P_AG, ZR = 6.7, 0.65, 150.0
RAW = P_AG * TAW
DSAT = ZR * (0.4765 - 0.30)  # 26.475 mm
K_DREN = 0.5
CC, DR_ROJO, MM_POR_PUNTO = 30.0, 4.34, ZR / 100.0
Z_SECO = -1.28
HORIZONTE = 30
ENTRADAS_B = ["S_tau20", "S_tau5", "Dr_k0.5", "riego_5d", "dias_desde_riego", "eto_5d", "ndvi"]
SENS = {"S2": 0, "landsat-7": 1, "landsat-8": 2, "landsat-9": 2}
CLASES = {0: "suficiente", 1: "media", 2: "baja"}
COLORES = {
    0: (46, 158, 91, 190),
    1: (242, 194, 48, 200),
    2: (224, 75, 75, 210),
    "marca": (142, 68, 173, 210),
}
CONTENT_TYPES = {
    ".json": "application/json",
    ".csv": "text/csv",
    ".png": "image/png",
    ".html": "text/html; charset=utf-8",
    ".gz": "application/gzip",
    ".parquet": "application/octet-stream",
}

T0 = time.time()


def log(msg: str):
    print(f"[sira-b-05 | {time.time() - T0:6.1f}s] {msg}", flush=True)


def parse_args(argv):
    defaults = {
        "JOB_NAME": "sira-b-05-inferencia-unificada-ml",
        "s3_bucket": "sira-b-lakehouse-929570730700",
        "aws_region": "us-east-2",
        "athena_db": "sira_b_lakehouse_db",
        "predio_id": "NATAIMA_01",
        "fecha_hoy": "auto",
        "riego_supuesto": "CD",
        "lat": "4.1895",
        "lon": "-74.9621",
        "lado_km": "2.0",
        "prefer_oro_geos": "true",
        "prefix_cubo_a": "plata/cubo_a",
        "prefix_modelo_a": "models/pronosticador_a/rf",
        "prefix_modelo_b": "models/semaforo_b/operacion",
        "key_geos_nrt": "bronce/geos_s2s_a/geos_nrt.csv",
        "prefix_oro_geos": "oro/geos_s2s_corregido",
        "prefix_oro_pixeles": "oro/semaforo_c_pixeles",
        "prefix_oro_resumen": "oro/semaforo_c_resumen",
        "prefix_oro_app": "oro/semaforo_app",
        "prefix_oro_mapas": "oro/semaforo_c_mapas",
    }
    parsed = dict(defaults)
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            key = tok[2:]
            if "=" in key:
                k, v = key.split("=", 1)
                parsed[k] = v
            elif i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                parsed[key] = argv[i + 1]
                i += 1
        i += 1
    return parsed


# ----------------------------------------------------------------------------
# Ecuaciones exactas de scripts/operacion/b_entradas.py (commit d77ae9c)
# ----------------------------------------------------------------------------
def propagar(L, ETO, R, NDVI, activo):
    forma = L.shape[1:]
    s20 = np.zeros(forma, dtype=float)
    s5 = np.zeros(forma, dtype=float)
    dr = np.zeros(forma, dtype=float)
    n = np.full(forma, 60.0, dtype=float)
    r_ult, e_ult = [], []
    f20, f5 = np.exp(-1.0 / 20.0), np.exp(-1.0 / 5.0)
    out = {c: np.zeros(L.shape, dtype=float) for c in ENTRADAS_B}
    for t in range(L.shape[0]):
        a = activo[t]
        kc = np.clip(1.0 * NDVI[t] + 0.45, 0.6, 1.3)
        entra, sale = L[t] + R[t], kc * ETO[t]
        s20 = np.where(a, s20 * f20 + entra - sale, 0.0)
        s5 = np.where(a, s5 * f5 + entra - sale, 0.0)
        d = dr - entra
        ks = np.where(d <= RAW, 1.0, np.clip((TAW - d) / ((1.0 - P_AG) * TAW), 0.0, None))
        d = d + ks * sale
        d = np.where(d < 0.0, d * (1.0 - K_DREN), d)
        dr = np.where(a, np.clip(d, -DSAT, TAW), 0.0)
        n = np.where(a, np.where(R[t] > 0.0, 0.0, np.minimum(n + 1.0, 60.0)), 60.0)
        r_ult = (r_ult + [np.where(a, R[t], 0.0)])[-5:]
        e_ult = (e_ult + [np.where(a, ETO[t], 0.0)])[-5:]
        for c, v in (
            ("S_tau20", s20),
            ("S_tau5", s5),
            ("Dr_k0.5", dr),
            ("dias_desde_riego", n),
            ("riego_5d", sum(r_ult)),
            ("eto_5d", sum(e_ult)),
            ("ndvi", NDVI[t]),
        ):
            out[c][t] = v
    return out


def propagar_dr(dr0, L, ETO, NDVI):
    dr = np.clip(np.broadcast_to(dr0, L.shape[1:]).astype(float), -DSAT, TAW)
    out = np.zeros(L.shape, dtype=float)
    for t in range(L.shape[0]):
        kc = np.clip(1.0 * NDVI[t] + 0.45, 0.6, 1.3)
        d = dr - L[t]
        ks = np.where(d <= RAW, 1.0, np.clip((TAW - d) / ((1.0 - P_AG) * TAW), 0.0, None))
        d = d + ks * kc * ETO[t]
        d = np.where(d < 0.0, d * (1.0 - K_DREN), d)
        dr = np.clip(d, -DSAT, TAW)
        out[t] = dr
    return out


class ArimaxB:
    def __init__(self, ruta: Path):
        m = json.loads(ruta.read_text(encoding="utf-8"))
        assert m["entradas"] == ENTRADAS_B, "Orden de entradas ARIMAX no coincide"
        self.m = m
        self.media = np.array(m["media_x"], dtype=float)
        self.desv = np.array(m["desv_x"], dtype=float)
        self.beta = np.array(m["beta"], dtype=float)
        self.c = float(m["intercepto"])
        self.phi = float(m["phi"])
        self.sigma = float(m["sigma_pct"])

    def mu(self, X: dict):
        z = sum(b * (X[c] - mu_) / sd for b, c, mu_, sd in zip(self.beta, ENTRADAS_B, self.media, self.desv))
        return z + self.c / (1.0 - self.phi)


# ----------------------------------------------------------------------------
# Deteccion de siembra y features de Modelo A (exactas a semaforo_c.py)
# ----------------------------------------------------------------------------
def siembras(d, v):
    out, k = [], 0
    while k < len(v):
        if v[k] <= 0.35:
            j = k + 1
            while j < len(v) and d[j] - d[k] <= 90 and v[j] < 0.65:
                if v[j] <= 0.35:
                    k = j
                j += 1
            if j < len(v) and v[j] >= 0.65 and 20 <= d[j] - d[k] <= 90:
                out.append(d[k])
                k = j
                while k < len(v) and v[k] > 0.35:
                    k += 1
                continue
        k += 1
    return np.array(out, dtype=int)


def das_de(s, dias):
    if len(s) == 0:
        return np.full(len(dias), np.nan)
    i = np.searchsorted(s, dias, side="right") - 1
    out = np.where(i >= 0, dias - s[np.clip(i, 0, None)], np.nan).astype(float)
    out[(out > 150) | (out < 0)] = np.nan
    return out


def etapa_de(das):
    return np.select([np.isnan(das), das <= 45, das <= 82, das <= 130], [0, 1, 2, 3], 0)


# ----------------------------------------------------------------------------
# Utilidades S3 y Glue Catalog
# ----------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload_with_sha256(s3, bucket: str, key: str, local_path: Path, manifiesto: list):
    h = sha256_file(local_path)
    n = local_path.stat().st_size
    ct = CONTENT_TYPES.get(local_path.suffix, "application/octet-stream")
    s3.upload_file(
        str(local_path),
        bucket,
        key,
        ExtraArgs={"Metadata": {"sha256": h}, "ContentType": ct},
    )
    manifiesto.append({"clave": key, "bytes": n, "sha256": h})
    return h, n


def clear_s3_prefix(s3, bucket: str, prefix: str):
    pref = prefix.rstrip("/") + "/"
    paginator = s3.get_paginator("list_objects_v2")
    to_del = []
    for page in paginator.paginate(Bucket=bucket, Prefix=pref):
        for obj in page.get("Contents", []):
            to_del.append({"Key": obj["Key"]})
    for i in range(0, len(to_del), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": to_del[i : i + 1000]})
    return len(to_del)


def load_geos_from_oro_or_bronce(s3, bucket: str, work: Path, args: dict, cl: pd.DataFrame, HOY: pd.Timestamp):
    """Carga GEOS-S2S-3 corregido (5 miembros x 30 d). Prioriza oro/geos_s2s_corregido si coincide emision."""
    per = cl.loc[SELLO - pd.DateOffset(years=ANIOS_CLIM) : SELLO - pd.Timedelta(days=1)]
    CLIM_ETO = per.eto.groupby(per.index.day_of_year).mean()
    CLIM_LL = per.lluvia.groupby(per.index.day_of_year).mean()
    CLIM_T = ((per.T2M_MAX + per.T2M_MIN) / 2.0).groupby(per.index.day_of_year).mean()
    FECHAS = pd.date_range(HOY + pd.Timedelta(days=1), periods=HORIZONTE)

    prefer_oro = str(args.get("prefer_oro_geos", "true")).strip().lower() == "true"
    if prefer_oro:
        try:
            pref = args["prefix_oro_geos"].rstrip("/") + "/"
            resp = s3.list_objects_v2(Bucket=bucket, Prefix=pref, Delimiter="/")
            em_dirs = []
            for cp in resp.get("CommonPrefixes", []):
                part = cp["Prefix"].rstrip("/").split("/")[-1]
                if part.startswith("emision="):
                    em_str = part.split("=", 1)[1]
                    try:
                        em_dt = pd.Timestamp(em_str)
                        if em_dt <= HOY:
                            em_dirs.append((em_dt, cp["Prefix"]))
                    except Exception:
                        pass
            if em_dirs:
                em_dirs.sort(key=lambda x: x[0])
                INI, em_prefix = em_dirs[-1]
                objs = s3.list_objects_v2(Bucket=bucket, Prefix=em_prefix).get("Contents", [])
                pq_keys = [o["Key"] for o in objs if o["Key"].endswith(".parquet")]
                if pq_keys:
                    dfs = []
                    for idx_k, k in enumerate(pq_keys):
                        lp = work / f"oro_geos_{idx_k}.parquet"
                        s3.download_file(bucket, k, str(lp))
                        dfs.append(pd.read_parquet(lp))
                    gp = pd.concat(dfs, ignore_index=True)
                    gp["fecha"] = pd.to_datetime(gp["fecha_objetivo"])
                    MIEMBROS = sorted(gp["miembro"].dropna().astype(int).unique().tolist())
                    if len(MIEMBROS) >= 5:
                        LL = np.zeros((len(MIEMBROS), HORIZONTE), dtype=float)
                        ETO = np.zeros((len(MIEMBROS), HORIZONTE), dtype=float)
                        relleno = 0
                        for a_, mb in enumerate(MIEMBROS):
                            q = gp[gp["miembro"] == mb].drop_duplicates("fecha").set_index("fecha").reindex(FECHAS)
                            sin = q["lluvia_corr_mm"].isna() | q["eto_corr_mm"].isna()
                            relleno += int(sin.sum())
                            LL[a_] = q["lluvia_corr_mm"].fillna(pd.Series(FECHAS.day_of_year.map(CLIM_LL), index=FECHAS)).to_numpy()
                            ETO[a_] = q["eto_corr_mm"].fillna(pd.Series(FECHAS.day_of_year.map(CLIM_ETO), index=FECHAS)).to_numpy()
                        log(f"GEOS-S2S cargado desde Oro ({em_prefix}): inicio={INI.date()}, {len(MIEMBROS)} miembros, "
                            f"lluvia30d={LL.sum(1).mean():.1f}mm, ETo30d={ETO.sum(1).mean():.1f}mm, relleno={relleno}")
                        return INI, MIEMBROS, LL, ETO, "oro/geos_s2s_corregido"
        except Exception as e:
            log(f"Aviso leyendo oro/geos_s2s_corregido ({e}); usando bronce/geos_s2s_a/geos_nrt.csv")

    # Fallback / fuente directa de ML: bronce/geos_s2s_a/geos_nrt.csv + paso8_geos_correccion.csv
    geos_local = work / "geos_nrt.csv"
    s3.download_file(bucket, args["key_geos_nrt"], str(geos_local))
    corr = pd.read_csv(work / "paso8_geos_correccion.csv")
    fac = {(r.variable, int(r.mes), int(r.plazo)): r.factor for r in corr.itertuples()}
    g = pd.read_csv(geos_local, parse_dates=["fecha"])
    g["inicio"] = pd.to_datetime(g.inicio.astype(str))
    INI = g.inicio[g.inicio <= HOY].max()
    g = g[g.inicio == INI]
    gp = g.pivot_table(index=["miembro", "fecha"], columns="variable", values="valor").reset_index()
    for v_ in ("pr_sfc", "t2mmax", "t2mmin"):
        if v_ not in gp:
            gp[v_] = np.nan
    gp["plazo"] = (gp.fecha - INI).dt.days
    gp["plz"] = np.select([gp.plazo <= 10, gp.plazo <= 20], [0, 1], 2)
    gp["mes"] = gp.fecha.dt.month
    gp["pr"] = gp.pr_sfc * 86400.0 * [fac.get(("pr", m, p), 1.0) for m, p in zip(gp.mes, gp.plz)]
    tmed = (gp.t2mmax + gp.t2mmin) / 2.0 - 273.15 + np.array([fac.get(("t", m, p), 0.0) for m, p in zip(gp.mes, gp.plz)])
    doy = gp.fecha.dt.day_of_year
    gp["eto"] = doy.map(CLIM_ETO) * (tmed + 17.8) / (doy.map(CLIM_T) + 17.8)
    MIEMBROS = sorted(gp.miembro.astype(int).unique().tolist())
    LL = np.zeros((len(MIEMBROS), HORIZONTE), dtype=float)
    ETO = np.zeros((len(MIEMBROS), HORIZONTE), dtype=float)
    relleno = 0
    for a_, mb in enumerate(MIEMBROS):
        q = gp[gp.miembro == mb].set_index("fecha").reindex(FECHAS)
        sin = q.pr.isna() | q.eto.isna()
        relleno += int(sin.sum())
        LL[a_] = q.pr.fillna(pd.Series(FECHAS.day_of_year.map(CLIM_LL), index=FECHAS)).to_numpy()
        ETO[a_] = q.eto.fillna(pd.Series(FECHAS.day_of_year.map(CLIM_ETO), index=FECHAS)).to_numpy()
    log(f"GEOS-S2S corregido desde Bronce ML ({args['key_geos_nrt']}): inicio={INI.date()}, {len(MIEMBROS)} miembros, "
        f"lluvia30d={LL.sum(1).mean():.1f}mm, ETo30d={ETO.sum(1).mean():.1f}mm, relleno={relleno}")
    return INI, MIEMBROS, LL, ETO, args["key_geos_nrt"]


def register_glue_partition(glue_client, db_name: str, table_name: str, emision_val: str, s3_part_loc: str):
    """Registra/actualiza la particion emision=<hoy> en Glue Catalog para visibilidad inmediata en Athena."""
    try:
        tbl = glue_client.get_table(DatabaseName=db_name, Name=table_name)["Table"]
        sd = dict(tbl["StorageDescriptor"])
        sd["Location"] = s3_part_loc.rstrip("/") + "/"
        part_input = {"Values": [emision_val], "StorageDescriptor": sd}
        try:
            glue_client.create_partition(DatabaseName=db_name, TableName=table_name, PartitionInput=part_input)
            log(f"Particion Glue creada: {db_name}.{table_name} [emision={emision_val}]")
        except glue_client.exceptions.AlreadyExistsException:
            glue_client.update_partition(
                DatabaseName=db_name,
                TableName=table_name,
                PartitionValueList=[emision_val],
                PartitionInput=part_input,
            )
            log(f"Particion Glue actualizada: {db_name}.{table_name} [emision={emision_val}]")
    except Exception as e:
        log(f"Aviso registrando particion Glue en {db_name}.{table_name}: {e}")


def render_mapa_html(resumen: dict) -> str:
    template = """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Semáforo de humedad SIRA</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{--bg:#1b2a47;--card:#16233d;--ink:#f3f5f9;--muted:#a9b4c8;--baja:#e04b4b;--media:#f2c230;--suf:#2e9e5b;--marca:#8e44ad}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,sans-serif}
.wrap{max-width:1100px;margin:auto;padding:16px;display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:16px}
#mapa{height:520px;border-radius:12px}
.card{background:var(--card);border-radius:12px;padding:12px 16px;margin-bottom:12px;border-left:4px solid var(--c)}
.card h3{margin:0 0 4px;font-size:18px}.card p{margin:2px 0}.card small{color:var(--muted)}
.pie{grid-column:1/-1;color:var(--muted);font-size:13px}
.slider{grid-column:1/-1}.slider input{width:100%}.marcas{display:flex;justify-content:space-between;color:var(--muted)}
@media(max-width:760px){.wrap{grid-template-columns:1fr}#mapa{height:380px}}
</style></head><body><div class="wrap">
<div class="slider"><div>Horizonte de predicción: <b id="hlabel"></b></div>
<input id="h" type="range" min="0" max="3" step="1" value="0"><div class="marcas"><span>hoy</span><span>7 d</span><span>15 d</span><span>30 d</span></div></div>
<div id="mapa"></div><div id="tarjetas"></div>
<div class="pie" id="pie"></div></div>
<script>
const R = __RESUMEN__;
const map = L.map('mapa', {zoomControl:true, scrollWheelZoom:false}).fitBounds(R.limites_latlon);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {maxZoom:19, attribution:'Imágenes &copy; Esri'}).addTo(map);
let capa = null;
const f = (x, d=1) => Number(x).toLocaleString('es-CO', {maximumFractionDigits:d});
function pintar(i){
  const H = R.horizontes[i];
  document.getElementById('hlabel').textContent = H.h === 0 ? `hoy (${H.fecha})` : `${H.h} días (${H.fecha})`;
  if (capa) map.removeLayer(capa);
  capa = L.imageOverlay(`semaforo_h${String(H.h).padStart(2,'0')}.png`, R.limites_latlon, {opacity:0.85}).addTo(map);
  const b = H.baja, m = H.media, s = H.suficiente, e = H.posible_enfermedad_o_deficiencia_nutricional;
  document.getElementById('tarjetas').innerHTML = `
  <div class="card" style="--c:var(--baja)"><h3>Humedad baja · ${f(b.pct)} %</h3>
   <p>Déficit ≈ ${f(b.deficit_L_m2_medio)} L/m² (${f(b.deficit_L_m2_medio*10000,0)} L/ha): regar ya</p><small>≈ ${f(b.agua_m3_zona,0)} m³ de agua en esta zona</small></div>
  <div class="card" style="--c:var(--media)"><h3>Humedad media · ${f(m.pct)} %</h3>
   <p>Faltan ≈ ${f(m.deficit_L_m2_medio)} L/m²${m.dias_hasta_baja_mediana!=null ? `; pasa a baja en ≈ ${m.dias_hasta_baja_mediana} días sin riego` : ''}</p><small>≈ ${f(m.agua_m3_zona,0)} m³ de agua en esta zona</small></div>
  <div class="card" style="--c:var(--suf)"><h3>Humedad suficiente · ${f(s.pct)} %</h3>
   <p>${H.h === 0 ? 'Hoy el suelo tiene agua suficiente: no requiere riego' : 'No requiere riego: alcanza con la lluvia pronosticada'}</p><small>Lluvia pronosticada hasta este día: ${f(H.lluvia_mm_hasta_h,0)} mm · ETo ${f(H.eto_mm_hasta_h,0)} mm</small></div>
  ${e.pct > 0 ? `<div class="card" style="--c:var(--marca)"><h3>Posible enfermedad o deficiencia nutricional · ${f(e.pct)} %</h3>
   <p>La hoja se ve más seca de lo normal aunque el suelo tenga agua</p><small>Revisar en campo</small></div>` : ''}`;
}
document.getElementById('h').addEventListener('input', e => pintar(+e.target.value));
const DB = R.dias_hasta_baja_sin_riego_pct;
document.getElementById('tarjetas').insertAdjacentHTML('afterend', '');
const extra = document.createElement('div'); extra.className = 'pie';
extra.innerHTML = '<b>Si no se riega desde hoy, el cultivo llega a humedad baja:</b> ' + Object.entries(DB).map(([k, v]) => `${k}: ${f(v)} %`).join(' · ');
document.querySelector('.wrap').insertBefore(extra, document.getElementById('pie'));
document.getElementById('pie').textContent = `${f(R.zona_ha,0)} ha alrededor del lote de Nataima · hoy ${R.hoy} · clima GEOS-S2S (inicio ${R.geos_inicio}, ${R.miembros_geos} miembros) · riego supuesto ${R.modelo_b.riego_supuesto} · hoy: humedad corregida a Nataima (ARIMAX) con margen de ${f(R.modelo_b.margen_mm)} L/m²; desde mañana: balance FAO-56 sin riego · umbrales 30 % y 27,1 % (Martínez-Vega et al. 2025)`;
pintar(0);
setTimeout(() => { map.invalidateSize(); map.fitBounds(R.limites_latlon); }, 50);
</script></body></html>"""
    return template.replace("__RESUMEN__", json.dumps(resumen, ensure_ascii=False))


def run_pipeline(args: dict, session=None):
    sess = session or boto3.Session(region_name=args["aws_region"])
    s3 = sess.client("s3")
    glue_client = sess.client("glue")
    bucket = args["s3_bucket"]

    lat = float(args["lat"])
    lon = float(args["lon"])
    lado_km = float(args["lado_km"])
    riego_sup = args["riego_supuesto"]
    predio_id = args["predio_id"]

    with tempfile.TemporaryDirectory(prefix="sira_b05_") as tmp_root:
        work = Path(tmp_root)
        out_dir = work / "out"
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---------------------------------------------------------------- 0. Descarga desde S3
        log(f"Descargando artefactos reales ML desde s3://{bucket}/ ...")
        downloads = [
            (f"{args['prefix_cubo_a'].rstrip('/')}/cubo.npz", work / "cubo.npz"),
            (f"{args['prefix_cubo_a'].rstrip('/')}/cubo_fechas.csv", work / "cubo_fechas.csv"),
            (f"{args['prefix_cubo_a'].rstrip('/')}/clima.csv", work / "clima.csv"),
            (f"{args['prefix_modelo_a'].rstrip('/')}/predictor_ndvi.joblib", work / "predictor_ndvi.joblib"),
            (f"{args['prefix_modelo_a'].rstrip('/')}/predictor_ndmi.joblib", work / "predictor_ndmi.joblib"),
            (f"{args['prefix_modelo_a'].rstrip('/')}/meta.json", work / "meta.json"),
            (f"{args['prefix_modelo_a'].rstrip('/')}/paso1_curva_por_dias_desde_siembra.csv", work / "paso1_curva_por_dias_desde_siembra.csv"),
            (f"{args['prefix_modelo_a'].rstrip('/')}/paso2_calibracion_landsat_s2.csv", work / "paso2_calibracion_landsat_s2.csv"),
            (f"{args['prefix_modelo_a'].rstrip('/')}/paso8_geos_correccion.csv", work / "paso8_geos_correccion.csv"),
            (f"{args['prefix_modelo_a'].rstrip('/')}/paso8_prueba_clima_observado.csv", work / "paso8_prueba_clima_observado.csv"),
            (f"{args['prefix_modelo_b'].rstrip('/')}/arimax.json", work / "arimax.json"),
            (f"{args['prefix_modelo_b'].rstrip('/')}/calendarios_riego.csv", work / "calendarios_riego.csv"),
        ]
        for s3_key, dst in downloads:
            s3.download_file(bucket, s3_key, str(dst))
        log(f"Descargados {len(downloads)} artefactos reales desde S3.")

        # ---------------------------------------------------------------- 1. Cubo de la zona (400 ha)
        cl = pd.read_csv(work / "clima.csv", parse_dates=["fecha"]).set_index("fecha")
        meta = pd.read_csv(work / "cubo_fechas.csv", parse_dates=["fecha"])

        if str(args["fecha_hoy"]).strip().lower() == "auto":
            # Misma regla de actualizar_datos.py (commit 876be25): ultimo dia con lluvia observada (CHIRPS o POWER)
            fechas_ok = cl[cl["lluvia"].notna() | cl["PRECTOTCORR"].notna()].index
            HOY = fechas_ok.max()
        else:
            HOY = pd.Timestamp(args["fecha_hoy"])
        DHOY = (HOY - BASE).days
        emision_str = str(HOY.date())
        log(f"Fecha operativa hoy = {emision_str} | riego supuesto = {riego_sup}")

        z = np.load(work / "cubo.npz")
        NG, X0, Y0 = int(z["n"]), float(z["x0"]), float(z["y0"])
        xc, yc = Transformer.from_crs("EPSG:4326", "EPSG:32618", always_xy=True).transform(lon, lat)
        cc, fc = int((xc - X0) // 10), int((Y0 - yc) // 10)
        med = int(lado_km * 1000 / 20)
        f0, f1, c0, c1 = max(fc - med, 0), min(fc + med, NG), max(cc - med, 0), min(cc + med, NG)
        FIL, COL = np.mgrid[f0:f1, c0:c1]
        IDX = (FIL * NG + COL).ravel()
        NF, NC = f1 - f0, c1 - c0

        plat = meta.plataforma.str.replace(r"Sentinel-2[A-Z]", "S2", regex=True).to_numpy()
        dia = (meta.fecha - BASE).dt.days.to_numpy()
        usar = dia <= DHOY
        orden = np.lexsort((plat != "S2", dia))
        orden = orden[usar[orden]]
        DIA, PLAT = dia[orden], plat[orden]
        V = z["ndvi"][:, IDX][orden].astype(np.float32)
        W = z["ndmi"][:, IDX][orden].astype(np.float32)
        AZ = z["azul"][:, IDX][orden].astype(np.float32)
        z.close()
        log(f"Zona {NF}x{NC} pixeles 10 m ({NF * NC / 100:.0f} ha) · {len(DIA)} imagenes hasta {emision_str}")

        # Calibracion Landsat -> Sentinel-2
        cal = pd.read_csv(work / "paso2_calibracion_landsat_s2.csv")
        for r in cal.itertuples():
            M_ = V if r.indice == "ndvi" else W
            M_[PLAT == r.plataforma] = r.a + r.b * M_[PLAT == r.plataforma]

        # Filtro deterministico de bruma
        for k in range(V.shape[1]):
            ok = np.where(np.isfinite(V[:, k]))[0]
            if len(ok) < 3:
                continue
            v, d, az = V[ok, k], DIA[ok], AZ[ok, k]
            pv, nv = np.r_[np.nan, v[:-1]], np.r_[v[1:], np.nan]
            pok = np.r_[False, (d[1:] - d[:-1]) <= 30]
            nok = np.r_[(d[1:] - d[:-1]) <= 30, False]
            malo = ok[
                (pok & nok & (v < pv - 0.10) & (v < nv - 0.10))
                | (pok & nok & (az >= 0.075) & (pv > 0.5) & (nv > 0.5))
            ]
            V[malo, k] = np.nan
            W[malo, k] = np.nan
        del AZ
        COD = np.array([SENS.get(p, 1) for p in PLAT])
        s2 = PLAT == "S2"
        AGRI = (np.nanstd(V[s2], axis=0) >= 0.15) & (np.isfinite(V[s2]).sum(0) >= 50)
        log(f"Calibracion y bruma listas · agricolas {int(AGRI.sum()):,} de {len(AGRI):,}")

        # ---------------------------------------------------------------- 2. Siembra por curva NDVI
        SIEM = []
        for k in range(V.shape[1]):
            ok = np.isfinite(V[:, k])
            SIEM.append(siembras(DIA[ok], V[ok, k]))
        log(f"Siembras detectadas en {sum(len(s) > 0 for s in SIEM):,} pixeles")

        # ---------------------------------------------------------------- 3. Modelo A (RF real .joblib)
        meta_a = json.loads((work / "meta.json").read_text(encoding="utf-8"))
        MOD = {i: joblib.load(work / f"predictor_{i}.joblib") for i in ("ndvi", "ndmi")}
        for m in MOD.values():
            m.n_jobs = -1
        log("Modelos Random Forest reales (NDVI y NDMI, 200 arboles) cargados exitosamente.")

        def ejemplos(k, d1s):
            ok = np.isfinite(V[:, k]) & np.isfinite(W[:, k])
            d, v, w, c = DIA[ok], V[ok, k], W[ok, k], COD[ok]
            if len(d) == 0:
                return None
            _, u = np.unique(d, return_index=True)
            d, v, w, c = d[u], v[u], w[u], c[u]
            i0 = len(d) - 1
            d1 = np.asarray(d1s)
            n = len(d1)
            d0 = np.full(n, d[i0])
            a1 = d[i0] - d[i0 - 1] if i0 >= 1 else np.nan
            a2 = d[i0] - d[i0 - 2] if i0 >= 2 else np.nan
            a1 = np.nan if a1 > 60 else a1
            a2 = np.nan if a2 > 90 else a2
            das0 = das_de(SIEM[k], d0)
            das1 = das0 + (d1 - d0)
            das1[das1 > 150] = np.nan
            doy0 = (BASE + pd.to_timedelta(d0, unit="D")).day_of_year.to_numpy()
            doy1 = (BASE + pd.to_timedelta(d1, unit="D")).day_of_year.to_numpy()
            f = {
                "ndvi0": np.full(n, v[i0]),
                "ndmi0": np.full(n, w[i0]),
                "delta": d1 - d0,
                "sensor0": np.full(n, c[i0]),
                "sensor1": np.zeros(n),
                "das0": das0,
                "das1": das1,
                "etapa0": etapa_de(das0),
                "etapa1": etapa_de(das1),
                "doy_sin": np.sin(2.0 * np.pi * doy0 / 365.25),
                "doy_cos": np.cos(2.0 * np.pi * doy0 / 365.25),
                "doy1_sin": np.sin(2.0 * np.pi * doy1 / 365.25),
                "doy1_cos": np.cos(2.0 * np.pi * doy1 / 365.25),
                "ndvi_l1": np.full(n, v[i0 - 1] if np.isfinite(a1) else np.nan),
                "edad_l1": np.full(n, a1),
                "ndmi_l1": np.full(n, w[i0 - 1] if np.isfinite(a1) else np.nan),
                "ndvi_l2": np.full(n, v[i0 - 2] if np.isfinite(a2) else np.nan),
                "edad_l2": np.full(n, a2),
            }
            f["pend_ndvi"] = (f["ndvi0"] - f["ndvi_l1"]) / f["edad_l1"]
            f["pend_ndmi"] = (f["ndmi0"] - f["ndmi_l1"]) / f["edad_l1"]
            return pd.DataFrame(f)

        D1 = DHOY + np.arange(0, HORIZONTE + 1)
        partes, pix_ok = [], []
        for k in np.where(AGRI)[0]:
            T_df = ejemplos(k, D1)
            if T_df is None or T_df.delta.iloc[0] > 20:
                continue
            T_df["k"] = k
            partes.append(T_df)
            pix_ok.append(k)
        T_all = pd.concat(partes, ignore_index=True)
        T_all["h"] = np.tile(np.arange(HORIZONTE + 1), len(pix_ok))
        obs0 = T_all.delta == 0
        pr = {i: MOD[i].predict(T_all[meta_a["variables"][i]].to_numpy(np.float32)) for i in ("ndvi", "ndmi")}
        T_all["ndvi"] = np.where(obs0, T_all.ndvi0, pr["ndvi"])
        T_all["ndmi"] = np.where(obs0, T_all.ndmi0, pr["ndmi"])
        log(f"Modelo A pronostico {len(pix_ok):,} pixeles x {HORIZONTE + 1} dias ({len(T_all):,} filas)")

        NDVI = T_all.ndvi.to_numpy().reshape(len(pix_ok), HORIZONTE + 1)
        NDMI = T_all.ndmi.to_numpy().reshape(len(pix_ok), HORIZONTE + 1)
        DAS = T_all.das1.to_numpy().reshape(len(pix_ok), HORIZONTE + 1)
        DELTA = T_all.delta.to_numpy().reshape(len(pix_ok), HORIZONTE + 1)
        EN_CULTIVO = np.isfinite(DAS)
        del T_all, partes, pr, MOD

        # ---------------------------------------------------------------- 4. Clima GEOS-S2S corregido
        INI, MIEMBROS, LL, ETO, geos_src = load_geos_from_oro_or_bronce(s3, bucket, work, args, cl, HOY)

        # ---------------------------------------------------------------- 5. Modelo B (hasta hoy) + FAO-56 (+30 d)
        MB = ArimaxB(work / "arimax.json")
        CAL = pd.read_csv(work / "calendarios_riego.csv", index_col="das")
        CAL = CAL[[c_ for c_ in CAL if c_.split("|")[0] == riego_sup]]
        NCAL, NP = CAL.shape[1], len(pix_ok)
        EN0 = np.isfinite(DAS[:, 0])
        SOW = np.where(EN0, DHOY - np.nan_to_num(DAS[:, 0]), DHOY + 1).astype(int)
        DIAS1 = np.arange(int(SOW[EN0].min()) if EN0.any() else DHOY, DHOY + 1)
        f1_ = BASE + pd.to_timedelta(DIAS1, unit="D")

        # Relleno oficial de hueco CHIRPS con NASA POWER PRECTOTCORR (corrida 21:24 UTC)
        ll_chirps = cl["lluvia"].reindex(f1_)
        dias_sin_chirps = int(ll_chirps.isna().sum())
        if "PRECTOTCORR" in cl.columns:
            L1 = ll_chirps.fillna(cl["PRECTOTCORR"].reindex(f1_)).fillna(0.0).to_numpy(dtype=float)
            lluvia_hueco = {"dias_sin_chirps": dias_sin_chirps, "relleno": "power" if dias_sin_chirps > 0 else "ninguno"}
        else:
            L1 = ll_chirps.fillna(0.0).to_numpy(dtype=float)
            lluvia_hueco = {"dias_sin_chirps": dias_sin_chirps, "relleno": "cero"}
        E1 = cl["eto"].reindex(f1_).ffill().bfill().to_numpy(dtype=float)

        NV1 = np.empty((len(DIAS1), NP), dtype=float)
        for i, k in enumerate(pix_ok):
            ok = np.isfinite(V[:, k])
            NV1[:, i] = np.interp(DIAS1, DIA[ok], V[ok, k])
        NV1[-1] = NDVI[:, 0]

        ACT1 = (DIAS1[:, None] >= SOW[None, :]) & EN0[None, :]
        DAS1 = DIAS1[:, None] - SOW[None, :] + 1
        R1 = np.zeros((len(DIAS1), NCAL, NP), dtype=float)
        for c_ in range(NCAL):
            tabla_r = np.r_[CAL.iloc[:, c_].to_numpy(dtype=float), 0.0]
            R1[:, c_] = np.where(ACT1, tabla_r[np.clip(DAS1, 1, len(CAL) + 1) - 1], 0.0)

        fo1 = (len(DIAS1), NCAL, NP)
        X1 = propagar(
            np.broadcast_to(L1[:, None, None], fo1),
            np.broadcast_to(E1[:, None, None], fo1),
            R1,
            np.broadcast_to(NV1[:, None, :], fo1),
            np.broadcast_to(ACT1[:, None, :], fo1),
        )

        # B solo hasta hoy + margen una sola vez al arranque
        MU0 = MB.mu({c_: X1[c_][-1] for c_ in ENTRADAS_B}).mean(0)
        DR0_ESP = MM_POR_PUNTO * (CC - MU0)
        DR0 = DR0_ESP + MM_POR_PUNTO * MB.sigma

        # Tramo 2: hoy -> +30 con Dr_modificada FAO-56, sin riego, sin margen adicional
        fo2 = (HORIZONTE, len(MIEMBROS), NP)
        DR2 = propagar_dr(
            DR0[None, :],
            np.broadcast_to(LL.T[:, :, None], fo2),
            np.broadcast_to(ETO.T[:, :, None], fo2),
            np.broadcast_to(NDVI[:, 1:].T[:, None, :], fo2),
        )
        DRP = np.c_[np.clip(DR0, -DSAT, TAW), DR2.mean(1).T]
        DRB = np.c_[DR0_ESP, np.full((NP, HORIZONTE), np.nan)]
        MU = CC - DRP / MM_POR_PUNTO
        S20 = np.c_[X1["S_tau20"][-1].mean(0), np.full((NP, HORIZONTE), np.nan)]
        DR_EC = np.c_[X1["Dr_k0.5"][-1].mean(0), np.full((NP, HORIZONTE), np.nan)]
        CLASE = np.select([DRP > DR_ROJO, DRP > 0.0], [2, 1], 0)

        # Alarma tardia NDMI (solo sobre pixeles verdes, no cambia media -> baja)
        cur = pd.read_csv(work / "paso1_curva_por_dias_desde_siembra.csv")
        mu_c = np.interp(DAS, cur.das_b + 5.0, cur.ndmi0_mean)
        sd_c = np.interp(DAS, cur.das_b + 5.0, cur.ndmi0_std)
        pa_err = pd.read_csv(work / "paso8_prueba_clima_observado.csv")
        pa_err = pa_err[(pa_err.indice == "ndmi") & pa_err.grupo.str.match(r"h\d+$")]
        H_ERR = pa_err.grupo.str[1:].astype(int).to_numpy()
        RMSE_H = np.interp(DELTA, H_ERR, pa_err.rmse.to_numpy())
        RMSE_H = np.where(DELTA == 0, 0.0, RMSE_H)
        ANOM = ((NDMI + RMSE_H - mu_c) / sd_c) < Z_SECO
        MARCA = (CLASE == 0) & ANOM
        CLASE = np.where(EN_CULTIVO, CLASE, -1)
        MARCA &= EN_CULTIVO
        DIAS_BAJA = np.where((CLASE == 2).any(1), (CLASE == 2).argmax(1), -1)

        log(f"Modelo B + FAO-56 listos | lluvia_hueco={lluvia_hueco} | "
            f"mu0 mediana={np.nanmedian(MU0[EN0]):.1f}% | sigma={MB.sigma:.2f}% (margen {1.5*MB.sigma:.2f} mm)")

        # ---------------------------------------------------------------- 6. Construccion de salidas
        ll_de = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
        xa, yb = X0 + 10 * c0, Y0 - 10 * f1
        xb, ya = X0 + 10 * c1, Y0 - 10 * f0
        (lon_a, lat_b), (lon_b, lat_a) = ll_de.transform(xa, yb), ll_de.transform(xb, ya)
        pk = np.array(pix_ok)

        resumen = {
            "hoy": emision_str,
            "geos_inicio": str(INI.date()),
            "miembros_geos": len(MIEMBROS),
            "zona_ha": float(NF * NC / 100.0),
            "limites_latlon": [[lat_b, lon_a], [lat_a, lon_b]],
            "modelo_b": {
                "sigma_pct": round(MB.sigma, 2),
                "margen_mm": round(MM_POR_PUNTO * MB.sigma, 2),
                "riego_supuesto": f"{riego_sup} de Nataima hasta hoy (promedio de GS1, GS2 y GS3); sin riego desde hoy",
            },
            "horizontes": [],
        }
        enc = CLASE[:, 0] >= 0
        db = DIAS_BAJA[enc]
        resumen["dias_hasta_baja_sin_riego_pct"] = {
            "ya hoy": round(100.0 * float(np.mean(db == 0)), 1),
            "1-2 días": round(100.0 * float(np.mean((db >= 1) & (db <= 2))), 1),
            "3-5 días": round(100.0 * float(np.mean((db >= 3) & (db <= 5))), 1),
            "6-30 días": round(100.0 * float(np.mean(db >= 6)), 1),
            "no llega en 30 días": round(100.0 * float(np.mean(db < 0)), 1),
        }
        resumen["lluvia_hueco"] = lluvia_hueco
        resumen["diario"] = [
            {
                "h": int(h),
                "fecha": str((HOY + pd.Timedelta(days=h)).date()),
                "lluvia_mm_hasta_h": round(float(LL[:, :h].sum(1).mean()), 1) if h else 0.0,
                "eto_mm_hasta_h": round(float(ETO[:, :h].sum(1).mean()), 1) if h else 0.0,
            }
            for h in range(HORIZONTE + 1)
        ]

        res_parquet_rows = []
        for h in (0, 7, 15, 30):
            img = np.zeros((NF * NC, 4), np.uint8)
            for c_ in (0, 1, 2):
                img[pk[CLASE[:, h] == c_]] = COLORES[c_]
            img[pk[MARCA[:, h]]] = COLORES["marca"]
            Image.fromarray(img.reshape(NF, NC, 4), "RGBA").save(out_dir / f"semaforo_h{h:02d}.png")

            cult = CLASE[:, h] >= 0
            n_c = int(cult.sum())
            ll_h = float(LL[:, :h].sum(1).mean()) if h else 0.0
            et_h = float(ETO[:, :h].sum(1).mean()) if h else 0.0
            fila = {
                "h": h,
                "fecha": str((HOY + pd.Timedelta(days=h)).date()),
                "ha_en_cultivo": n_c / 100.0,
                "lluvia_mm_hasta_h": ll_h,
                "eto_mm_hasta_h": et_h,
            }
            for c_, nom in CLASES.items():
                sel = CLASE[:, h] == c_
                dr_ = np.clip(DRP[sel, h], 0.0, None)
                fila[nom] = {
                    "pct": round(100.0 * sel.sum() / max(n_c, 1), 1),
                    "ha": sel.sum() / 100.0,
                    "deficit_L_m2_medio": round(float(dr_.mean()), 2) if sel.any() else 0.0,
                    "agua_m3_zona": round(float(dr_.sum() * 0.1), 0),
                }
            med_mask = (CLASE[:, h] == 1) & (DIAS_BAJA >= h)
            fila["media"]["dias_hasta_baja_mediana"] = (
                int(np.median(DIAS_BAJA[med_mask] - h)) if med_mask.any() else None
            )
            fila["posible_enfermedad_o_deficiencia_nutricional"] = {
                "pct": round(100.0 * MARCA[:, h].sum() / max(n_c, 1), 1)
            }
            resumen["horizontes"].append(fila)

            res_parquet_rows.append(
                {
                    "predio_id": predio_id,
                    "hoy": emision_str,
                    "horizonte": int(h),
                    "fecha": fila["fecha"],
                    "zona_ha": float(resumen["zona_ha"]),
                    "ha_en_cultivo": float(fila["ha_en_cultivo"]),
                    "lluvia_mm_hasta_h": round(ll_h, 2),
                    "eto_mm_hasta_h": round(et_h, 2),
                    "pct_suficiente": float(fila["suficiente"]["pct"]),
                    "ha_suficiente": float(fila["suficiente"]["ha"]),
                    "pct_media": float(fila["media"]["pct"]),
                    "ha_media": float(fila["media"]["ha"]),
                    "deficit_media_L_m2": float(fila["media"]["deficit_L_m2_medio"]),
                    "agua_media_m3_zona": float(fila["media"]["agua_m3_zona"]),
                    "dias_hasta_baja_mediana": (
                        float(fila["media"]["dias_hasta_baja_mediana"])
                        if fila["media"]["dias_hasta_baja_mediana"] is not None
                        else np.nan
                    ),
                    "pct_baja": float(fila["baja"]["pct"]),
                    "ha_baja": float(fila["baja"]["ha"]),
                    "deficit_baja_L_m2": float(fila["baja"]["deficit_L_m2_medio"]),
                    "agua_baja_m3_zona": float(fila["baja"]["agua_m3_zona"]),
                    "pct_marca_fitosanitaria": float(fila["posible_enfermedad_o_deficiencia_nutricional"]["pct"]),
                    "modelo_b_sigma_pct": float(resumen["modelo_b"]["sigma_pct"]),
                    "modelo_b_margen_mm": float(resumen["modelo_b"]["margen_mm"]),
                    "riego_supuesto": str(resumen["modelo_b"]["riego_supuesto"]),
                    "lluvia_hueco_dias_sin_chirps": int(lluvia_hueco["dias_sin_chirps"]),
                    "lluvia_hueco_relleno": str(lluvia_hueco["relleno"]),
                }
            )

        (out_dir / "resumen.json").write_text(json.dumps(resumen, indent=1, ensure_ascii=False), encoding="utf-8")
        (out_dir / "mapa.html").write_text(render_mapa_html(resumen), encoding="utf-8")

        # Tabla completa de 20.286 pixeles agricolas (39 cols ML + predio_id para Athena)
        filas = pd.DataFrame(
            {
                "pixel": IDX[pk].astype(np.int64),
                "das_hoy": DAS[:, 0].astype(np.float64),
                "dias_hasta_baja": DIAS_BAJA.astype(np.int32),
            }
        )
        for h in (0, 7, 15, 30):
            filas[f"clase_h{h}"] = CLASE[:, h].astype(np.int32)
            filas[f"humedad_pct_h{h}"] = np.round(MU[:, h], 2).astype(np.float64)
            filas[f"deficit_B_sin_margen_mm_h{h}"] = np.round(DRB[:, h], 2).astype(np.float64)
            filas[f"deficit_mm_h{h}"] = np.round(DRP[:, h], 2).astype(np.float64)
            filas[f"S_tau20_h{h}"] = np.round(S20[:, h], 2).astype(np.float64)
            filas[f"dr_ecuacion_mm_h{h}"] = np.round(DR_EC[:, h], 2).astype(np.float64)
            filas[f"ndvi_h{h}"] = np.round(NDVI[:, h], 3).astype(np.float64)
            filas[f"ndmi_h{h}"] = np.round(NDMI[:, h], 3).astype(np.float64)
            filas[f"marca_h{h}"] = MARCA[:, h].astype(bool)

        filas.to_csv(out_dir / "pixeles.csv.gz", index=False, compression="gzip")

        # Parquet tipado explicito para Athena (oro_semaforo_c_pixeles y oro_semaforo_c_resumen)
        df_pq_pix = filas.copy()
        df_pq_pix.insert(0, "predio_id", predio_id)
        pix_fields = [
            pa.field("predio_id", pa.string()),
            pa.field("pixel", pa.int64()),
            pa.field("das_hoy", pa.float64()),
            pa.field("dias_hasta_baja", pa.int32()),
        ]
        for h in (0, 7, 15, 30):
            pix_fields.extend(
                [
                    pa.field(f"clase_h{h}", pa.int32()),
                    pa.field(f"humedad_pct_h{h}", pa.float64()),
                    pa.field(f"deficit_B_sin_margen_mm_h{h}", pa.float64()),
                    pa.field(f"deficit_mm_h{h}", pa.float64()),
                    pa.field(f"S_tau20_h{h}", pa.float64()),
                    pa.field(f"dr_ecuacion_mm_h{h}", pa.float64()),
                    pa.field(f"ndvi_h{h}", pa.float64()),
                    pa.field(f"ndmi_h{h}", pa.float64()),
                    pa.field(f"marca_h{h}", pa.bool_()),
                ]
            )
        pix_schema = pa.schema(pix_fields)
        pq_pix_path = out_dir / "semaforo_c_pixeles.parquet"
        pq.write_table(pa.Table.from_pandas(df_pq_pix, schema=pix_schema, preserve_index=False), pq_pix_path, compression="snappy")

        df_pq_res = pd.DataFrame(res_parquet_rows)
        res_schema = pa.schema(
            [
                pa.field("predio_id", pa.string()),
                pa.field("hoy", pa.string()),
                pa.field("horizonte", pa.int32()),
                pa.field("fecha", pa.string()),
                pa.field("zona_ha", pa.float64()),
                pa.field("ha_en_cultivo", pa.float64()),
                pa.field("lluvia_mm_hasta_h", pa.float64()),
                pa.field("eto_mm_hasta_h", pa.float64()),
                pa.field("pct_suficiente", pa.float64()),
                pa.field("ha_suficiente", pa.float64()),
                pa.field("pct_media", pa.float64()),
                pa.field("ha_media", pa.float64()),
                pa.field("deficit_media_L_m2", pa.float64()),
                pa.field("agua_media_m3_zona", pa.float64()),
                pa.field("dias_hasta_baja_mediana", pa.float64()),
                pa.field("pct_baja", pa.float64()),
                pa.field("ha_baja", pa.float64()),
                pa.field("deficit_baja_L_m2", pa.float64()),
                pa.field("agua_baja_m3_zona", pa.float64()),
                pa.field("pct_marca_fitosanitaria", pa.float64()),
                pa.field("modelo_b_sigma_pct", pa.float64()),
                pa.field("modelo_b_margen_mm", pa.float64()),
                pa.field("riego_supuesto", pa.string()),
                pa.field("lluvia_hueco_dias_sin_chirps", pa.int32()),
                pa.field("lluvia_hueco_relleno", pa.string()),
            ]
        )
        pq_res_path = out_dir / "semaforo_c_resumen.parquet"
        pq.write_table(pa.Table.from_pandas(df_pq_res, schema=res_schema, preserve_index=False), pq_res_path, compression="snappy")

        # ---------------------------------------------------------------- 7. semaforo.json (formato 2, 31 dias)
        # Misma seleccion que exportar_app.py: solo pixeles en cultivo en al menos un horizonte
        en_app = (CLASE >= 0).any(axis=1)
        idx_app = np.where(en_app)[0]
        pix_ids_app = IDX[pk[idx_app]]
        fila_u, col_u = np.divmod(pix_ids_app, NG)
        xu, yu = X0 + 10.0 * col_u + 5.0, Y0 - 10.0 * fila_u - 5.0
        lon_u, lat_u = ll_de.transform(xu, yu)

        def rlist(arr, dec):
            return [None if not np.isfinite(a) else round(float(a), dec) for a in arr]

        cls_char = {-1: "-", 0: "0", 1: "1", 2: "2"}
        dias_json = []
        for h in range(HORIZONTE + 1):
            ch = CLASE[idx_app, h]
            drh = DRP[idx_app, h]
            mkh = MARCA[idx_app, h]
            dias_json.append(
                {
                    "h": int(h),
                    "fecha": str((HOY + pd.Timedelta(days=h)).date()),
                    "lluvia_mm": round(float(LL[:, :h].sum(1).mean()), 1) if h else 0.0,
                    "eto_mm": round(float(ETO[:, :h].sum(1).mean()), 1) if h else 0.0,
                    "clase": "".join(cls_char[int(c)] for c in ch),
                    "deficit_mm": [None if int(c) < 0 else round(float(v), 2) for c, v in zip(ch, drh)],
                    "marca": [int(i) for i in np.where(mkh)[0]],
                }
            )

        sem_app = {
            "formato": 2,
            "hoy": emision_str,
            "geos_inicio": str(INI.date()),
            "miembros_geos": len(MIEMBROS),
            "modelo_b": resumen["modelo_b"],
            "lluvia_hueco": lluvia_hueco,
            "limites_latlon": resumen["limites_latlon"],
            "celda_m": 10,
            "umbrales": {
                "rojo_dr_mm": DR_ROJO,
                "verde_dr_mm": 0.0,
                "rojo_humedad_pct": 27.1,
                "verde_humedad_pct": CC,
                "mm_por_punto": MM_POR_PUNTO,
            },
            "pixeles": {
                "n": int(len(idx_app)),
                "lat": rlist(lat_u, 6),
                "lon": rlist(lon_u, 6),
                "das_hoy": rlist(DAS[idx_app, 0], 0),
                "dias_hasta_rojo": [int(v) for v in DIAS_BAJA[idx_app]],
            },
            "dias": dias_json,
        }
        (out_dir / "semaforo.json").write_text(
            json.dumps(sem_app, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )

        # Insumo compacto para AWS Lambda POST /regar_hoy (fisica exacta FAO-56 sin cargar sklearn ni cubo.npz)
        sim_app = {
            "version": 1,
            "hoy": emision_str,
            "geos_inicio": str(INI.date()),
            "geos_fuente": geos_src,
            "miembros": [int(m) for m in MIEMBROS],
            "parametros_fao56": {
                "TAW": TAW,
                "P_AG": P_AG,
                "RAW": RAW,
                "DSAT": DSAT,
                "K_DREN": K_DREN,
                "CC": CC,
                "DR_ROJO": DR_ROJO,
                "MM_POR_PUNTO": MM_POR_PUNTO,
            },
            "fechas_h1_30": [str((HOY + pd.Timedelta(days=h)).date()) for h in range(1, HORIZONTE + 1)],
            "geos_lluvia_5x30": [[round(float(x), 3) for x in row] for row in LL],
            "geos_eto_5x30": [[round(float(x), 3) for x in row] for row in ETO],
            "n": int(len(idx_app)),
            "pixel_id": [int(v) for v in pix_ids_app],
            "lat": rlist(lat_u, 6),
            "lon": rlist(lon_u, 6),
            "das_hoy": rlist(DAS[idx_app, 0], 0),
            "dr0_con_margen_mm": [round(float(x), 2) for x in DRP[idx_app, 0]],
            "dr0_sin_margen_mm": [round(float(x), 2) for x in DR0_ESP[idx_app]],
            "ndvi_h1_30": [[round(float(x), 3) for x in NDVI[i, 1:]] for i in idx_app],
        }
        (out_dir / "simulador_riego.json").write_text(
            json.dumps(sim_app, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )

        # ---------------------------------------------------------------- 8. Publicacion atomica en S3
        manifiesto = []
        # 8.1 Parquet Oro para Athena (purga solo la particion de esta emision para 0 duplicados)
        pix_part_prefix = f"{args['prefix_oro_pixeles'].rstrip('/')}/emision={emision_str}"
        res_part_prefix = f"{args['prefix_oro_resumen'].rstrip('/')}/emision={emision_str}"
        clear_s3_prefix(s3, bucket, pix_part_prefix)
        clear_s3_prefix(s3, bucket, res_part_prefix)
        upload_with_sha256(s3, bucket, f"{pix_part_prefix}/part-00000.parquet", pq_pix_path, manifiesto)
        upload_with_sha256(s3, bucket, f"{res_part_prefix}/part-00000.parquet", pq_res_path, manifiesto)

        # 8.2 Artefactos App Lovable en oro/semaforo_app/emision=<hoy>/
        app_base = args["prefix_oro_app"].rstrip("/")
        app_em_base = f"{app_base}/emision={emision_str}"
        for fname in (
            "semaforo.json",
            "simulador_riego.json",
            "resumen.json",
            "pixeles.csv.gz",
            "mapa.html",
            "semaforo_h00.png",
            "semaforo_h07.png",
            "semaforo_h15.png",
            "semaforo_h30.png",
        ):
            upload_with_sha256(s3, bucket, f"{app_em_base}/{fname}", out_dir / fname, manifiesto)

        # 8.3 Espejo de mapas actualizados en oro/semaforo_c_mapas/ (elimina mapas viejos 1a592aa)
        mapas_base = args["prefix_oro_mapas"].rstrip("/")
        for fname in ("resumen.json", "mapa.html", "semaforo_h00.png", "semaforo_h07.png", "semaforo_h15.png", "semaforo_h30.png"):
            upload_with_sha256(s3, bucket, f"{mapas_base}/{fname}", out_dir / fname, manifiesto)

        # 8.4 Puntero atomico latest.json (con guardia monotonica: nunca retrocede a una emision mas vieja)
        now_utc = datetime.now(timezone.utc)
        prev_emision = None
        try:
            prev_latest = json.loads(s3.get_object(Bucket=bucket, Key=f"{app_base}/latest.json")["Body"].read().decode("utf-8"))
            prev_emision = str(prev_latest.get("emision") or "")
        except Exception:
            prev_emision = None

        puntero = {
            "emision": emision_str,
            "semaforo": f"emision={emision_str}/semaforo.json",
            "simulador": f"emision={emision_str}/simulador_riego.json",
            "resumen": f"emision={emision_str}/resumen.json",
            "zonas": "zonas.json",
            "formato": 2,
            "generado_por": f"aws-glue:{args['JOB_NAME']}",
            "subido_utc": now_utc.isoformat(timespec="seconds"),
        }
        if not prev_emision or emision_str >= prev_emision:
            s3.put_object(
                Bucket=bucket,
                Key=f"{app_base}/latest.json",
                Body=json.dumps(puntero, ensure_ascii=False).encode("utf-8"),
                ContentType="application/json",
            )
            log(f"latest.json actualizado -> emision={emision_str} (prev={prev_emision})")
        else:
            log(f"Guardia monotonica: se conserva latest.json en emision={prev_emision} (corrida actual={emision_str})")
        man_key = f"{app_base}/manifiestos/corrida_glue_{now_utc:%Y%m%dT%H%M%SZ}.json"
        s3.put_object(
            Bucket=bucket,
            Key=man_key,
            Body=json.dumps(
                {
                    "bucket": bucket,
                    "emision": emision_str,
                    "generado_por": f"aws-glue:{args['JOB_NAME']}",
                    "geos_fuente": geos_src,
                    "pixeles_agricolas": int(len(pix_ok)),
                    "pixeles_en_cultivo_app": int(len(idx_app)),
                    "objetos": manifiesto,
                },
                ensure_ascii=False,
                indent=1,
            ).encode("utf-8"),
            ContentType="application/json",
        )

        # 8.5 Registrar particiones inmediatamente en AWS Glue Data Catalog (para Athena)
        register_glue_partition(glue_client, args["athena_db"], "oro_semaforo_c_pixeles", emision_str, f"s3://{bucket}/{pix_part_prefix}/")
        register_glue_partition(glue_client, args["athena_db"], "oro_semaforo_c_resumen", emision_str, f"s3://{bucket}/{res_part_prefix}/")

        res_str = " | ".join(
            f"h{f['h']}: suf {f['suficiente']['pct']}% med {f['media']['pct']}% baja {f['baja']['pct']}% marca {f['posible_enfermedad_o_deficiencia_nutricional']['pct']}%"
            for f in resumen["horizontes"]
        )
        log(f"COMPLETADO ({len(manifiesto)} objetos en S3, {man_key}) -> {res_str}")


def main():
    args = parse_args(sys.argv)
    # Inicializacion opcional de Glue Job cuando se ejecuta dentro de AWS Glue ETL
    glue_job = None
    try:
        from awsglue.context import GlueContext
        from awsglue.job import Job
        from pyspark.context import SparkContext

        sc = SparkContext.getOrCreate()
        glue_ctx = GlueContext(sc)
        glue_job = Job(glue_ctx)
        glue_job.init(args["JOB_NAME"], args)
    except Exception:
        pass

    run_pipeline(args)

    if glue_job is not None:
        glue_job.commit()


if __name__ == "__main__":
    main()
