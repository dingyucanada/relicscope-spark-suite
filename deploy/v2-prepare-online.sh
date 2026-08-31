#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_ENV_FILE:-${project_root}/.env.v2}"
allow_network=0
hf_token_file=""

usage() {
  printf '%s\n' \
    'Usage: ./deploy/v2-prepare-online.sh --allow-network [--hf-token-file FILE]' \
    'Pulls the pinned runtime images, builds the gateway, and caches exactly the' \
    'configured model revision. Runtime remains offline after preparation.'
}

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --allow-network) allow_network=1; shift ;;
    --hf-token-file)
      (($# >= 2)) || fail "--hf-token-file requires a path"
      hf_token_file="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done
[[ "${allow_network}" == "1" ]] || fail "network preparation requires the explicit --allow-network flag"
[[ -f "${env_file}" ]] || fail "run deploy/v2-install.sh and review .env.v2 first"
if [[ -n "${hf_token_file}" ]]; then
  [[ -f "${hf_token_file}" && -s "${hf_token_file}" ]] || fail "Hugging Face token file is missing or empty"
fi
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) fail "online preparation must run on the ARM64 DGX Spark" ;;
esac
for command_name in awk docker git install mktemp python3; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
[[ -z "$(git -C "${project_root}" status --porcelain --untracked-files=all)" ]] \
  || fail "online preparation requires a clean checked-out source tree"

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

set_env_value() {
  local key="$1"
  local value="$2"
  local temporary=""
  temporary="$(mktemp "${env_file}.XXXXXX")"
  awk -v wanted="${key}" -v replacement="${value}" '
    BEGIN { found=0 }
    $0 ~ "^[[:space:]]*" wanted "=" {
      if (!found) print wanted "=" replacement
      found=1
      next
    }
    { print }
    END { if (!found) print wanted "=" replacement }
  ' "${env_file}" >"${temporary}"
  chmod 600 "${temporary}"
  mv -f -- "${temporary}" "${env_file}"
}

absolute_path() {
  [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "${project_root}" "$1"
}

validate_managed_paths() {
  python3 "${project_root}/deploy/validate-v2-managed-paths.py" \
    --project-root "${project_root}" "$@"
}

model="$(cfg VISION_MODEL Qwen/Qwen3-VL-30B-A3B-Instruct)"
model_source="$(cfg VISION_MODEL_SOURCE Qwen/Qwen3-VL-30B-A3B-Instruct)"
revision="$(cfg VISION_MODEL_REVISION)"
max_model_len="$(cfg VISION_MAX_MODEL_LEN 16384)"
gpu_memory_utilization="$(cfg VISION_GPU_MEMORY_UTILIZATION 0.72)"
vllm_image="$(cfg VLLM_IMAGE nvcr.io/nvidia/vllm:26.05.post1-py3)"
caddy_image="$(cfg CADDY_IMAGE caddy:2.10.2-alpine)"
python_image="$(cfg PYTHON_IMAGE python:3.12.11-slim-bookworm)"
hf_cache="$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")"
data_dir="$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/v2-data)")"
vllm_cache="$(absolute_path "$(cfg VLLM_CACHE_DIR ./runtime/vllm-cache)")"
caddy_data="$(absolute_path "$(cfg CADDY_DATA_DIR ./runtime/caddy/data)")"
caddy_config="$(absolute_path "$(cfg CADDY_CONFIG_DIR ./runtime/caddy/config)")"
secret_file="$(absolute_path "$(cfg SERVICE_API_KEY_FILE ./secrets/service_api_key)")"
validate_managed_paths \
  "${data_dir}" "${hf_cache}" "${vllm_cache}" \
  "${caddy_data}" "${caddy_config}" "${secret_file}"
for managed_dir in \
  "${data_dir}" "${hf_cache}" "${vllm_cache}" \
  "${caddy_data}" "${caddy_config}"; do
  [[ -d "${managed_dir}" && ! -L "${managed_dir}" ]] \
    || fail "managed directory is unavailable or is a symlink: ${managed_dir}"
done
[[ -f "${secret_file}" && ! -L "${secret_file}" && -s "${secret_file}" ]] \
  || fail "service API key must be a non-symlink regular file"
[[ -n "${model}" && "${model}" != *[[:space:]]* ]] \
  || fail "VISION_MODEL must be a non-empty model identity without whitespace"
python3 - "${max_model_len}" "${gpu_memory_utilization}" <<'PY'
import math
import sys

max_len = int(sys.argv[1])
memory = float(sys.argv[2])
if not 4096 <= max_len <= 131072:
    raise SystemExit("VISION_MAX_MODEL_LEN must be between 4096 and 131072")
if not math.isfinite(memory) or not 0.10 <= memory <= 0.95:
    raise SystemExit("VISION_GPU_MEMORY_UTILIZATION must be between 0.10 and 0.95")
PY
[[ "${revision}" =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]] || fail "VISION_MODEL_REVISION must be an immutable commit"
revision="${revision,,}"
set_env_value VISION_MODEL_REVISION "${revision}"
[[ "${model_source}" == "${model}" ]] \
  || fail "VISION_MODEL_SOURCE must equal the model identity loaded by V2 vLLM"
[[ "$(cfg RELICSCOPE_GIT_COMMIT)" == "$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}')" ]] \
  || fail "RELICSCOPE_GIT_COMMIT does not match the checked-out source"
install -d -m 700 -- "${hf_cache}" "${project_root}/runtime/preparation"

pin_registry_image() {
  local requested="$1"
  local digests=""
  local selected=""
  docker pull "${requested}" >&2
  if [[ "${requested}" =~ @sha256:[0-9a-fA-F]{64}$ ]]; then
    printf '%s' "${requested}"
    return
  fi
  digests="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${requested}")"
  selected="${digests%%$'\n'*}"
  [[ "${selected}" =~ @sha256:[0-9a-fA-F]{64}$ ]] \
    || fail "registry did not return an immutable digest for ${requested}"
  printf '%s' "${selected}"
}

printf 'Pulling and pinning the configured Python, NVIDIA vLLM and HTTPS ingress images...\n'
python_image="$(pin_registry_image "${python_image}")"
vllm_image="$(pin_registry_image "${vllm_image}")"
caddy_image="$(pin_registry_image "${caddy_image}")"
set_env_value PYTHON_IMAGE "${python_image}"
set_env_value VLLM_IMAGE "${vllm_image}"
set_env_value CADDY_IMAGE "${caddy_image}"

printf 'Building the ARM64 Scout gateway from the current source tree...\n'
docker compose --env-file "${env_file}" -f "${project_root}/compose.v2.yml" build --pull gateway

printf 'Preparing the small host-side Scout smoke-test environment...\n'
if [[ ! -x "${project_root}/.venv-v2/bin/python" ]]; then
  python3 -m venv "${project_root}/.venv-v2" \
    || fail "python3 venv is unavailable; install the DGX OS python3-venv package in the approved maintenance window"
fi
"${project_root}/.venv-v2/bin/python" -m pip install \
  --disable-pip-version-check \
  --constraint "${project_root}/requirements.lock" \
  httpx==0.28.1 pillow==11.3.0 pydantic==2.11.7
"${project_root}/.venv-v2/bin/python" -c \
  'import hashlib, httpx, PIL, pydantic; assert hasattr(hashlib, "scrypt"); assert httpx.__version__ == "0.28.1"; assert PIL.__version__ == "11.3.0"; assert pydantic.__version__ == "2.11.7"'

token_mount=()
if [[ -n "${hf_token_file}" ]]; then
  token_mount=(--mount "type=bind,source=$(cd "$(dirname "${hf_token_file}")" && pwd)/$(basename "${hf_token_file}"),target=/run/secrets/hf_token,readonly")
fi

printf 'Caching %s at immutable revision %s...\n' "${model}" "${revision}"
docker run --rm \
  --platform linux/arm64 \
  --user "$(id -u):$(id -g)" \
  --entrypoint python \
  --network bridge \
  --env HOME=/tmp \
  --env HF_HOME=/model-cache \
  --env HF_HUB_DISABLE_TELEMETRY=1 \
  --mount "type=bind,source=${hf_cache},target=/model-cache" \
  "${token_mount[@]}" \
  "${vllm_image}" \
  -c '
import pathlib
import sys
from huggingface_hub import snapshot_download

repo_id, revision = sys.argv[1:3]
token_path = pathlib.Path("/run/secrets/hf_token")
token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else None
resolved = snapshot_download(
    repo_id=repo_id,
    revision=revision,
    cache_dir="/model-cache/hub",
    token=token,
)
if pathlib.Path(resolved).name.lower() != revision.lower():
    raise SystemExit(f"resolved snapshot does not match requested revision: {resolved}")
if not any(path.is_file() for path in pathlib.Path(resolved).rglob("*")):
    raise SystemExit(f"resolved snapshot is empty or contains only broken links: {resolved}")
print(resolved)
' "${model}" "${revision}"

docker compose --env-file "${env_file}" -f "${project_root}/compose.v2.yml" config --quiet
gateway_image="$(cfg SCOUT_GATEWAY_IMAGE relicscope-scout-gateway:2.0.0-arm64)"
gateway_image_id="$(docker image inspect --format '{{.Id}}' "${gateway_image}")"
gateway_image_user="$(docker image inspect --format '{{.Config.User}}' "${gateway_image}")"
{
  printf 'prepared_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_commit=%s\n' "$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}')"
  printf 'model=%s\n' "${model}"
  printf 'model_revision=%s\n' "${revision}"
  printf 'max_model_len=%s\n' "${max_model_len}"
  printf 'gpu_memory_utilization=%s\n' "${gpu_memory_utilization}"
  printf 'python_image=%s\n' "${python_image}"
  printf 'vllm_image=%s\n' "${vllm_image}"
  printf 'caddy_image=%s\n' "${caddy_image}"
  printf 'gateway_image=%s\n' "${gateway_image}"
  printf 'gateway_image_id=%s\n' "${gateway_image_id}"
  printf 'gateway_image_user=%s\n' "${gateway_image_user}"
} >"${project_root}/runtime/preparation/v2-runtime-manifest.txt"
chmod 600 "${project_root}/runtime/preparation/v2-runtime-manifest.txt"

printf '%s\n' \
  'Online preparation complete. No service was started.' \
  'Disconnect or disable the approved download path, then run make v2-preflight.'
