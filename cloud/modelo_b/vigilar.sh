#!/bin/bash
# Vigila una corrida del Modelo B desde local: lee status.txt de S3 cada 30 s y avisa en cada cambio.
# Termina con estado final, o si el log no cambia en más de 5 min (entonces termina la instancia desde aquí).
# Uso: bash vigilar.sh <bucket> <prefijo> <instance_id>
B=$1; P=$2; I=$3
prev=""; etag_prev=""; quieto=$(date +%s)
while true; do
  s=$(aws s3 cp "s3://$B/$P/status.txt" - --region us-west-2 2>/dev/null | tr -d '\r\n')
  e=$(aws s3api head-object --bucket "$B" --key "$P/run.log" --region us-west-2 --query ETag --output text 2>/dev/null)
  ahora=$(date +%s)
  if [ -n "$e" ] && [ "$e" != "$etag_prev" ]; then etag_prev=$e; quieto=$ahora; fi
  if [ "$s" != "$prev" ]; then echo "ESTADO: ${s:-sin estado aún} ($(date +%H:%M:%S))"; prev=$s; fi
  case "$s" in
    OK|ERROR*|TOPE*|QA_FALLIDO|ESTIMADO_EXCEDE|TERMINADO_INESPERADO*) echo "FINAL: $s"; exit 0;;
  esac
  if [ -n "$etag_prev" ] && [ $((ahora - quieto)) -gt 300 ]; then
    echo "COLGADA: el log no cambia hace más de 5 min; termino $I"
    aws ec2 terminate-instances --instance-ids "$I" --region us-west-2 --query "TerminatingInstances[0].CurrentState.Name" --output text
    exit 0
  fi
  sleep 30
done
