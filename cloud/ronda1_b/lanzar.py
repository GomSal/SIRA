"""Lanza la ronda 1 de B en EC2 (us-west-2) con salvaguardas deterministas.

Salvaguardas:
  1. Tope duro: `shutdown -h +60` al arrancar + InstanceInitiatedShutdownBehavior=terminate
     → a los 60 min la instancia se TERMINA pase lo que pase. Disco con DeleteOnTermination=true.
  2. Tiempos internos: QA ≤ 10 min, corrida ≤ 35 min (`timeout`); si se pasan → estado TOPE_TIEMPO.
  3. Prueba QA en la instancia antes de la corrida larga; si no imprime "QA OK" → QA_FALLIDO y se apaga.
  4. Trampa EXIT: ante error o fin, sube log + estado + todo lo construido (tar.gz) y apaga.
  5. Log y estado se suben cada 60 s; el job sube un parcial después de cada etapa.
  6. Sin `set -u`; cero bytes CR en lo que corre en Linux; URLs prefirmadas insertadas con Python (no sed)
     y probadas con un PUT + lectura antes de lanzar.
Uso: python lanzar.py            (prepara, verifica y lanza; imprime instance_id)
     python lanzar.py --solo-preparar
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

import boto3

AQUI = Path(__file__).resolve().parent
REGION = "us-west-2"
CUENTA = boto3.client("sts").get_caller_identity()["Account"]
BUCKET = f"sira-{CUENTA}-usw2"
PREFIJO = "ronda1_b/" + time.strftime("%Y%m%d-%H%M%S")
TIPO = "t3.medium"
TOPE_MIN = 60
EXPIRA_S = 8 * 3600

from botocore.config import Config  # noqa: E402

# endpoint regional explícito: un bucket recién creado responde 307 por el endpoint global
s3 = boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com",
                  config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
ec2 = boto3.client("ec2", region_name=REGION)

USER_DATA = r"""#!/bin/bash
shutdown -h +__TOPE__
mkdir -p /opt/sira && cd /opt/sira
LOG=/opt/sira/run.log
touch $LOG
exec > >(tee -a $LOG) 2>&1
subir(){
  curl -s -f -T $LOG "__URL_LOG__" >/dev/null
  curl -s -f -T /opt/sira/status.txt "__URL_STATUS__" >/dev/null
}
estado(){ echo "$1" > /opt/sira/status.txt; echo "ESTADO: $1 $(date -u +%H:%M:%S)"; subir; }
final(){
  S=$(cat /opt/sira/status.txt 2>/dev/null)
  case "$S" in OK|QA_FALLIDO|ERROR*|TOPE*) ;; *) echo "TERMINADO_INESPERADO_$S" > /opt/sira/status.txt;; esac
  if [ -d /opt/sira/out ]; then tar czf /opt/sira/out_final.tar.gz -C /opt/sira out && curl -s -f -T /opt/sira/out_final.tar.gz "__URL_PARCIAL__" >/dev/null; fi
  if [ -d /opt/sira/out_qa ]; then tar czf /opt/sira/out_qa.tar.gz -C /opt/sira out_qa && curl -s -f -T /opt/sira/out_qa.tar.gz "__URL_QA_PARCIAL__" >/dev/null; fi
  subir
  shutdown -h now
}
trap final EXIT
estado ARRANCANDO
(while true; do sleep 60; subir; done) &
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -q && apt-get install -y -q python3-venv python3-pip curl
curl -s -f -o codigo.tar.gz "__URL_CODIGO__" || { estado ERROR_DESCARGA_CODIGO; exit 1; }
tar xzf codigo.tar.gz
estado INSTALANDO
python3 -m venv v && v/bin/pip install -q numpy pandas scipy scikit-learn rasterio pystac-client planetary-computer pyproj shapely requests || { estado ERROR_PIP; exit 1; }
estado QA
cd /opt/sira/ronda1_b
SIRA_URL_PARCIAL="__URL_QA_PARCIAL__" timeout 10m /opt/sira/v/bin/python -W ignore job_b.py --qa --out /opt/sira/out_qa > /opt/sira/qa.log 2>&1
cat /opt/sira/qa.log
grep -q "QA OK" /opt/sira/qa.log || { estado QA_FALLIDO; exit 1; }
estado CORRIENDO
SIRA_URL_PARCIAL="__URL_PARCIAL__" timeout 35m /opt/sira/v/bin/python -W ignore job_b.py --out /opt/sira/out
rc=$?
if [ $rc -eq 0 ]; then estado OK; elif [ $rc -eq 124 ]; then estado TOPE_TIEMPO; else estado ERROR_$rc; fi
"""


def bucket() -> None:
    try:
        s3.head_bucket(Bucket=BUCKET)
    except Exception:  # noqa: BLE001
        s3.create_bucket(Bucket=BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION})
        s3.put_public_access_block(Bucket=BUCKET, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})


def empaquetar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for f in [AQUI / "job_b.py", *sorted((AQUI / "datos").iterdir())]:
            data = f.read_bytes()
            if f.suffix == ".py":
                assert data.count(b"\r") == 0, f"CR en {f}"
            info = tarfile.TarInfo(f"ronda1_b/{f.relative_to(AQUI).as_posix()}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def url(op: str, clave: str) -> str:
    return s3.generate_presigned_url(op, Params={"Bucket": BUCKET, "Key": f"{PREFIJO}/{clave}"}, ExpiresIn=EXPIRA_S)


def preparar() -> tuple[str, dict]:
    bucket()
    s3.put_object(Bucket=BUCKET, Key=f"{PREFIJO}/codigo.tar.gz", Body=empaquetar())
    urls = {"__URL_CODIGO__": url("get_object", "codigo.tar.gz"),
            "__URL_LOG__": url("put_object", "run.log"),
            "__URL_STATUS__": url("put_object", "status.txt"),
            "__URL_PARCIAL__": url("put_object", "out.tar.gz"),
            "__URL_QA_PARCIAL__": url("put_object", "out_qa.tar.gz")}
    ud = USER_DATA.replace("__TOPE__", str(TOPE_MIN))
    for k, v in urls.items():
        ud = ud.replace(k, v)
    assert "__" not in ud.replace("__name__", ""), "quedó un marcador sin reemplazar"
    assert ud.encode().count(b"\r") == 0, "user-data con CR"
    # prueba real de cada URL: PUT de un archivo y lectura de vuelta; GET del código
    for k in ("__URL_LOG__", "__URL_STATUS__", "__URL_PARCIAL__", "__URL_QA_PARCIAL__"):
        u = [line for line in ud.splitlines() if urls[k] in line][0]
        u = urls[k]
        assert u in ud
        req = urllib.request.Request(u, data=b"prueba-preflight", method="PUT")
        assert urllib.request.urlopen(req, timeout=30).status == 200, k
    leido = s3.get_object(Bucket=BUCKET, Key=f"{PREFIJO}/status.txt")["Body"].read()
    assert leido == b"prueba-preflight", "no se pudo leer de vuelta"
    assert urllib.request.urlopen(urls["__URL_CODIGO__"], timeout=30).status == 200
    for clave in ("run.log", "status.txt", "out.tar.gz", "out_qa.tar.gz"):
        s3.delete_object(Bucket=BUCKET, Key=f"{PREFIJO}/{clave}")
    (AQUI / "user_data.generado.sh").write_bytes(ud.encode())
    return ud, urls


def lanzar(ud: str) -> str:
    activas = ec2.describe_instances(Filters=[{"Name": "instance-state-name", "Values": ["pending", "running"]}])
    n = sum(len(r["Instances"]) for r in activas["Reservations"])
    assert n == 0, f"ya hay {n} instancias encendidas en {REGION}"
    ami = boto3.client("ssm", region_name=REGION).get_parameter(
        Name="/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id")["Parameter"]["Value"]
    raiz = ec2.describe_images(ImageIds=[ami])["Images"][0]["RootDeviceName"]
    r = ec2.run_instances(
        ImageId=ami, InstanceType=TIPO, MinCount=1, MaxCount=1, UserData=ud,
        InstanceInitiatedShutdownBehavior="terminate",
        BlockDeviceMappings=[{"DeviceName": raiz, "Ebs": {"VolumeSize": 16, "VolumeType": "gp3", "DeleteOnTermination": True}}],
        NetworkInterfaces=[{"DeviceIndex": 0, "AssociatePublicIpAddress": True,
                            "SubnetId": subred_por_defecto()}],
        TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": "sira-ronda1-b"},
                                                                  {"Key": "Proyecto", "Value": "SIRA"}]}])
    iid = r["Instances"][0]["InstanceId"]
    ec2.get_waiter("instance_exists").wait(InstanceIds=[iid])
    time.sleep(5)
    attr = ec2.describe_instance_attribute(InstanceId=iid, Attribute="instanceInitiatedShutdownBehavior")
    assert attr["InstanceInitiatedShutdownBehavior"]["Value"] == "terminate"
    inst = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
    for bdm in inst.get("BlockDeviceMappings", []):
        assert bdm["Ebs"]["DeleteOnTermination"], "disco sin DeleteOnTermination"
    return iid


def subred_por_defecto() -> str:
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subs = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}, {"Name": "default-for-az", "Values": ["true"]}])
    return sorted(subs["Subnets"], key=lambda s: s["AvailabilityZone"])[0]["SubnetId"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-preparar", action="store_true")
    a = ap.parse_args()
    ud, urls = preparar()
    info = {"bucket": BUCKET, "prefijo": PREFIJO, "region": REGION, "tipo": TIPO, "tope_min": TOPE_MIN}
    print("preflight OK:", json.dumps(info))
    if not a.solo_preparar:
        iid = lanzar(ud)
        info["instance_id"] = iid
        print("LANZADA:", json.dumps(info))
    (AQUI / "ultima_corrida.json").write_text(json.dumps(info, indent=1))
