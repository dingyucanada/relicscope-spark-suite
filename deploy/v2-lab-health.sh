#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_LAB_ENV_FILE:-${project_root}/.env.v2.lab}"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

for command_name in awk curl docker mktemp python3; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
[[ -f "${env_file}" ]] || fail ".env.v2.lab is missing"

cfg() {
  local key="$1"
  local fallback="${2-}"
  local value=""
  value="$(awk -v wanted="${key}" '$0 ~ "^[[:space:]]*" wanted "=" {sub("^[[:space:]]*" wanted "=", "", $0); found=$0} END {print found}' "${env_file}")"
  value="${value%$'\r'}"
  [[ "${value}" == \"*\" && "${value}" == *\" ]] && value="${value:1:${#value}-2}"
  [[ "${value}" == \'*\' && "${value}" == *\' ]] && value="${value:1:${#value}-2}"
  printf '%s' "${value:-${fallback}}"
}

absolute_path() {
  [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "${project_root}" "$1"
}

hostname="$(cfg LAB_HOSTNAME spark-lab.local)"
bind_ip="$(cfg LAB_BIND_IP 127.0.0.1)"
port="$(cfg LAB_HTTPS_PORT 8444)"
timeout_seconds="$(cfg LAB_HEALTH_TIMEOUT_SECONDS 1800)"
model="$(cfg LAB_MODEL)"
python3 - "${hostname}" "${bind_ip}" "${port}" "${timeout_seconds}" <<'PY'
import ipaddress
import re
import sys

hostname, address_text, port_text, timeout_text = sys.argv[1:]
if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", hostname):
    raise SystemExit("LAB_HOSTNAME is invalid")
address = ipaddress.ip_address(address_text)
if address.version != 4 or (not address.is_loopback and not address.is_private):
    raise SystemExit("LAB_BIND_IP must be loopback or private IPv4")
port = int(port_text)
timeout = int(timeout_text)
if not 1024 <= port <= 65535:
    raise SystemExit("LAB_HTTPS_PORT must be an unprivileged TCP port")
if not 5 <= timeout <= 3600:
    raise SystemExit("LAB_HEALTH_TIMEOUT_SECONDS must be between 5 and 3600")
PY

caddy_data="$(absolute_path "$(cfg LAB_CADDY_DATA_DIR ./runtime/lab-caddy/data)")"
ca_file="${caddy_data}/caddy/pki/authorities/local/root.crt"
secret_file="$(absolute_path "$(cfg LAB_API_KEY_FILE ./secrets/lab_api_key)")"
[[ -s "${secret_file}" ]] || fail "lab API key is missing"
python3 - "${secret_file}" <<'PY'
import pathlib
import re
import sys

value = pathlib.Path(sys.argv[1]).read_text(encoding="ascii").strip()
if not re.fullmatch(r"[A-Za-z0-9._~-]{32,256}", value):
    raise SystemExit("lab API key is invalid")
PY
api_key="$(<"${secret_file}")"
header_file="$(mktemp "${TMPDIR:-/tmp}/relicscope-lab-header.XXXXXX")"
trap 'rm -f -- "${header_file}"' EXIT
printf 'Authorization: Bearer %s\n' "${api_key}" >"${header_file}"
chmod 600 "${header_file}"
unset api_key

docker compose --env-file "${env_file}" -f "${project_root}/compose.v2.lab.yml" ps
started_at="${SECONDS}"
last_detail="HTTPS endpoint has not answered"
while ((SECONDS - started_at < timeout_seconds)); do
  if [[ ! -s "${ca_file}" ]]; then
    last_detail="waiting for the Caddy local CA"
  elif model_json="$(
    curl --fail --silent --show-error \
      --connect-timeout 5 \
      --max-time 20 \
      --cacert "${ca_file}" \
      --resolve "${hostname}:${port}:${bind_ip}" \
      --header "@${header_file}" \
      "https://${hostname}:${port}/v1/models" 2>&1
  )"; then
    if readiness="$(MODEL_JSON="${model_json}" python3 - "${model}" <<'PY' 2>&1
import json
import os
import sys

expected_model = sys.argv[1]
value = json.loads(os.environ["MODEL_JSON"])
if value.get("object") != "list" or not isinstance(value.get("data"), list):
    raise SystemExit("/v1/models did not return the OpenAI-compatible model list")
model_ids = [item.get("id") for item in value["data"] if isinstance(item, dict)]
if expected_model not in model_ids:
    raise SystemExit(f"configured model is absent from /v1/models: {model_ids}")
print(f"PASS: private HTTPS model endpoint is ready | model={expected_model}")
PY
    )"; then
      printf '%s\n' "${readiness}"
      exit 0
    else
      last_detail="${readiness}"
    fi
  else
    last_detail="${model_json}"
  fi
  printf 'WAIT: %s\n' "${last_detail}" >&2
  sleep 5
done
fail "timed out waiting for the exact lab model through HTTPS /v1/models: ${last_detail}"
