#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
ROLE="all"

usage() {
  printf '%s\n' \
    "Usage: $0 [--role spark-a|spark-b|single|all]" \
    "Network access requires ALLOW_NETWORK_DOWNLOADS=YES. Any selected model" \
    "also requires ACCEPT_MODEL_TERMS=YES after reviewing its model card."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$ROLE" in
  spark-a|spark-b|single|all) ;;
  *) die "invalid role: ${ROLE}" ;;
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

set_env_value() {
  local key="$1"
  local value="$2"
  local temporary=""
  temporary="$(mktemp "${ENV_FILE}.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    $0 ~ "^[[:space:]]*" key "=" {
      if (!found) print key "=" value
      found=1
      next
    }
    { print }
    END { if (!found) print key "=" value }
  ' "$ENV_FILE" >"$temporary"
  chmod 600 "$temporary"
  mv -f -- "$temporary" "$ENV_FILE"
}

absolute_path() {
  [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "$PROJECT_DIR" "$1"
}

safe_managed_dir() {
  python3 - "$1" "$PROJECT_DIR" <<'PY'
import os
import sys

value, project = sys.argv[1:3]
path = os.path.realpath(value)
forbidden = {
    "/", "/home", "/opt", "/srv", "/tmp", "/usr", "/var",
    os.path.realpath(os.path.expanduser("~")), os.path.realpath(project),
}
if path in forbidden:
    raise SystemExit(f"refusing broad managed-directory path: {path}")
print(path)
PY
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_pinned_image() {
  local image="$1"
  [[ "$image" == *@sha256:* || ( "$image" == *:* && "$image" != *:latest ) ]] \
    || die "image must use an immutable digest or a non-latest fixed tag: ${image}"
}

download_model() {
  local model_id="$1"
  printf 'Prefetching model: %s\n' "$model_id"
  docker run --rm \
    --network bridge \
    --security-opt no-new-privileges:true \
    --volume "${hf_cache_dir}:/root/.cache/huggingface:rw" \
    --volume "${hf_token_file}:/run/secrets/hf_token:ro" \
    --env "MODEL_ID=${model_id}" \
    --env HF_HUB_DISABLE_TELEMETRY=1 \
    --env DO_NOT_TRACK=1 \
    --entrypoint /bin/bash \
    "$vllm_image" -ec '
      token="$(cat /run/secrets/hf_token)"
      if [ "${#token}" -lt 8 ]; then
        echo "FATAL: Hugging Face token is missing or too short" >&2
        exit 78
      fi
      export HF_TOKEN="$token"
      exec hf download "$MODEL_ID"
    '
}

cached_model_revision() {
  local model_id="$1"
  local model_cache="models--${model_id//\//--}"
  local candidate=""
  for candidate in "${hf_cache_dir}/hub/${model_cache}" "${hf_cache_dir}/${model_cache}"; do
    if [[ -f "${candidate}/refs/main" ]]; then
      tr -d '\r\n' <"${candidate}/refs/main"
      return
    fi
  done
  printf '%s' unknown
}

[[ -f "$ENV_FILE" ]] || die "environment file is missing: ${ENV_FILE}"
[[ "$(cfg ALLOW_NETWORK_DOWNLOADS NO)" == "YES" ]] \
  || die "network preparation is locked; set ALLOW_NETWORK_DOWNLOADS=YES only for the approved prefetch window"

require_command docker
require_command awk
require_command python3
require_command git

[[ "$(uname -s)" == "Linux" ]] || die "prefetch must run on the target Linux DGX Spark"
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) die "target must be ARM64; detected $(uname -m)" ;;
esac
docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"
deployment_git_commit="$(git -C "$PROJECT_DIR" rev-parse HEAD)"
[[ "$deployment_git_commit" =~ ^[0-9A-Fa-f]{40,64}$ ]] \
  || die "unable to resolve an immutable source commit"
[[ -z "$(git -C "$PROJECT_DIR" status --porcelain)" ]] \
  || die "tracked source files are dirty; commit or restore them before building deployment images"

app_image="$(cfg APP_IMAGE relicscope-ai-demo:1.2.0-arm64)"
python_image="$(cfg PYTHON_IMAGE python:3.12.11-slim-bookworm)"
pypi_index_url="$(cfg PYPI_INDEX_URL https://pypi.org/simple)"
vllm_base_image="$(cfg VLLM_BASE_IMAGE vllm/vllm-openai:v0.20.0)"
vllm_image="$(cfg VLLM_IMAGE relicscope-multimodal-vllm:0.20.0-arm64)"
reference_embedding_image="$(cfg REFERENCE_EMBEDDING_IMAGE relicscope-reference-embedding:1.0.0-arm64)"
app_uid="$(cfg APP_UID "$(id -u)")"
app_gid="$(cfg APP_GID "$(id -g)")"
hf_cache_dir="$(safe_managed_dir "$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")")"
vllm_cache_dir="$(safe_managed_dir "$(absolute_path "$(cfg VLLM_CACHE_DIR ./runtime/vllm-cache)")")"
hf_token_file="$(absolute_path "$(cfg HF_TOKEN_FILE ./secrets/hf_token)")"
manifest_dir="$(safe_managed_dir "${PROJECT_DIR}/runtime")"
manifest_file="${manifest_dir}/prefetch-manifest-${ROLE}.txt"

require_pinned_image "$app_image"
require_pinned_image "$python_image"
require_pinned_image "$vllm_base_image"
require_pinned_image "$vllm_image"
require_pinned_image "$reference_embedding_image"
[[ "$pypi_index_url" == https://* ]] \
  || die "PYPI_INDEX_URL must use HTTPS"
[[ "$pypi_index_url" != *"@"* ]] \
  || die "PYPI_INDEX_URL must not contain embedded credentials"

need_app=0
need_vision=0
need_embedding=0
need_reference_embedding=0
need_reasoner=0
case "$ROLE" in
  spark-a)
    need_vision=1
    need_embedding="$(cfg PREFETCH_EMBEDDING 0)"
    ;;
  spark-b)
    need_app=1
    need_reasoner="$(cfg PREFETCH_REASONER 0)"
    ;;
  single|all)
    need_app=1
    need_vision=1
    need_embedding="$(cfg PREFETCH_EMBEDDING 0)"
    need_reference_embedding="$(cfg PREFETCH_REFERENCE_EMBEDDING 1)"
    need_reasoner="$(cfg PREFETCH_REASONER 0)"
    ;;
esac
need_vllm=0
[[ "$need_vision" == "1" || "$need_embedding" == "1" || "$need_reference_embedding" == "1" || "$need_reasoner" == "1" ]] && need_vllm=1

[[ "$app_uid" =~ ^[0-9]+$ && "$app_gid" =~ ^[0-9]+$ ]] \
  || die "APP_UID and APP_GID must be numeric"
((app_uid > 0 && app_gid > 0)) \
  || die "APP_UID and APP_GID must be non-root values"
if [[ "$need_app" == "1" ]]; then
  [[ "$app_uid" == "$(id -u)" && "$app_gid" == "$(id -g)" ]] \
    || die "APP_UID/APP_GID must match the user running prefetch ($(id -u):$(id -g)) so the non-root app can write the persistent bind directory"
fi

if [[ "$need_vllm" == "1" ]]; then
  [[ "$(cfg ACCEPT_MODEL_TERMS NO)" == "YES" ]] \
    || die "model terms were not acknowledged; review every selected model card, then set ACCEPT_MODEL_TERMS=YES"
  [[ -f "$hf_token_file" ]] || die "Hugging Face token file is missing: ${hf_token_file}"
  python3 - "$hf_token_file" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
if len(open(path, "rb").read().strip()) < 8:
    raise SystemExit("Hugging Face token file is empty or too short")
mode = stat.S_IMODE(os.stat(path).st_mode)
if mode & 0o077:
    raise SystemExit(f"Hugging Face token permissions are too broad: {mode:03o}; run chmod 600")
PY
fi

mkdir -p -- "$hf_cache_dir" "$vllm_cache_dir" "$manifest_dir"
chmod 700 "$hf_cache_dir" "$vllm_cache_dir" "$manifest_dir"

if [[ "$need_vllm" == "1" ]]; then
  printf 'Pulling fixed vLLM base image: %s\n' "$vllm_base_image"
  docker pull "$vllm_base_image"
  printf 'Building offline multimodal runtime: %s\n' "$vllm_image"
  docker build \
    --platform linux/arm64 \
    --pull=false \
    --file "${PROJECT_DIR}/Dockerfile.vllm" \
    --build-arg "VLLM_BASE_IMAGE=${vllm_base_image}" \
    --build-arg "PYPI_INDEX_URL=${pypi_index_url}" \
    --build-arg "RELICSCOPE_GIT_COMMIT=${deployment_git_commit}" \
    --tag "$vllm_image" \
    "$PROJECT_DIR"
fi

if [[ "$need_reference_embedding" == "1" ]]; then
  printf 'Building private reference-embedding runtime: %s\n' "$reference_embedding_image"
  docker build \
    --platform linux/arm64 \
    --pull=false \
    --file "${PROJECT_DIR}/Dockerfile.embedding" \
    --build-arg "EMBEDDING_BASE_IMAGE=${vllm_base_image}" \
    --build-arg "PYPI_INDEX_URL=${pypi_index_url}" \
    --build-arg "RELICSCOPE_GIT_COMMIT=${deployment_git_commit}" \
    --tag "$reference_embedding_image" \
    "$PROJECT_DIR"
fi

if [[ "$need_app" == "1" ]]; then
  printf 'Pulling fixed ARM64 Python base: %s\n' "$python_image"
  docker pull "$python_image"
  printf 'Building fixed ARM64 application image: %s\n' "$app_image"
  docker build \
    --platform linux/arm64 \
    --pull=false \
    --build-arg "PYTHON_IMAGE=${python_image}" \
    --build-arg "PYPI_INDEX_URL=${pypi_index_url}" \
    --build-arg "APP_UID=${app_uid}" \
    --build-arg "APP_GID=${app_gid}" \
    --build-arg "RELICSCOPE_GIT_COMMIT=${deployment_git_commit}" \
    --tag "$app_image" \
    "$PROJECT_DIR"
fi

models=()
if [[ "$need_vision" == "1" ]]; then
  models+=("$(cfg VISION_MODEL_SOURCE Qwen/Qwen3-VL-30B-A3B-Instruct)")
  if [[ "$ROLE" == "single" || "$ROLE" == "all" ]] \
      && [[ "$(cfg PREFETCH_AB_MODELS 1)" == "1" ]]; then
    models+=("$(cfg AB_NEMOTRON_MODEL_SOURCE nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4)")
  fi
fi
if [[ "$need_embedding" == "1" ]]; then
  models+=("$(cfg EMBEDDING_MODEL Qwen/Qwen3-VL-Embedding-2B)")
fi
if [[ "$need_reference_embedding" == "1" ]]; then
  models+=("$(cfg REFERENCE_EMBEDDING_MODEL_SOURCE Qwen/Qwen3-VL-Embedding-2B)")
fi
if [[ "$need_reasoner" == "1" ]]; then
  models+=("$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)")
fi
for model_id in "${models[@]}"; do
  download_model "$model_id"
done

if [[ "$need_reference_embedding" == "1" ]]; then
  reference_embedding_revision="$(cached_model_revision "$(cfg REFERENCE_EMBEDDING_MODEL_SOURCE Qwen/Qwen3-VL-Embedding-2B)")"
  [[ "$reference_embedding_revision" =~ ^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$ ]] \
    || die "downloaded reference embedding cache does not expose an immutable revision"
  set_env_value REFERENCE_EMBEDDING_MODEL_REVISION "${reference_embedding_revision,,}"
fi

tmp_manifest="$(mktemp "${manifest_dir}/prefetch.XXXXXX")"
{
  printf 'generated_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'role=%s\n' "$ROLE"
  printf 'architecture=%s\n' "$(uname -m)"
  printf 'deployment_git_commit=%s\n' "$deployment_git_commit"
  printf 'app_uid=%s\n' "$app_uid"
  printf 'app_gid=%s\n' "$app_gid"
  printf 'app_image=%s\n' "$app_image"
  if [[ "$need_app" == "1" ]]; then
    printf 'app_image_id=%s\n' "$(docker image inspect --format '{{.Id}}' "$app_image")"
  fi
  printf 'vllm_image=%s\n' "$vllm_image"
  printf 'vllm_base_image=%s\n' "$vllm_base_image"
  if [[ "$need_vllm" == "1" ]]; then
    printf 'vllm_image_id=%s\n' "$(docker image inspect --format '{{.Id}}' "$vllm_image")"
  fi
  printf 'reference_embedding_image=%s\n' "$reference_embedding_image"
  if [[ "$need_reference_embedding" == "1" ]]; then
    printf 'reference_embedding_image_id=%s\n' "$(docker image inspect --format '{{.Id}}' "$reference_embedding_image")"
    printf 'reference_embedding_model_revision=%s\n' \
      "$reference_embedding_revision"
  fi
  for model_id in "${models[@]}"; do
    printf 'model=%s\n' "$model_id"
    printf 'model_revision=%s@%s\n' "$model_id" "$(cached_model_revision "$model_id")"
  done
  printf 'contains_secrets=false\n'
} >"$tmp_manifest"
mv -f -- "$tmp_manifest" "$manifest_file"
chmod 600 "$manifest_file"

printf 'Prefetch complete for role=%s. Manifest: %s\n' "$ROLE" "$manifest_file"
printf '%s\n' 'Before runtime: set ALLOW_NETWORK_DOWNLOADS=NO, OFFLINE_RUNTIME=1, HF_HUB_OFFLINE=1, and TRANSFORMERS_OFFLINE=1.'
