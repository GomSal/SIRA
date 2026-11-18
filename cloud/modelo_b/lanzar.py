"""Lanza el entrenamiento del Modelo B en EC2 on-demand (us-west-2) con salvaguardas. Parte de cloud/ronda1_b/lanzar.py.

Salvaguardas de tiempo (paran y apagan):
  1. `shutdown -h +120` al arrancar + InstanceInitiatedShutdownBehavior=terminate → a los 120 min la instancia se
     TERMINA pase lo que pase. Disco con DeleteOnTermination=true, verificado después de lanzar.
  2. Topes por etapa con `timeout`: QA 15 min; Prithvi 40 min; entrenamiento y evaluación 50 min → TOPE_TIEMPO_<etapa>.
  3. La QA mide tiempos y proyecta el total; si pasa de 90 min → ESTIMADO_EXCEDE y se apaga sin correr.
  4. Vigilante: si el archivo `latido` del job tiene más de 8 min → ERROR_SIN_PROGRESO, mata el job y se apaga.
Salvaguardas de error: sin `set -u`; `trap final EXIT` sube log, estado y lo construido y apaga; estados específicos
(ERROR_DATOS, ERROR_NO_REGRESION, ERROR_PRITHVI, ERROR_NUMERICO, ERROR_PASO<n>, ERROR_PIP); log y estado a S3 cada 60 s.
Alerta: tema SNS `sira-alertas` (correo) + regla de EventBridge por corrida (shutting-down/stopped/terminated).

Uso:
  python lanzar.py --tema-correo usuario@dominio     crea el tema SNS y la suscripción (confirmar el correo de AWS)
  python lanzar.py --solo-preparar                   empaqueta, sube y prueba las URLs; no lanza
  python lanzar.py [--tipo c7i.2xlarge]              prepara, verifica, lanza y crea la regla de EventBridge
  python lanzar.py --borrar-regla                    borra la regla de la última corrida
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
from botocore.config import Config

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
REGION = "us-west-2"
CUENTA = boto3.client("sts").get_caller_identity()["Account"]
BUCKET = f"sira-{CUENTA}-usw2"
TOPE_MIN = 120
EXPIRA_S = 12 * 3600
TEMA = "sira-alertas"

s3 = boto3.client("s3", region_name=REGION, endpoint_url=f"https://s3.{REGION}.amazonaws.com",
                  config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
ec2 = boto3.client("ec2", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)
events = boto3.client("events", region_name=REGION)

USER_DATA = r"""#!/bin/bash
shutdown -h +__TOPE__
mkdir -p /opt/sira && cd /opt/sira
LOG=/opt/sira/run.log
touch $LOG
exec > >(tee -a $LOG) 2>&1
export SIRA_LATIDO=/opt/sira/latido
export SIRA_GIT_COMMIT="__COMMIT__"
export PYTHONIOENCODING=utf-8
subir(){
  curl -s -f -T $LOG "__URL_LOG__" >/dev/null
  curl -s -f -T /opt/sira/status.txt "__URL_STATUS__" >/dev/null
}
estado(){ echo "$1" > /opt/sira/status.txt; echo "ESTADO: $1 $(date -u +%H:%M:%S)"; subir; }
es_final(){ case "$(cat /opt/sira/status.txt 2>/dev/null)" in OK|QA_FALLIDO|ESTIMADO_EXCEDE|ERROR*|TOPE*) return 0;; *) return 1;; esac; }
final(){
  if ! es_final; then S=$(cat /opt/sira/status.txt 2>/dev/null); echo "TERMINADO_INESPERADO_$S" > /opt/sira/status.txt; fi
  kill $VIGIA $SUBIDOR 2>/dev/null
  cd /opt/sira
  if [ -d out ]; then tar czf out.tar.gz out && curl -s -f -T out.tar.gz "__URL_OUT__" >/dev/null; fi
  if [ -d out_qa ]; then tar czf out_qa.tar.gz out_qa && curl -s -f -T out_qa.tar.gz "__URL_QA__" >/dev/null; fi
  echo "FIN $(cat /opt/sira/status.txt) $(date -u +%H:%M:%S)"
  subir
  shutdown -h now
}
trap final EXIT
estado ARRANCANDO
(while true; do sleep 60; subir; done) &
SUBIDOR=$!
vigia(){
  while true; do
    sleep 60
    if [ -f $SIRA_LATIDO ]; then
      edad=$(( $(date +%s) - $(stat -c %Y $SIRA_LATIDO) ))
      if [ $edad -gt 480 ]; then
        estado ERROR_SIN_PROGRESO
        pkill -f job_modelo_b.py; sleep 20; pkill -9 -f job_modelo_b.py
        return
      fi
    fi
  done
}
correr(){
  nombre=$1; tope=$2; shift 2
  touch $SIRA_LATIDO
  timeout ${tope}m /opt/sira/v/bin/python -W ignore /opt/sira/modelo_b/job_modelo_b.py "$@"
  rc=$?
  if es_final; then exit 1; fi
  if [ $rc -eq 124 ]; then estado TOPE_TIEMPO_$nombre; exit 1; fi
  return 0
}
estado_job(){ cat "$1/estado_job.txt" 2>/dev/null | tr -d '\n'; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -q && apt-get install -y -q python3-venv python3-pip curl
curl -s -f -o codigo.tar.gz "__URL_CODIGO__" || { estado ERROR_DESCARGA_CODIGO; exit 1; }
tar xzf codigo.tar.gz
estado INSTALANDO
python3 -m venv v && v/bin/pip install -q --upgrade pip \
  && v/bin/pip install -q --only-binary=:all: torch==__TORCH__ torchvision==__TORCHVISION__ --index-url https://download.pytorch.org/whl/cpu \
  && v/bin/pip install -q --only-binary=:all: -r modelo_b/requirements.txt || { estado ERROR_PIP; exit 1; }
v/bin/pip freeze > /opt/sira/pip_freeze.txt
nproc; free -g; df -h /
vigia &
VIGIA=$!
estado QA
SIRA_URL_PARCIAL="__URL_QA_PARCIAL__" correr QA 15 --qa --out /opt/sira/out_qa __ARGS__ > /opt/sira/qa.log 2>&1
cat /opt/sira/qa.log
EJ=$(estado_job /opt/sira/out_qa)
cp /opt/sira/out_qa/proyeccion.json /opt/sira/proyeccion.json 2>/dev/null
if [ "$EJ" = "ESTIMADO_EXCEDE" ]; then estado ESTIMADO_EXCEDE; exit 1; fi
case "$EJ" in ERROR*) estado $EJ; exit 1;; esac
grep -q "QA OK" /opt/sira/qa.log && [ "$EJ" = "QA_OK" ] || { estado QA_FALLIDO; exit 1; }
estado PRITHVI
mkdir -p /opt/sira/out
SIRA_URL_PARCIAL="__URL_PARCIAL__" SIRA_URL_PRITHVI="__URL_PRITHVI__" correr PRITHVI 40 --etapa prithvi --out /opt/sira/out
EJ=$(estado_job /opt/sira/out)
[ "$EJ" = "OK" ] || { estado ${EJ:-ERROR_PASO2}; exit 1; }
estado ENTRENANDO
SIRA_URL_PARCIAL="__URL_PARCIAL__" correr ENTRENAMIENTO 50 --etapa modelo --out /opt/sira/out __ARGS__
EJ=$(estado_job /opt/sira/out)
[ "$EJ" = "OK" ] || { estado ${EJ:-ERROR_PASO0}; exit 1; }
cp /opt/sira/pip_freeze.txt /opt/sira/out/ 2>/dev/null
estado OK
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
    archivos = [AQUI / "job_modelo_b.py", AQUI / "requirements.txt", *sorted((AQUI / "datos").iterdir())]
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for f in archivos:
            data = f.read_bytes()
            if f.suffix in (".py", ".sh", ".txt"):
                assert data.count(b"\r") == 0, f"CR en {f}"
            if f.suffix == ".csv":
                data = data.replace(b"\r\n", b"\n")
                assert data.count(b"\r") == 0, f"CR en {f}"
            info = tarfile.TarInfo(f"modelo_b/{f.relative_to(AQUI).as_posix()}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def url(prefijo: str, op: str, clave: str) -> str:
    return s3.generate_presigned_url(op, Params={"Bucket": BUCKET, "Key": f"{prefijo}/{clave}"}, ExpiresIn=EXPIRA_S)


def versiones_torch() -> tuple[str, str]:
    req = (AQUI / "requirements.txt").read_text().splitlines()
    v = {ln.split("==")[0].strip(): ln.split("==")[1].split()[0] for ln in req if "==" in ln and not ln.startswith("#")}
    return v["torch"], v["torchvision"]


def preparar(prefijo: str, args_job: str) -> tuple[str, dict]:
    bucket()
    s3.put_object(Bucket=BUCKET, Key=f"{prefijo}/codigo.tar.gz", Body=empaquetar())
    puts = {"__URL_LOG__": "run.log", "__URL_STATUS__": "status.txt", "__URL_OUT__": "out.tar.gz",
            "__URL_QA__": "out_qa.tar.gz", "__URL_PARCIAL__": "out_parcial.tar.gz",
            "__URL_QA_PARCIAL__": "out_qa_parcial.tar.gz", "__URL_PRITHVI__": "prithvi_embeddings.csv"}
    urls = {k: url(prefijo, "put_object", v) for k, v in puts.items()}
    urls["__URL_CODIGO__"] = url(prefijo, "get_object", "codigo.tar.gz")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=RAIZ, capture_output=True, text=True).stdout.strip()
    tv, tvv = versiones_torch()
    ud = USER_DATA
    for k, v in {"__TOPE__": str(TOPE_MIN), "__COMMIT__": commit, "__ARGS__": args_job, "__TORCH__": tv,
                 "__TORCHVISION__": tvv, **urls}.items():
        ud = ud.replace(k, v)
    resto = [m for m in ud.split("__")[1::2] if m.isupper() or m.startswith("URL")]
    assert "__URL" not in ud and "__TOPE" not in ud and not resto, f"marcadores sin reemplazar: {resto}"
    assert ud.encode().count(b"\r") == 0, "user-data con CR"
    assert len(ud.encode()) < 16000, "user-data > 16 KB"
    # prueba real de cada URL: PUT y lectura de vuelta; GET del código
    for k, clave in puts.items():
        cuerpo = f"prueba-preflight {clave}".encode()
        req = urllib.request.Request(urls[k], data=cuerpo, method="PUT")
        assert urllib.request.urlopen(req, timeout=30).status == 200, k
        assert s3.get_object(Bucket=BUCKET, Key=f"{prefijo}/{clave}")["Body"].read() == cuerpo, f"no se leyó {clave}"
        s3.delete_object(Bucket=BUCKET, Key=f"{prefijo}/{clave}")
    assert urllib.request.urlopen(urls["__URL_CODIGO__"], timeout=30).status == 200
    (AQUI / "user_data.generado.sh").write_bytes(ud.encode())
    return ud, urls


def subred_por_defecto() -> str:
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subs = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}, {"Name": "default-for-az", "Values": ["true"]}])
    return sorted(subs["Subnets"], key=lambda s: s["AvailabilityZone"])[0]["SubnetId"]


def lanzar(ud: str, tipo: str, disco_gb: int) -> str:
    activas = ec2.describe_instances(Filters=[{"Name": "instance-state-name", "Values": ["pending", "running"]}])
    n = sum(len(r["Instances"]) for r in activas["Reservations"])
    assert n == 0, f"ya hay {n} instancias encendidas en {REGION}"
    ami = boto3.client("ssm", region_name=REGION).get_parameter(
        Name="/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id")["Parameter"]["Value"]
    raiz = ec2.describe_images(ImageIds=[ami])["Images"][0]["RootDeviceName"]
    r = ec2.run_instances(
        ImageId=ami, InstanceType=tipo, MinCount=1, MaxCount=1, UserData=ud,
        InstanceInitiatedShutdownBehavior="terminate",
        BlockDeviceMappings=[{"DeviceName": raiz, "Ebs": {"VolumeSize": disco_gb, "VolumeType": "gp3",
                                                          "DeleteOnTermination": True}}],
        NetworkInterfaces=[{"DeviceIndex": 0, "AssociatePublicIpAddress": True, "SubnetId": subred_por_defecto()}],
        TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": "sira-modelo-b"},
                                                                  {"Key": "Proyecto", "Value": "SIRA"}]},
                           {"ResourceType": "volume", "Tags": [{"Key": "Proyecto", "Value": "SIRA"}]}])
    return r["Instances"][0]["InstanceId"]


def verificar_instancia(iid: str) -> None:
    ec2.get_waiter("instance_exists").wait(InstanceIds=[iid])
    for _ in range(30):
        inst = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]
        if inst.get("BlockDeviceMappings"):
            break
        time.sleep(5)
    attr = ec2.describe_instance_attribute(InstanceId=iid, Attribute="instanceInitiatedShutdownBehavior")
    assert attr["InstanceInitiatedShutdownBehavior"]["Value"] == "terminate"
    assert inst["BlockDeviceMappings"], "sin discos"
    for bdm in inst["BlockDeviceMappings"]:
        assert bdm["Ebs"]["DeleteOnTermination"], "disco sin DeleteOnTermination"


def tema_arn() -> str:
    return sns.create_topic(Name=TEMA)["TopicArn"]  # idempotente


def crear_tema(correo: str) -> str:
    arn = tema_arn()
    pol = {"Version": "2012-10-17", "Statement": [
        {"Sid": "propietario", "Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{CUENTA}:root"},
         "Action": ["SNS:Publish", "SNS:Subscribe", "SNS:GetTopicAttributes", "SNS:SetTopicAttributes",
                    "SNS:ListSubscriptionsByTopic"], "Resource": arn},
        {"Sid": "eventbridge", "Effect": "Allow", "Principal": {"Service": "events.amazonaws.com"},
         "Action": "sns:Publish", "Resource": arn, "Condition": {"StringEquals": {"aws:SourceAccount": CUENTA}}}]}
    sns.set_topic_attributes(TopicArn=arn, AttributeName="Policy", AttributeValue=json.dumps(pol))
    ya = [s for s in sns.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"] if s["Endpoint"] == correo]
    if not ya:
        sns.subscribe(TopicArn=arn, Protocol="email", Endpoint=correo)
    return arn


def suscripcion_confirmada() -> bool:
    subs = sns.list_subscriptions_by_topic(TopicArn=tema_arn())["Subscriptions"]
    return any(s["Protocol"] == "email" and s["SubscriptionArn"].startswith("arn:") for s in subs)


def crear_regla(iid: str, prefijo: str) -> str:
    nombre = f"sira-modelo-b-{iid}"
    patron = {"source": ["aws.ec2"], "detail-type": ["EC2 Instance State-change Notification"],
              "detail": {"state": ["shutting-down", "stopped", "terminated"], "instance-id": [iid]}}
    events.put_rule(Name=nombre, EventPattern=json.dumps(patron), State="ENABLED",
                    Description="SIRA modelo B: avisa cuando la máquina se apaga")
    events.put_targets(Rule=nombre, Targets=[{
        "Id": "correo", "Arn": tema_arn(),
        "InputTransformer": {
            "InputPathsMap": {"i": "$.detail.instance-id", "s": "$.detail.state", "t": "$.time"},
            "InputTemplate": f'"SIRA modelo B: la máquina <i> pasó a <s> (<t>). Estado y log: s3://{BUCKET}/{prefijo}/status.txt y run.log"'}}])
    return nombre


def borrar_regla(nombre: str) -> None:
    try:
        events.remove_targets(Rule=nombre, Ids=["correo"])
    except events.exceptions.ResourceNotFoundException:
        pass
    try:
        events.delete_rule(Name=nombre)
    except events.exceptions.ResourceNotFoundException:
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--solo-preparar", action="store_true")
    ap.add_argument("--tema-correo", default=None)
    ap.add_argument("--borrar-regla", action="store_true")
    ap.add_argument("--tipo", default="c7i.2xlarge")
    ap.add_argument("--disco-gb", type=int, default=40)
    ap.add_argument("--sin-alerta", action="store_true", help="sin correo SNS ni regla de EventBridge")
    ap.add_argument("--args-job", default="", help="argumentos extra para el job (QA y entrenamiento)")
    a = ap.parse_args()
    if a.tema_correo:
        print("tema:", crear_tema(a.tema_correo), "· confirmar el correo de AWS")
        raise SystemExit(0)
    ult = AQUI / "ultima_corrida.json"
    if a.borrar_regla:
        info = json.loads(ult.read_text())
        borrar_regla(info["regla"])
        info["regla_borrada"] = True
        ult.write_text(json.dumps(info, indent=1))
        print("regla borrada:", info["regla"])
        raise SystemExit(0)
    prefijo = "modelo_b/" + time.strftime("%Y%m%d-%H%M%S")
    ud, urls = preparar(prefijo, a.args_job)
    info = {"bucket": BUCKET, "prefijo": prefijo, "region": REGION, "tipo": a.tipo, "tope_min": TOPE_MIN,
            "disco_gb": a.disco_gb, "args_job": a.args_job,
            "git_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=RAIZ, capture_output=True, text=True).stdout.strip()}
    print("preflight OK:", json.dumps(info))
    if not a.solo_preparar:
        if not a.sin_alerta:
            assert suscripcion_confirmada(), "la suscripción SNS por correo no está confirmada"
        iid = lanzar(ud, a.tipo, a.disco_gb)
        info["instance_id"] = iid
        info["lanzada_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        info["regla"] = None if a.sin_alerta else crear_regla(iid, prefijo)
        ult.write_text(json.dumps(info, indent=1))
        verificar_instancia(iid)
        info["verificada"] = "terminate al apagar + DeleteOnTermination"
        print("LANZADA:", json.dumps(info))
    ult.write_text(json.dumps(info, indent=1))
