#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
COMMAND="${1-}"
[[ -n "$COMMAND" ]] && shift
ROLE=""
NO_WAIT=0
WAIT_OVERRIDE=""

usage() {
  printf '%s\n' \
    "Usage: $0 install|preflight|start|stop|restart|health|status --role spark-a|spark-b|single|all [--no-wait|--wait SECONDS]" \
    "Use deploy/install.sh directly when install needs --service-key or --generate-key."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    --no-wait) NO_WAIT=1; shift ;;
    --wait)
      (($# >= 2)) || die "--wait requires seconds"
      WAIT_OVERRIDE="$2"
      [[ "$WAIT_OVERRIDE" =~ ^[0-9]+$ ]] || die "--wait must be a non-negative integer"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$COMMAND" in
  install|preflight|start|stop|restart|health|status) ;;
  *) usage; die "invalid command: ${COMMAND:-missing}" ;;
esac
case "$ROLE" in
  spark-a|spark-b|single|all) ;;
  *) die "invalid role: ${ROLE:-missing}" ;;
esac

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

container_exists() { docker container inspect "$1" >/dev/null 2>&1; }
container_running() {
  [[ "$(docker container inspect --format '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

start_container_or_create() {
  local name="$1"
  local script="$2"
  if container_exists "$name"; then
    if container_running "$name"; then
      printf 'Already running: %s\n' "$name"
    else
      docker container start "$name" >/dev/null
      printf 'Restarted existing container: %s\n' "$name"
    fi
  else
    "$script"
  fi
}

run_preflight() {
  if [[ "$ROLE" == "spark-a" || "$ROLE" == "spark-b" ]]; then
    "${SCRIPT_DIR}/network-preflight.sh" --role "$ROLE"
  elif [[ "$ROLE" == "all" ]]; then
    die "role=all cannot prove two physical links from one host; run preflight separately on Spark A and Spark B"
  fi
  "${SCRIPT_DIR}/preflight.sh" --role "$ROLE"
}

start_role() {
  run_preflight
  case "$ROLE" in
    spark-a)
      start_container_or_create relicscope-vision "${SCRIPT_DIR}/spark-a-vision.sh"
      if [[ "$(cfg EMBEDDING_ENABLED 0)" == "1" ]]; then
        start_container_or_create relicscope-embedding "${SCRIPT_DIR}/spark-a-embedding.sh"
      fi
      ;;
    spark-b)
      args=()
      [[ "$(cfg REASONER_ENABLED 0)" == "1" ]] && args+=(--with-reasoner)
      "${SCRIPT_DIR}/spark-b-app.sh" "${args[@]}"
      ;;
    single)
      args=(--single --model-profile "$(cfg MODEL_PROFILE qwen3-vl)")
      "${SCRIPT_DIR}/spark-b-app.sh" "${args[@]}"
      ;;
    all)
      die "start role=all is intentionally disabled; each physical Spark must start only its assigned role"
      ;;
  esac
  if [[ "$NO_WAIT" == "0" ]]; then
    "${SCRIPT_DIR}/healthcheck.sh" --role "$ROLE" --wait "${WAIT_OVERRIDE:-$(cfg HEALTH_WAIT_SECONDS 900)}"
  fi
}

case "$COMMAND" in
  install)
    "${SCRIPT_DIR}/install.sh" --role "$ROLE"
    ;;
  preflight)
    run_preflight
    ;;
  start)
    start_role
    ;;
  stop)
    "${SCRIPT_DIR}/rollback.sh" --role "$ROLE"
    ;;
  restart)
    "${SCRIPT_DIR}/rollback.sh" --role "$ROLE"
    start_role
    ;;
  health)
    "${SCRIPT_DIR}/healthcheck.sh" --role "$ROLE" --wait "${WAIT_OVERRIDE:-0}"
    ;;
  status)
    command -v docker >/dev/null 2>&1 || die "Docker is required"
    if [[ "$ROLE" == "spark-a" || "$ROLE" == "all" ]]; then
      docker container ls --all --filter name='^/relicscope-vision$' --filter name='^/relicscope-embedding$' \
        --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
    fi
    if [[ "$ROLE" == "spark-b" || "$ROLE" == "all" ]]; then
      (cd "$PROJECT_DIR" && docker compose --env-file "$ENV_FILE" -f compose.yml --profile reasoner ps)
    fi
    if [[ "$ROLE" == "single" ]]; then
      (cd "$PROJECT_DIR" && docker compose --env-file "$ENV_FILE" -f compose.single.yml ps)
    fi
    ;;
esac
