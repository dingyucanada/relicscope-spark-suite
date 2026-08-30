#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_DIR="${OUTPUT_DIR:-${PROJECT_DIR}/runtime/model-ab/${run_stamp}}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cfg() {
  local key="$1" fallback="${2-}" value=""
  if [[ -n "${!key-}" ]]; then printf '%s' "${!key}"; return; fi
  if [[ -f "$ENV_FILE" ]]; then
    value="$(awk -v wanted="$key" '$0 ~ "^[[:space:]]*" wanted "=" {sub("^[[:space:]]*" wanted "=", "", $0); found=$0} END {print found}' "$ENV_FILE")"
  fi
  printf '%s' "${value:-$fallback}"
}

[[ -f "$ENV_FILE" ]] || die "environment file is missing: ${ENV_FILE}"
mkdir -p -- "$OUTPUT_DIR"
chmod 700 "$OUTPUT_DIR"
base_url="http://$(cfg APP_BIND_IP 127.0.0.1):$(cfg RELICSCOPE_PORT 8088)"
wait_seconds="$(cfg HEALTH_WAIT_SECONDS 1200)"
baseline_file="${OUTPUT_DIR}/qwen3-vl-baseline.json"
candidate_file="${OUTPUT_DIR}/nemotron-omni-candidate.json"
final_file="${OUTPUT_DIR}/qwen3-vl-final-report.json"
scorecard_file="${OUTPUT_DIR}/model-ab-scorecard.json"
RESTORE_QWEN_ON_EXIT=0
VISION_CONTAINER_ID=""

select_profile() {
  case "$1" in
    qwen3-vl)
      export MODEL_PROFILE=qwen3-vl
      export VISION_MODEL_SOURCE="$(cfg VISION_MODEL_SOURCE Qwen/Qwen3-VL-30B-A3B-Instruct)"
      export VISION_MODEL="$(cfg VISION_MODEL qwen3_vl_30b_a3b)"
      export VISION_MAX_MODEL_LEN="$(cfg VISION_MAX_MODEL_LEN 8192)"
      export VISION_GPU_MEMORY_UTILIZATION="$(cfg VISION_GPU_MEMORY_UTILIZATION 0.75)"
      ;;
    nemotron-omni)
      export MODEL_PROFILE=nemotron-omni
      export VISION_MODEL_SOURCE="$(cfg AB_NEMOTRON_MODEL_SOURCE nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4)"
      export VISION_MODEL="$(cfg AB_NEMOTRON_MODEL nemotron_3_nano_omni)"
      export VISION_MAX_MODEL_LEN="$(cfg AB_NEMOTRON_MAX_MODEL_LEN 32768)"
      export VISION_GPU_MEMORY_UTILIZATION="$(cfg AB_NEMOTRON_GPU_MEMORY_UTILIZATION 0.70)"
      ;;
    *) die "unsupported profile: $1" ;;
  esac
  export SINGLE_VISION_BASE_URL=http://vision:8000/v1
  export SINGLE_REASONER_BASE_URL=http://vision:8000/v1
  export REASONER_MODEL="$VISION_MODEL"
}

start_profile() {
  RESTORE_QWEN_ON_EXIT=1
  select_profile "$1"
  "${SCRIPT_DIR}/rollback.sh" --role single
  "${SCRIPT_DIR}/spark-b-app.sh" --single --model-profile "$MODEL_PROFILE"
  "${SCRIPT_DIR}/healthcheck.sh" --role single --wait "$wait_seconds"
  VISION_CONTAINER_ID="$({
    cd "$PROJECT_DIR"
    docker compose --env-file "$ENV_FILE" -f compose.single.yml ps -q vision
  })"
  [[ -n "$VISION_CONTAINER_ID" ]] || die "single-Spark vision container is not running"
  if [[ "$MODEL_PROFILE" == "qwen3-vl" ]]; then
    RESTORE_QWEN_ON_EXIT=0
  fi
}

restore_qwen_on_exit() {
  local original_status=$?
  local restore_status=0
  trap - EXIT
  if [[ "$RESTORE_QWEN_ON_EXIT" == "1" ]]; then
    printf '%s\n' 'A/B run interrupted; restoring the Qwen3-VL baseline before exit.' >&2
    set +e
    select_profile qwen3-vl
    "${SCRIPT_DIR}/rollback.sh" --role single
    "${SCRIPT_DIR}/spark-b-app.sh" --single --model-profile qwen3-vl
    "${SCRIPT_DIR}/healthcheck.sh" --role single --wait "$wait_seconds"
    restore_status=$?
    set -e
    if [[ "$restore_status" != "0" ]]; then
      printf '%s\n' 'WARNING: automatic Qwen baseline restoration failed; inspect container status.' >&2
    fi
  fi
  exit "$original_status"
}
trap restore_qwen_on_exit EXIT

printf '%s\n' 'Phase 1/4: Qwen3-VL Chinese ceramic baseline'
start_profile qwen3-vl
python3 "${PROJECT_DIR}/scripts/spark-live-acceptance.py" baseline \
  --base-url "$base_url" --profile "$MODEL_PROFILE" \
  --expected-model "$VISION_MODEL" --vision-container-id "$VISION_CONTAINER_ID" \
  --output "$baseline_file"
session_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["session_id"])' "$baseline_file")"
video_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["video_id"])' "$baseline_file")"

printf '%s\n' 'Phase 2/4: Nemotron Omni native-video candidate on the identical stored input'
start_profile nemotron-omni
python3 "${PROJECT_DIR}/scripts/spark-live-acceptance.py" candidate \
  --base-url "$base_url" --profile "$MODEL_PROFILE" \
  --expected-model "$VISION_MODEL" --session-id "$session_id" \
  --video-id "$video_id" --vision-container-id "$VISION_CONTAINER_ID" \
  --output "$candidate_file"

printf '%s\n' 'Phase 3/4: restore Qwen baseline and generate a report containing both runs'
start_profile qwen3-vl
python3 "${PROJECT_DIR}/scripts/spark-live-acceptance.py" finalize \
  --base-url "$base_url" --profile "$MODEL_PROFILE" \
  --expected-model "$VISION_MODEL" --session-id "$session_id" \
  --vision-container-id "$VISION_CONTAINER_ID" --output "$final_file"

printf '%s\n' 'Phase 4/4: generate a non-automatic promotion scorecard'
python3 "${PROJECT_DIR}/scripts/compare-model-runs.py" \
  --baseline "$baseline_file" --candidate "$candidate_file" \
  --output "$scorecard_file"

python3 - "$OUTPUT_DIR" <<'PY'
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
lines = []
for path in sorted(root.glob("*.json")):
    lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
(root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
printf 'A/B evidence package: %s\n' "$OUTPUT_DIR"
printf '%s\n' 'Qwen3-VL has been restored. Promotion remains blocked until expert review is recorded.'
