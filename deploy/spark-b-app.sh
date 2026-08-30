#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
MODE="dual"
WITH_REASONER=0
WITH_VISION=0

usage() {
  printf '%s\n' \
    "Usage: $0 [--single] [--with-vision] [--with-reasoner]" \
    "Dual mode exposes only the Spark B application entry. Single mode is" \
    "explicitly labelled degraded and may optionally run local model profiles."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

cfg() {
  local key="$1"
  local fallback="${2-}"
  local direct="${!key-}"
  local value=""
  if [[ -n "$direct" ]]; then printf '%s' "$direct"; return; fi
  if [[ -f "$ENV_FILE" ]]; then
    value="$(awk -v wanted="$key" '$0 ~ "^[[:space:]]*" wanted "=" {sub("^[[:space:]]*" wanted "=", "", $0); found=$0} END {print found}' "$ENV_FILE")"
    value="${value%$'\r'}"
    [[ "$value" == \"*\" && "$value" == *\" ]] && value="${value:1:${#value}-2}"
    [[ "$value" == \'*\' && "$value" == *\' ]] && value="${value:1:${#value}-2}"
  fi
  printf '%s' "${value:-$fallback}"
}

while (($#)); do
  case "$1" in
    --single) MODE="single"; shift ;;
    --with-vision) WITH_VISION=1; shift ;;
    --with-reasoner) WITH_REASONER=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -f "$ENV_FILE" ]] || die "environment file is missing: ${ENV_FILE}; copy .env.example to .env and fill it"
offline_runtime="$(cfg OFFLINE_RUNTIME 1)"
[[ "$offline_runtime" == "1" ]] \
  || die "runtime deployment is cache-only; finish deploy/prefetch.sh and set OFFLINE_RUNTIME=1"

if [[ "$MODE" == "dual" && "$WITH_VISION" == "1" ]]; then
  die "--with-vision is only valid with --single; Spark A owns vision in dual mode"
fi

if [[ "$MODE" == "single" ]]; then
  compose_file="compose.single.yml"
  preflight_role="single"
  export RELICSCOPE_RUNTIME_MODE=single-degraded
  RELICSCOPE_NODE_ID="$(cfg SINGLE_NODE_ID spark-single)"
  export RELICSCOPE_NODE_ID
  export RELICSCOPE_COMPUTE_NODE_ID="$RELICSCOPE_NODE_ID"
  if [[ "$WITH_VISION" == "1" ]]; then
    export SINGLE_VISION_BASE_URL=http://vision:8000/v1
  else
    export SINGLE_VISION_BASE_URL=
  fi
  if [[ "$WITH_REASONER" == "1" ]]; then
    export SINGLE_REASONER_BASE_URL=http://reasoner:8000/v1
  else
    export SINGLE_REASONER_BASE_URL=
  fi
else
  compose_file="compose.yml"
  preflight_role="spark-b"
  export RELICSCOPE_RUNTIME_MODE=dual-node
  if [[ "$WITH_REASONER" == "1" ]]; then
    export REASONER_BASE_URL=http://reasoner:8000/v1
  fi
fi

preflight_args=(--role "$preflight_role")
[[ "$WITH_VISION" == "1" ]] && preflight_args+=(--require-vision)
[[ "$WITH_REASONER" == "1" ]] && preflight_args+=(--require-reasoner)
"${SCRIPT_DIR}/preflight.sh" "${preflight_args[@]}"

cd "$PROJECT_DIR"

compose_args=(--env-file "$ENV_FILE" -f "$compose_file")
services=(app)

if [[ "$WITH_VISION" == "1" ]]; then
  compose_args+=(--profile vision)
  services+=(vision)
fi
if [[ "$WITH_REASONER" == "1" ]]; then
  compose_args+=(--profile reasoner)
  services+=(reasoner)
fi

docker compose "${compose_args[@]}" up \
  --detach --no-build --pull never "${services[@]}"

printf 'RelicScope application started: mode=%s entry=http://%s:%s profiles=vision:%s,reasoner:%s\n' \
  "$RELICSCOPE_RUNTIME_MODE" "$(cfg APP_BIND_IP 127.0.0.1)" "$(cfg RELICSCOPE_PORT 8088)" \
  "$WITH_VISION" "$WITH_REASONER"
printf '%s\n' 'Browser traffic uses this single entry; model ports are not browser-facing.'
