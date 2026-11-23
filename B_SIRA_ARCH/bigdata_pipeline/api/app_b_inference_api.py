# -*- coding: utf-8 -*-
"""
SIRA (Iteración B Unificada) · Microservicio AWS Lambda + Function URL (`sira-b-api-serving`)
=============================================================================================
Puente ligero (solo biblioteca estándar + boto3, 0 capas externas) entre la Capa Oro de S3
(`s3://sira-b-lakehouse-929570730700/oro/semaforo_app/`) y el frontend de la App Lovable.

Rutas HTTP expuestas (Function URL con CORS habilitado):
  - `GET /health` o `GET /`  : estado del servicio, última emisión activa y catálogo de rutas.
  - `GET /latest`            : puntero `oro/semaforo_app/latest.json`.
  - `GET /zonas`             : `oro/semaforo_app/zonas.json` (Tolima + 47 municipios + zona piloto).
  - `GET /resumen`           : `oro/semaforo_app/emision=<emision>/resumen.json` (métricas 400 ha).
  - `GET /semaforo`          : `oro/semaforo_app/emision=<emision>/semaforo.json` (formato 2, 31 días,
                               6.868 píxeles en cultivo con lat/lon; soporta gzip automático).
  - `GET /semaforo_c`        : consulta puntual de un píxel (`?pixel_idx=0` o `?lat=4.1895&lon=-74.9621`)
                               junto con resumen zonal de urgencia de riego.
  - `POST /regar_hoy`        : simulador interactivo <50 ms con física exacta `Dr_modificada` FAO-56
                               (`TAW=6.7, p=0.65, DSAT=26.475, k=0.5`, sin margen adicional) sobre los
                               5 miembros GEOS-S2S y el NDVI real del Modelo A (`simulador_riego.json`).
                               Body: `{"lamina_riego_mm": 30.0, "dia_aplicacion": 0, "lat": 4.1895, "lon": -74.9621}`
                               (o `"pixel_idx": 0` / `"pixel_id": ...`).
"""

import base64
import gzip
import json
import math
import os
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

S3_BUCKET = os.environ.get("SIRA_S3_BUCKET", "sira-b-lakehouse-929570730700")
S3_APP_PREFIX = os.environ.get("SIRA_APP_PREFIX", "oro/semaforo_app").rstrip("/")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
CACHE_TTL_SEC = int(os.environ.get("SIRA_CACHE_TTL_SEC", "60"))

CLASE_LBL = {"0": "suficiente", "1": "media", "2": "baja", "-": "fuera_de_cultivo"}
CLASE_INT_LBL = {0: "suficiente", 1: "media", 2: "baja", -1: "fuera_de_cultivo"}

_S3_CLIENT = None
_CACHE = {
    "latest_ts": 0.0,
    "latest": None,
    "bytes": {},     # s3_key -> raw bytes
    "json": {},      # s3_key -> parsed dict
}


def _get_s3():
    global _S3_CLIENT
    if _S3_CLIENT is None:
        _S3_CLIENT = boto3.client("s3", region_name=AWS_REGION)
    return _S3_CLIENT


def _read_s3_bytes(key: str, force: bool = False) -> bytes:
    if not force and key in _CACHE["bytes"]:
        return _CACHE["bytes"][key]
    data = _get_s3().get_object(Bucket=S3_BUCKET, Key=key)["Body"].read()
    _CACHE["bytes"][key] = data
    return data


def _read_s3_json(key: str, force: bool = False) -> dict:
    if not force and key in _CACHE["json"]:
        return _CACHE["json"][key]
    obj = json.loads(_read_s3_bytes(key, force=force).decode("utf-8"))
    _CACHE["json"][key] = obj
    return obj


def _get_latest() -> dict:
    now = time.time()
    if _CACHE["latest"] is not None and (now - _CACHE["latest_ts"]) < CACHE_TTL_SEC:
        return _CACHE["latest"]
    prev = _CACHE["latest"]
    latest = _read_s3_json(f"{S3_APP_PREFIX}/latest.json", force=True)
    # Si cambio la emision o el timestamp de subida, invalida cache de artefactos previos
    if prev is not None and (
        prev.get("emision") != latest.get("emision") or prev.get("subido_utc") != latest.get("subido_utc")
    ):
        _CACHE["bytes"].clear()
        _CACHE["json"].clear()
        _CACHE["json"][f"{S3_APP_PREFIX}/latest.json"] = latest
    _CACHE["latest"] = latest
    _CACHE["latest_ts"] = now
    return latest


def _resolve_emision(params: dict) -> str:
    em = (params or {}).get("emision")
    if em:
        return str(em).strip()
    return str(_get_latest()["emision"])


def _find_pixel_index(sim: dict, query: dict) -> int:
    n = int(sim["n"])
    if query.get("pixel_idx") is not None:
        idx = int(query["pixel_idx"])
        return max(0, min(n - 1, idx))
    if query.get("pixel_id") is not None or query.get("pixel") is not None:
        pid = int(query.get("pixel_id") if query.get("pixel_id") is not None else query.get("pixel"))
        pids = sim.get("pixel_id", [])
        try:
            return pids.index(pid)
        except ValueError:
            pass
    if query.get("lat") is not None and query.get("lon") is not None:
        q_lat, q_lon = float(query["lat"]), float(query["lon"])
        lats, lons = sim["lat"], sim["lon"]
        best_i, best_d = 0, float("inf")
        for i in range(n):
            d = (lats[i] - q_lat) ** 2 + (lons[i] - q_lon) ** 2
            if d < best_d:
                best_d, best_i = d, i
        return best_i
    return 0


def _propagar_dr_5m(
    dr0: float,
    ll_5x30: list,
    eto_5x30: list,
    ndvi_30: list,
    params_fao: dict,
    lamina_riego_mm: float = 0.0,
    dia_aplicacion: int = 0,
) -> list:
    """Propagacion exacta de `b_entradas.propagar_dr` sobre 5 miembros GEOS-S2S sin margen adicional."""
    TAW = float(params_fao["TAW"])
    P_AG = float(params_fao["P_AG"])
    RAW = float(params_fao["RAW"])
    DSAT = float(params_fao["DSAT"])
    K_DREN = float(params_fao["K_DREN"])

    d0 = float(dr0)
    if lamina_riego_mm > 0.0 and dia_aplicacion == 0:
        d0 -= lamina_riego_mm
        if d0 < 0.0:
            d0 *= (1.0 - K_DREN)
    d0 = min(TAW, max(-DSAT, d0))

    n_m = len(ll_5x30)
    H = len(ndvi_30)
    acum = [0.0] * H
    for m in range(n_m):
        dr = d0
        ll_m = ll_5x30[m]
        et_m = eto_5x30[m]
        for t in range(H):
            rg = lamina_riego_mm if (lamina_riego_mm > 0.0 and dia_aplicacion == (t + 1)) else 0.0
            kc = min(1.3, max(0.6, 1.0 * ndvi_30[t] + 0.45))
            d = dr - (ll_m[t] + rg)
            ks = 1.0 if d <= RAW else max(0.0, (TAW - d) / ((1.0 - P_AG) * TAW))
            d = d + ks * kc * et_m[t]
            if d < 0.0:
                d *= (1.0 - K_DREN)
            dr = min(TAW, max(-DSAT, d))
            acum[t] += dr
    return [d0] + [a / n_m for a in acum]


def _resumir_trayectoria(dr_series: list, fechas_h0_30: list, das_hoy: float, params_fao: dict) -> dict:
    DR_ROJO = float(params_fao["DR_ROJO"])
    CC = float(params_fao["CC"])
    MM_P = float(params_fao["MM_POR_PUNTO"])

    diario = []
    dias_hasta_rojo = -1
    dias_hasta_amarillo = -1
    for h, dr in enumerate(dr_series):
        en_cultivo = (das_hoy is None) or ((das_hoy + h) <= 150.0)
        if not en_cultivo:
            cls_i = -1
        elif dr > DR_ROJO:
            cls_i = 2
        elif dr > 0.0:
            cls_i = 1
        else:
            cls_i = 0
        if cls_i == 2 and dias_hasta_rojo == -1:
            dias_hasta_rojo = h
        if cls_i in (1, 2) and dias_hasta_amarillo == -1:
            dias_hasta_amarillo = h
        diario.append(
            {
                "h": h,
                "fecha": fechas_h0_30[h],
                "deficit_mm": round(dr, 2),
                "humedad_pct": round(CC - dr / MM_P, 2),
                "clase_id": cls_i,
                "clase": CLASE_INT_LBL[cls_i],
            }
        )
    horizontes = {f"h{h}": diario[h] for h in (0, 7, 15, 30) if h < len(diario)}
    return {
        "dias_hasta_amarillo": dias_hasta_amarillo,
        "dias_hasta_rojo": dias_hasta_rojo,
        "horizontes": horizontes,
        "primeros_7_dias": diario[:8],
        "diario": diario,
    }


def _load_simulador(emision: str) -> tuple:
    """Lee simulador_riego.json de la emision solicitada; si una subida manual aun no lo genero, cae al mas reciente."""
    key = f"{S3_APP_PREFIX}/emision={emision}/simulador_riego.json"
    try:
        return _read_s3_json(key), emision
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
            raise
        resp = _get_s3().list_objects_v2(Bucket=S3_BUCKET, Prefix=f"{S3_APP_PREFIX}/emision=")
        candidates = sorted(
            [o["Key"] for o in resp.get("Contents", []) if o["Key"].endswith("/simulador_riego.json")],
            reverse=True,
        )
        if not candidates:
            raise
        fb_key = candidates[0]
        fb_em = fb_key.split("emision=", 1)[1].split("/", 1)[0]
        return _read_s3_json(fb_key), fb_em


def simular_regar_hoy(payload: dict) -> dict:
    """Simulador interactivo `POST /regar_hoy` con física exacta FAO-56 sobre 5 miembros GEOS-S2S (<50 ms)."""
    t0 = time.perf_counter()
    emision = _resolve_emision(payload)
    sim, sim_emision = _load_simulador(emision)
    idx = _find_pixel_index(sim, payload)

    lamina_mm = max(0.0, float(payload.get("lamina_riego_mm", 30.0)))
    dia_ap = max(0, min(30, int(payload.get("dia_aplicacion", 0))))
    params_fao = sim["parametros_fao56"]

    dr0 = float(sim["dr0_con_margen_mm"][idx])
    das_hoy = sim["das_hoy"][idx]
    ndvi_30 = sim["ndvi_h1_30"][idx]
    fechas = [sim["hoy"]] + sim["fechas_h1_30"]

    dr_sin = _propagar_dr_5m(dr0, sim["geos_lluvia_5x30"], sim["geos_eto_5x30"], ndvi_30, params_fao, 0.0, 0)
    dr_con = _propagar_dr_5m(dr0, sim["geos_lluvia_5x30"], sim["geos_eto_5x30"], ndvi_30, params_fao, lamina_mm, dia_ap)

    res_sin = _resumir_trayectoria(dr_sin, fechas, das_hoy, params_fao)
    res_con = _resumir_trayectoria(dr_con, fechas, das_hoy, params_fao)

    d_sin, d_con = res_sin["dias_hasta_rojo"], res_con["dias_hasta_rojo"]
    if d_sin >= 0 and d_con == -1:
        ganados = f">={30 - d_sin} (no llega a rojo en 30 días)"
    elif d_sin >= 0 and d_con >= 0:
        ganados = d_con - d_sin
    else:
        ganados = 0

    return {
        "modo": "SIMULADOR_REGAR_HOY_FAO56_UNIFICADO",
        "emision": emision,
        "simulador_emision": sim_emision,
        "hoy": sim["hoy"],
        "geos_inicio": sim["geos_inicio"],
        "geos_fuente": sim.get("geos_fuente"),
        "miembros_geos": len(sim["miembros"]),
        "re_ejecuto_modelo_a": False,
        "latencia_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        "pixel": {
            "pixel_idx": idx,
            "pixel_id": sim["pixel_id"][idx],
            "lat": sim["lat"][idx],
            "lon": sim["lon"][idx],
            "das_hoy": das_hoy,
            "dr0_con_margen_mm": dr0,
            "dr0_sin_margen_mm": sim["dr0_sin_margen_mm"][idx],
        },
        "riego_simulado": {"lamina_riego_mm": lamina_mm, "dia_aplicacion": dia_ap},
        "dias_ganados_antes_de_rojo": ganados,
        "escenario_sin_riego": {
            "dias_hasta_amarillo": res_sin["dias_hasta_amarillo"],
            "dias_hasta_rojo": res_sin["dias_hasta_rojo"],
            "horizontes": res_sin["horizontes"],
            "primeros_7_dias": res_sin["primeros_7_dias"],
        },
        "escenario_con_riego": {
            "dias_hasta_amarillo": res_con["dias_hasta_amarillo"],
            "dias_hasta_rojo": res_con["dias_hasta_rojo"],
            "horizontes": res_con["horizontes"],
            "primeros_7_dias": res_con["primeros_7_dias"],
        },
    }


def consultar_semaforo_pixel(params: dict) -> dict:
    """Consulta puntual de un píxel + resumen zonal 400 ha (`GET /semaforo_c`) sin depender de simulador_riego.json."""
    emision = _resolve_emision(params)
    sem = _read_s3_json(f"{S3_APP_PREFIX}/emision={emision}/semaforo.json")
    res = _read_s3_json(f"{S3_APP_PREFIX}/emision={emision}/resumen.json")
    px_geo = sem["pixeles"]
    idx = _find_pixel_index(px_geo, params)

    horizontes_px = {}
    for h in (0, 7, 15, 30):
        dh = sem["dias"][h]
        c_ch = dh["clase"][idx]
        horizontes_px[f"h{h}"] = {
            "fecha": dh["fecha"],
            "clase": CLASE_LBL.get(c_ch, "desconocido"),
            "deficit_mm": dh["deficit_mm"][idx],
            "marca_fitosanitaria": idx in dh["marca"],
        }

    return {
        "modo": "SEMAFORO_UNIFICADO_A_B_FAO56",
        "emision": emision,
        "hoy": res["hoy"],
        "geos_inicio": res["geos_inicio"],
        "zona_ha": res["zona_ha"],
        "modelo_b": res["modelo_b"],
        "lluvia_hueco": res.get("lluvia_hueco"),
        "pixeles_en_cultivo": px_geo["n"],
        "pixel_consultado": {
            "pixel_idx": idx,
            "lat": px_geo["lat"][idx],
            "lon": px_geo["lon"][idx],
            "das_hoy": px_geo["das_hoy"][idx],
            "dias_hasta_rojo_sin_riego": px_geo["dias_hasta_rojo"][idx],
            **horizontes_px,
        },
        "dias_hasta_baja_sin_riego_pct_zona": res["dias_hasta_baja_sin_riego_pct"],
        "horizontes_zona": res["horizontes"],
        "visor_html_s3": f"s3://{S3_BUCKET}/{S3_APP_PREFIX}/emision={emision}/mapa.html",
    }


def _response_headers(content_type: str = "application/json; charset=utf-8", extra: dict = None) -> dict:
    """Cabeceras HTTP limpias: CORS lo gestiona nativamente AWS Lambda Function URL para evitar encabezados duplicados."""
    h = {
        "Content-Type": content_type,
        "Cache-Control": "public, max-age=60",
    }
    if extra:
        h.update(extra)
    return h


def _json_response(status: int, payload, accept_gzip: bool = False) -> dict:
    raw = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if accept_gzip and len(raw) > 50_000:
        gz = gzip.compress(raw, compresslevel=6)
        return {
            "statusCode": status,
            "headers": _response_headers(extra={"Content-Encoding": "gzip"}),
            "body": base64.b64encode(gz).decode("ascii"),
            "isBase64Encoded": True,
        }
    return {
        "statusCode": status,
        "headers": _response_headers(),
        "body": raw.decode("utf-8"),
        "isBase64Encoded": False,
    }


def lambda_handler(event, context=None):
    """Manejador oficial AWS Lambda Function URL / API Gateway HTTP API (v2.0)."""
    req_ctx = event.get("requestContext") or {}
    http_ctx = req_ctx.get("http") or {}
    method = str(http_ctx.get("method") or event.get("httpMethod") or "GET").upper()
    path = str(event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    query = event.get("queryStringParameters") or {}
    headers_in = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    accept_gzip = "gzip" in headers_in.get("accept-encoding", "").lower()

    if method == "OPTIONS":
        return {"statusCode": 204, "headers": _response_headers(), "body": ""}

    body = {}
    if event.get("body"):
        raw_body = event["body"]
        if event.get("isBase64Encoded"):
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        body = json.loads(raw_body) if isinstance(raw_body, str) else raw_body
    params = {**query, **(body if isinstance(body, dict) else {})}

    try:
        if path in ("/", "/health"):
            latest = _get_latest()
            return _json_response(
                200,
                {
                    "servicio": "SIRA API Serving (ML <-> Big Data <-> Lovable)",
                    "estado": "OK",
                    "bucket": S3_BUCKET,
                    "ultima_emision": latest,
                    "rutas": [
                        "GET /health",
                        "GET /latest",
                        "GET /zonas",
                        "GET /resumen",
                        "GET /semaforo",
                        "GET /semaforo_c?lat=4.1895&lon=-74.9621",
                        "POST /regar_hoy",
                    ],
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                },
            )
        if path == "/latest":
            return _json_response(200, _get_latest())
        if path == "/zonas":
            return _json_response(200, _read_s3_bytes(f"{S3_APP_PREFIX}/zonas.json"), accept_gzip=accept_gzip)
        if path == "/resumen":
            em = _resolve_emision(params)
            return _json_response(200, _read_s3_bytes(f"{S3_APP_PREFIX}/emision={em}/resumen.json"), accept_gzip=accept_gzip)
        if path == "/semaforo":
            em = _resolve_emision(params)
            return _json_response(200, _read_s3_bytes(f"{S3_APP_PREFIX}/emision={em}/semaforo.json"), accept_gzip=accept_gzip)
        if path == "/semaforo_c":
            return _json_response(200, consultar_semaforo_pixel(params))
        if path == "/regar_hoy":
            return _json_response(200, simular_regar_hoy(params))
        return _json_response(404, {"error": f"Ruta no encontrada: {method} {path}"})
    except Exception as exc:
        return _json_response(500, {"error": f"{type(exc).__name__}: {exc}", "path": path})


if __name__ == "__main__":
    # Modo prueba local contra AWS S3 usando las credenciales del proyecto si no hay variables de entorno
    if not os.environ.get("AWS_ACCESS_KEY_ID"):
        creds_csv = os.environ.get(
            "AWS_CREDENTIALS_CSV",
            r"C:\Users\Sismo\Documents\EDUCATION\EAFIT\10th_semester\Big_Data\01-Session\admin-gomsal_accessKeys.csv",
        )
        if os.path.exists(creds_csv):
            import csv

            with open(creds_csv, mode="r", encoding="utf-8-sig") as f:
                row = next(csv.DictReader(f))
            ak = [v for k, v in row.items() if "Id" in k or "ID" in k][0].strip()
            sk = [v for k, v in row.items() if "Secret" in k][0].strip()
            _S3_CLIENT = boto3.Session(
                aws_access_key_id=ak, aws_secret_access_key=sk, region_name=AWS_REGION
            ).client("s3")

    print("=== PRUEBA 1: GET /latest ===")
    print(lambda_handler({"rawPath": "/latest"})["body"])
    print("\n=== PRUEBA 2: GET /semaforo_c (Nataima centro) ===")
    print(json.dumps(consultar_semaforo_pixel({"lat": 4.1895, "lon": -74.9621}), indent=2, ensure_ascii=False))
    print("\n=== PRUEBA 3: POST /regar_hoy (30 mm hoy) ===")
    print(json.dumps(simular_regar_hoy({"lat": 4.1895, "lon": -74.9621, "lamina_riego_mm": 30.0, "dia_aplicacion": 0}), indent=2, ensure_ascii=False))
