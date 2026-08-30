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

[[ "$(cfg EMBEDDING_ENABLED 0)" == "1" ]] \
  || die "embedding service is disabled; set EMBEDDING_ENABLED=1 after selecting a validated embedding model"

"${SCRIPT_DIR}/preflight.sh" --role spark-a --require-embedding

container_name="relicscope-embedding"
if docker container inspect "$container_name" >/dev/null 2>&1; then
  if [[ "$(docker container inspect --format '{{.State.Running}}' "$container_name")" == "true" ]]; then
    printf 'Embedding service already running: %s\n' "$container_name"
  else
    docker container start "$container_name" >/dev/null
    printf 'Embedding service restarted from existing container: %s\n' "$container_name"
  fi
  printf '%s\n' 'Configuration changes require an explicit stop/rollback before start.'
  exit 0
fi

bind_ip="$(cfg SPARK_A_BIND_IP '')"
[[ -n "$bind_ip" ]] || die "SPARK_A_BIND_IP must be a pinned private IP"
port="$(cfg EMBEDDING_PORT 8003)"
image="$(cfg VLLM_IMAGE nvcr.io/nvidia/vllm:26.05.post1-py3)"
model="$(cfg EMBEDDING_MODEL Qwen/Qwen3-VL-Embedding-2B)"
node_id="$(cfg EMBEDDING_NODE_ID "$(cfg RELICSCOPE_COMPUTE_NODE_ID spark-a)")"
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
  --shm-size 8g \
  --security-opt no-new-privileges:true \
  --log-opt max-size=20m \
  --log-opt max-file=3 \
  --publish "${bind_ip}:${port}:8000" \
  --volume "${cache_dir}:/root/.cache/huggingface:ro" \
  --volume "${vllm_cache_dir}:/root/.cache/vllm:rw" \
  --volume "${secret_file}:/run/secrets/service_api_key:ro" \
  --env "EMBEDDING_MODEL=${model}" \
  --env "EMBEDDING_GPU_MEMORY_UTILIZATION=$(cfg EMBEDDING_GPU_MEMORY_UTILIZATION 0.15)" \
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
  --label ai.relicscope.role=replaceable-embedding \
  --label ai.relicscope.runtime=independent-service-not-tp \
  --entrypoint /bin/bash \
  "$image" -ec '
    service_key="$(cat /run/secrets/service_api_key)"
    if [ "${#service_key}" -lt 32 ]; then
      echo "FATAL: service API key is missing or too short" >&2
      exit 78
    fi
    export VLLM_API_KEY="$service_key"
    exec vllm serve "$EMBEDDING_MODEL" \
      --host 0.0.0.0 \
      --port 8000 \
      --runner pooling \
      --gpu-memory-utilization "$EMBEDDING_GPU_MEMORY_UTILIZATION" \
      --max-num-seqs 4
  ' >/dev/null

printf 'Embedding service started: node=%s role=replaceable-embedding model=%s endpoint=http://%s:%s/v1 mode=independent-service-not-tp\n' \
  "$node_id" "$model" "$bind_ip" "$port"
