"""Sube a s3://sira-b-lakehouse-929570730700/ lo que necesita el semáforo, en prefijos propios, sin tocar los demás.

Prefijos (el resto del bucket es de la iteración B de Big Data y no se toca; el bucket tiene versionado):
  models/pronosticador_a/rf/        modelo A: predictor_ndvi/ndmi.joblib, meta.json y las tablas que lee semaforo_c
  models/semaforo_b/operacion/      modelo B de uso: arimax.json y calendarios_riego.csv
  plata/cubo_a/                     cubo de A (cubo.npz, cubo_fechas.csv) y clima observado (clima.csv)
  bronce/geos_s2s_a/                GEOS-S2S NRT en la celda de Nataima (geos_nrt.csv)
  oro/semaforo_app/                 lo que lee la app: emision=<hoy>/ (semaforo.json, resumen.json, pixeles.csv.gz, PNG,
                                    mapa.html), zonas.json y latest.json (apunta a la última emisión)
Cada objeto lleva su SHA-256 en los metadatos; si ya está igual en S3, no se vuelve a subir. Al final deja
oro/semaforo_app/manifiestos/subida_<fecha>.json con clave, tamaño y SHA-256 de todo lo subido.

    python scripts/operacion/subir_aws.py [--solo-oro] [--perfil sira-b] [--seco]
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import boto3

RAIZ = Path(__file__).resolve().parents[2]
BUCKET = "sira-b-lakehouse-929570730700"
RES_A = RAIZ / "data/interim/modelo_a/kaggle_final/resultados_a"
CUBO = RAIZ / "data/interim/modelo_a/kaggle_nb1/datos_a"
SEM = RAIZ / "reports/semaforo_c"
APP = RAIZ / "app/datos"

ap = argparse.ArgumentParser()
ap.add_argument("--solo-oro", action="store_true", help="solo la salida del semáforo (actualización diaria)")
ap.add_argument("--perfil", default="sira-b")
ap.add_argument("--seco", action="store_true", help="muestra qué subiría, sin subir")
A = ap.parse_args()

emision = json.loads((SEM / "resumen.json").read_text(encoding="utf-8"))["hoy"]
assert json.loads((APP / "semaforo.json").read_text(encoding="utf-8"))["hoy"] == emision, \
    "app/datos/semaforo.json no es de la misma corrida: correr exportar_app.py"

fijos = {
    **{f"models/pronosticador_a/rf/{n}": RES_A / "modelos" / n
       for n in ("predictor_ndvi.joblib", "predictor_ndmi.joblib", "meta.json")},
    **{f"models/pronosticador_a/rf/{n}": RES_A / n
       for n in ("paso1_curva_por_dias_desde_siembra.csv", "paso2_calibracion_landsat_s2.csv",
                 "paso8_geos_correccion.csv", "paso8_prueba_clima_observado.csv", "paso8_predicciones_prueba.csv.gz")},
    **{f"models/semaforo_b/operacion/{n}": RAIZ / "models/semaforo_b" / n for n in ("arimax.json", "calendarios_riego.csv")},
    **{f"plata/cubo_a/{n}": CUBO / n for n in ("cubo.npz", "cubo_fechas.csv", "clima.csv")},
    "bronce/geos_s2s_a/geos_nrt.csv": RAIZ / "data/processed/modelo_a/geos/geos_nrt.csv",
}
oro = {
    f"oro/semaforo_app/emision={emision}/semaforo.json": APP / "semaforo.json",
    **{f"oro/semaforo_app/emision={emision}/{p.name}": p
       for p in sorted(SEM.glob("*")) if p.suffix in (".json", ".gz", ".png", ".html", ".npz")},
    "oro/semaforo_app/zonas.json": APP / "zonas.json",
}
objetos = oro if A.solo_oro else {**fijos, **oro}
faltan = [str(p) for p in objetos.values() if not p.exists()]
assert not faltan, f"faltan archivos: {faltan}"

TIPOS = {".json": "application/json", ".csv": "text/csv", ".png": "image/png", ".html": "text/html; charset=utf-8",
         ".gz": "application/gzip"}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


s3 = boto3.Session(profile_name=A.perfil).client("s3")
manifiesto = []
for clave, p in objetos.items():
    h, n = sha256(p), p.stat().st_size
    manifiesto.append({"clave": clave, "bytes": n, "sha256": h, "origen": str(p.relative_to(RAIZ)).replace("\\", "/")})
    try:
        igual = s3.head_object(Bucket=BUCKET, Key=clave).get("Metadata", {}).get("sha256") == h
    except s3.exceptions.ClientError:
        igual = False
    estado = "igual, no se sube" if igual else ("se subiría" if A.seco else "subido")
    if not igual and not A.seco:
        extra = {"Metadata": {"sha256": h}, "ContentType": TIPOS.get(p.suffix, "application/octet-stream")}
        s3.upload_file(str(p), BUCKET, clave, ExtraArgs=extra)
    print(f"{n / 1e6:9.2f} MB  {clave}  [{estado}]", flush=True)

if not A.seco:
    puntero = {"emision": emision, "semaforo": f"emision={emision}/semaforo.json", "zonas": "zonas.json",
               "subido_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    s3.put_object(Bucket=BUCKET, Key="oro/semaforo_app/latest.json", Body=json.dumps(puntero, ensure_ascii=False),
                  ContentType="application/json")
    clave_m = f"oro/semaforo_app/manifiestos/subida_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    s3.put_object(Bucket=BUCKET, Key=clave_m, ContentType="application/json",
                  Body=json.dumps({"bucket": BUCKET, "emision": emision, "objetos": manifiesto}, ensure_ascii=False, indent=1))
    # control: lo que quedó en S3 tiene el tamaño y la huella de lo local
    for m in manifiesto:
        o = s3.head_object(Bucket=BUCKET, Key=m["clave"])
        assert o["ContentLength"] == m["bytes"] and o["Metadata"].get("sha256") == m["sha256"], m["clave"]
    print(f"listo: {len(manifiesto)} objetos verificados ({sum(m['bytes'] for m in manifiesto) / 1e6:.1f} MB) · "
          f"latest.json → emision={emision} · {clave_m}")
