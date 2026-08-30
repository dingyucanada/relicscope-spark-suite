#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"

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

absolute_path() { [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "$PROJECT_DIR" "$1"; }

[[ "$(cfg REASONER_ENABLED 0)" == "1" ]] \
  || die "reasoner is optional and disabled; set REASONER_ENABLED=1 to start it"

"${SCRIPT_DIR}/preflight.sh" --role spark-b --require-reasoner

container_name="relicscope-reasoner"
if docker container inspect "$container_name" >/dev/null 2>&1; then
  if [[ "$(docker container inspect --format '{{.State.Running}}' "$container_name")" == "true" ]]; then
    printf 'Reasoner service already running: %s\n' "$container_name"
  else
    docker container start "$container_name" >/dev/null
    printf 'Reasoner service restarted from existing container: %s\n' "$container_name"
  fi
  printf '%s\n' 'Configuration changes require an explicit stop/rollback before start.'
  exit 0
fi

bind_ip="$(cfg SPARK_B_BIND_IP '')"
[[ -n "$bind_ip" ]] || die "SPARK_B_BIND_IP must be a pinned private IP"
port="$(cfg REASONER_PORT 8002)"
image="$(cfg VLLM_IMAGE relicscope-multimodal-vllm:0.20.0-arm64)"
model="$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)"
node_id="$(cfg REASONER_NODE_ID "$(cfg RELICSCOPE_NODE_ID spark-b)")"
cache_dir="$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")"
vllm_cache_dir="$(absolute_path "$(cfg VLLM_CACHE_DIR ./runtime/vllm-cache)")"
secret_file="$(absolute_path "$(cfg SERVICE_API_KEY_FILE ./secrets/service_api_key)")"
offline="$(cfg OFFLINE_RUNTIME 1)"
[[ "$offline" == "1" ]] \
  || die "runtime deployment is cache-only; finish deploy/prefetch.sh and set OFFLINE_RUNTIME=1"

docker run -d \
  --name "$container_name" \
  --restart unless-stopped \
  --init \
  --gpus all \
  --ipc host \
  --shm-size 16g \
  --security-opt no-new-privileges:true \
  --log-opt max-size=20m \
  --log-opt max-file=3 \
  --publish "${bind_ip}:${port}:8000" \
  --volume "${cache_dir}:/root/.cache/huggingface:ro" \
  --volume "${vllm_cache_dir}:/root/.cache/vllm:rw" \
  --volume "${secret_file}:/run/secrets/service_api_key:ro" \
  --env "REASONER_MODEL=${model}" \
  --env "REASONER_MAX_MODEL_LEN=$(cfg REASONER_MAX_MODEL_LEN 8192)" \
  --env "REASONER_GPU_MEMORY_UTILIZATION=$(cfg REASONER_GPU_MEMORY_UTILIZATION 0.70)" \
  --env "HF_HUB_OFFLINE=$(cfg HF_HUB_OFFLINE 1)" \
  --env "TRANSFORMERS_OFFLINE=$(cfg TRANSFORMERS_OFFLINE 1)" \
  --env "HF_HUB_DISABLE_TELEMETRY=1" \
  --env "DO_NOT_TRACK=1" \
  --env "VLLM_LOGGING_LEVEL=WARNING" \
  --env "VLLM_SERVER_DEV_MODE=0" \
  --health-cmd 'python3 -c "import urllib.request; urllib.request.urlopen('\''http://127.0.0.1:8000/health'\'', timeout=3).read()" || exit 1' \
  --health-interval 20s \
  --health-timeout 5s \
  --health-start-period 15m \
  --health-retries 5 \
  --label "ai.relicscope.node=${node_id}" \
  --label ai.relicscope.role=optional-report-reasoner \
  --label ai.relicscope.runtime=independent-service-not-tp \
  --entrypoint /bin/bash \
  "$image" -ec '
    service_key="$(cat /run/secrets/service_api_key)"
    if [ "${#service_key}" -lt 32 ]; then
      echo "FATAL: service API key is missing or too short" >&2
      exit 78
    fi
    export VLLM_API_KEY="$service_key"
    exec vllm serve "$REASONER_MODEL" \
      --host 0.0.0.0 \
      --port 8000 \
      --max-model-len "$REASONER_MAX_MODEL_LEN" \
      --gpu-memory-utilization "$REASONER_GPU_MEMORY_UTILIZATION" \
      --max-num-seqs 2
  ' >/dev/null

printf 'Reasoner service started: node=%s role=optional-report-reasoner model=%s endpoint=http://%s:%s/v1 mode=independent-service-not-tp\n' \
  "$node_id" "$model" "$bind_ip" "$port"
