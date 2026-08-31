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
MODEL_PROFILE_OVERRIDE=""

usage() {
  printf '%s\n' \
    "Usage: $0 [--single] [--model-profile qwen3-vl|nemotron-omni] [--with-vision] [--with-reasoner]" \
    "Dual mode exposes only the Spark B application entry. Single mode always" \
    "runs exactly one shared local GPU model for observation and report summary."
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

absolute_path() { [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "$PROJECT_DIR" "$1"; }

cached_model_revision() {
  local model_id="$1" cache_dir cache_name root revision=""
  cache_dir="$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")"
  cache_name="models--${model_id//\//--}"
  for root in "${cache_dir}/hub/${cache_name}" "${cache_dir}/${cache_name}"; do
    if [[ -f "${root}/refs/main" ]]; then
      revision="$(tr -d '\r\n' <"${root}/refs/main")"
      break
    fi
  done
  [[ "$revision" =~ ^[0-9A-Fa-f]{7,64}$ ]] \
    || die "cached model revision is missing or invalid for ${model_id}; rerun prefetch"
  printf '%s' "$revision"
}

while (($#)); do
  case "$1" in
    --single) MODE="single"; shift ;;
    --with-vision) WITH_VISION=1; shift ;;
    --with-reasoner) WITH_REASONER=1; shift ;;
    --model-profile)
      (($# >= 2)) || die "--model-profile requires a value"
      MODEL_PROFILE_OVERRIDE="$2"
      shift 2
      ;;
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
  export RELICSCOPE_RUNTIME_MODE=single-spark
  RELICSCOPE_NODE_ID="$(cfg SINGLE_NODE_ID spark-single)"
  export RELICSCOPE_NODE_ID
  export RELICSCOPE_COMPUTE_NODE_ID="$RELICSCOPE_NODE_ID"
  WITH_VISION=1
  WITH_REASONER=0

  model_profile="${MODEL_PROFILE_OVERRIDE:-$(cfg MODEL_PROFILE qwen3-vl)}"
  case "$model_profile" in
    qwen3-vl)
      export VISION_MODEL_SOURCE="$(cfg VISION_MODEL_SOURCE Qwen/Qwen3-VL-30B-A3B-Instruct)"
      export VISION_MODEL="$(cfg VISION_MODEL qwen3_vl_30b_a3b)"
      export VISION_MAX_MODEL_LEN="$(cfg VISION_MAX_MODEL_LEN 8192)"
      export VISION_GPU_MEMORY_UTILIZATION="$(cfg VISION_GPU_MEMORY_UTILIZATION 0.75)"
      ;;
    nemotron-omni)
      export VISION_MODEL_SOURCE="$(cfg AB_NEMOTRON_MODEL_SOURCE nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4)"
      export VISION_MODEL="$(cfg AB_NEMOTRON_MODEL nemotron_3_nano_omni)"
      export VISION_MAX_MODEL_LEN="$(cfg AB_NEMOTRON_MAX_MODEL_LEN 32768)"
      export VISION_GPU_MEMORY_UTILIZATION="$(cfg AB_NEMOTRON_GPU_MEMORY_UTILIZATION 0.70)"
      ;;
    *) die "unsupported single-Spark model profile: ${model_profile}" ;;
  esac
  export MODEL_PROFILE="$model_profile"
  export VISION_MODEL_REVISION="$(cached_model_revision "$VISION_MODEL_SOURCE")"
  export SINGLE_VISION_BASE_URL=http://vision:8000/v1
  export SINGLE_REASONER_BASE_URL=http://vision:8000/v1
  export REASONER_MODEL="$VISION_MODEL"
  export RELICSCOPE_REFERENCE_LIBRARY_ENABLED="$(cfg RELICSCOPE_REFERENCE_LIBRARY_ENABLED true)"
  export REFERENCE_EMBEDDING_MODEL_SOURCE="$(cfg REFERENCE_EMBEDDING_MODEL_SOURCE Qwen/Qwen3-VL-Embedding-2B)"
  export REFERENCE_EMBEDDING_MODEL="$(cfg REFERENCE_EMBEDDING_MODEL qwen3_vl_embedding_2b)"
  export REFERENCE_EMBEDDING_MODEL_REVISION="$(cached_model_revision "$REFERENCE_EMBEDDING_MODEL_SOURCE")"
  [[ "$REFERENCE_EMBEDDING_MODEL_REVISION" =~ ^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$ ]] \
    || die "reference embedding model revision must be an immutable 40- or 64-hex commit"
  export REFERENCE_EMBEDDING_DIMENSION="$(cfg REFERENCE_EMBEDDING_DIMENSION 2048)"
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
[[ "$MODE" == "single" ]] && preflight_args+=(--require-reference-embedding)
[[ "$WITH_REASONER" == "1" ]] && preflight_args+=(--require-reasoner)
"${SCRIPT_DIR}/preflight.sh" "${preflight_args[@]}"

cd "$PROJECT_DIR"
compose_args=(--env-file "$ENV_FILE" -f "$compose_file")
services=(app)
if [[ "$MODE" == "single" ]]; then
  services=(vision reference-embedding app)
elif [[ "$WITH_REASONER" == "1" ]]; then
  compose_args+=(--profile reasoner)
  services+=(reasoner)
fi

docker compose "${compose_args[@]}" up \
  --detach --no-build --pull never "${services[@]}"

printf 'RelicScope application started: mode=%s entry=http://%s:%s vision=%s reasoner=%s\n' \
  "$RELICSCOPE_RUNTIME_MODE" "$(cfg APP_BIND_IP 127.0.0.1)" "$(cfg RELICSCOPE_PORT 8088)" \
  "$WITH_VISION" "$WITH_REASONER"
printf '%s\n' 'Browser traffic uses this single entry; model ports are not browser-facing.'
if [[ "$MODE" == "single" ]]; then
  printf 'Single-Spark model profile: %s (%s@%s; served=%s)\n' \
    "$MODEL_PROFILE" "$VISION_MODEL_SOURCE" "$VISION_MODEL_REVISION" "$VISION_MODEL"
  printf 'Reference embedding: %s@%s (served=%s; private sidecar)\n' \
    "$REFERENCE_EMBEDDING_MODEL_SOURCE" "$REFERENCE_EMBEDDING_MODEL_REVISION" "$REFERENCE_EMBEDDING_MODEL"
  printf '%s\n' 'The shared VLM performs observation/report summarization; the separate 2B sidecar performs only catalog retrieval embeddings.'
fi
