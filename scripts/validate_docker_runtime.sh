#!/usr/bin/env bash
set -Eeuo pipefail

: "${BASE_URL:=http://127.0.0.1:8080}"

docker compose config >/dev/null
docker compose build
docker compose up -d
cleanup() { docker compose down; }
trap cleanup EXIT

for attempt in $(seq 1 30); do
  if curl --fail --silent "$BASE_URL/healthz" >/tmp/dz23-health.json; then break; fi
  sleep 2
  if [[ "$attempt" == 30 ]]; then echo "healthcheck timeout" >&2; exit 1; fi
done
curl --fail --silent "$BASE_URL/healthz"
curl --fail --silent "$BASE_URL/readyz"
docker compose restart tradingagents
sleep 5
curl --fail --silent "$BASE_URL/healthz"
curl --fail --silent "$BASE_URL/readyz"
printf '%s\n' 'docker runtime validation passed; inspect volumes and logs before production use'
