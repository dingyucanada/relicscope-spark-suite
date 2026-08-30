#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
OUTPUT="${PROJECT_DIR}/runtime/acceptance/single-spark-live.json"
BASE_URL=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cfg() {
  local key="$1" fallback="${2-}" value=""
  if [[ -n "${!key-}" ]]; then printf '%s' "${!key}"; return; fi
  if [[ -f "$ENV_FILE" ]]; then
    value="$(awk -v wanted="$key" '$0 ~ "^[[:space:]]*" wanted "=" {sub("^[[:space:]]*" wanted "=", "", $0); found=$0} END {print found}' "$ENV_FILE")"
  fi
  printf '%s' "${value:-$fallback}"
}

while (($#)); do
  case "$1" in
    --base-url) (($# >= 2)) || die "--base-url requires a value"; BASE_URL="$2"; shift 2 ;;
    --output) (($# >= 2)) || die "--output requires a path"; OUTPUT="$2"; shift 2 ;;
    -h|--help)
      printf '%s\n' "Usage: $0 [--base-url URL] [--output FILE]"
      exit 0
      ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -f "$ENV_FILE" ]] || die "environment file is missing: ${ENV_FILE}"
profile="$(cfg MODEL_PROFILE qwen3-vl)"
[[ "$profile" == "qwen3-vl" ]] \
  || die "formal single-Spark acceptance is Qwen3-VL baseline-only; use deploy/single-spark-ab.sh for the Nemotron candidate"
model="$(cfg VISION_MODEL qwen3_vl_30b_a3b)"
[[ "$OUTPUT" == /* ]] || OUTPUT="${PROJECT_DIR}/${OUTPUT}"
BASE_URL="${BASE_URL:-http://$(cfg APP_BIND_IP 127.0.0.1):$(cfg RELICSCOPE_PORT 8088)}"
vision_container_id="$({
  cd "$PROJECT_DIR"
  docker compose --env-file "$ENV_FILE" -f compose.single.yml ps -q vision
})"
[[ -n "$vision_container_id" ]] || die "single-Spark vision container is not running"

python3 "${PROJECT_DIR}/scripts/spark-live-acceptance.py" baseline \
  --base-url "$BASE_URL" \
  --profile "$profile" \
  --expected-model "$model" \
  --vision-container-id "$vision_container_id" \
  --output "$OUTPUT"

command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required"
(
  cd -- "$(dirname -- "$OUTPUT")"
  sha256sum --check "$(basename -- "$OUTPUT").sha256"
)
printf 'Formal Qwen3-VL acceptance evidence: %s\n' "$OUTPUT"
