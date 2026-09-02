#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_ENV_FILE:-${project_root}/.env.v2}"
compose_file="${V2_COMPOSE_FILE:-${project_root}/compose.v2.yml}"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

for command_name in curl docker python3; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
[[ -f "${env_file}" ]] || fail ".env.v2 is missing"

cfg() {
  local key="$1"
  local fallback="${2-}"
  python3 "${project_root}/deploy/read-v2-env.py" \
    --file "${env_file}" --key "${key}" --default "${fallback}"
}

hostname="$(cfg SCOUT_HOSTNAME scout.spark.local)"
bind_ip="$(cfg SCOUT_BIND_IP 127.0.0.1)"
port="$(cfg SCOUT_HTTPS_PORT 8443)"
timeout_seconds="$(cfg V2_HEALTH_TIMEOUT_SECONDS 1500)"
[[ "${timeout_seconds}" =~ ^[0-9]+$ ]] || fail "V2 health timeout must be an integer"
((timeout_seconds >= 5 && timeout_seconds <= 3600)) \
  || fail "V2 health timeout must be between 5 and 3600 seconds"
caddy_data="$(cfg CADDY_DATA_DIR ./runtime/caddy/data)"
[[ "${caddy_data}" == /* ]] || caddy_data="${project_root}/${caddy_data}"
ca_file="${caddy_data}/caddy/pki/authorities/local/root.crt"
[[ -s "${ca_file}" ]] || fail "Caddy local CA is missing; inspect the ingress container"

docker compose --env-file "${env_file}" -f "${compose_file}" ps
started_at="${SECONDS}"
last_detail="service did not answer"
while ((SECONDS - started_at < timeout_seconds)); do
  if health_json="$(
    curl --fail --silent --show-error \
      --connect-timeout 3 \
      --max-time 10 \
      --cacert "${ca_file}" \
      --resolve "${hostname}:${port}:${bind_ip}" \
      "https://${hostname}:${port}/api/v2/scout/health" 2>&1
  )"; then
    if readiness="$(HEALTH_JSON="${health_json}" python3 - <<'PY' 2>&1
import json
import os

value = json.loads(os.environ["HEALTH_JSON"])
if value.get("status") != "ready" or value.get("queue_worker") != "running":
    raise SystemExit("Scout gateway or durable worker is not ready")
if value.get("queue_worker_error") is not None:
    raise SystemExit(f"Scout durable worker recorded an error: {value['queue_worker_error']}")
if value.get("model_ready") is not True:
    detail = (value.get("model") or {}).get("detail", "unknown model failure")
    raise SystemExit(f"Scout gateway is ready but the local VLM is degraded: {detail}")
if (value.get("storage") or {}).get("ready") is not True:
    raise SystemExit("Scout data volume is below its configured free-space reserve")
print(
    "PASS: gateway, durable worker and local model are ready | "
    f"node={value.get('node_id')} model={(value.get('model') or {}).get('model')}"
)
PY
    )"; then
      printf '%s\n' "${readiness}"
      exit 0
    else
      last_detail="${readiness}"
    fi
  else
    last_detail="${health_json}"
  fi
  printf 'WAIT: %s\n' "${last_detail}" >&2
  sleep 5
done
fail "timed out waiting for the full local analysis service: ${last_detail}"
