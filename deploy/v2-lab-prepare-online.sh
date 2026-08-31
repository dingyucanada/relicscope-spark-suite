#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_LAB_ENV_FILE:-${project_root}/.env.v2.lab}"
allow_network=0
hf_token_file=""

usage() {
  printf '%s\n' \
    'Usage: ./deploy/v2-lab-prepare-online.sh --allow-network [--hf-token-file FILE]' \
    'Uses an explicit download window to pin both registry images, cache exactly' \
    'one model revision and prepare the host-side HTTPS benchmark dependency.'
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
[[ "${allow_network}" == "1" ]] \
  || fail "online preparation requires the explicit --allow-network flag"
[[ -f "${env_file}" ]] \
  || fail "run deploy/v2-lab-install.sh and review .env.v2.lab first"
if [[ -n "${hf_token_file}" ]]; then
  [[ -s "${hf_token_file}" ]] || fail "Hugging Face token file is missing or empty"
fi
[[ "$(uname -s)" == "Linux" ]] || fail "online preparation must run on DGX Spark Linux"
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) fail "online preparation must run on ARM64 DGX Spark" ;;
esac
[[ "$(id -u)" != "0" ]] || fail "run online preparation as the non-root Spark operator"
for command_name in awk docker git install mktemp nvidia-smi python3 stat; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
docker info >/dev/null 2>&1 || fail "Docker is unavailable to this user"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
nvidia-smi -L >/dev/null 2>&1 || fail "NVIDIA GPU is unavailable"
gpu_names="$(nvidia-smi --query-gpu=name --format=csv,noheader)"
[[ "${gpu_names,,}" == *"gb10"* ]] || fail "the GB10 GPU identity was not verified"
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

model="$(cfg LAB_MODEL)"
model_profile="$(cfg LAB_MODEL_PROFILE nemotron3-nano-omni)"
revision="$(cfg LAB_MODEL_REVISION)"
max_model_len="$(cfg LAB_MAX_MODEL_LEN 32768)"
gpu_memory_utilization="$(cfg LAB_GPU_MEMORY_UTILIZATION 0.70)"
max_batched_tokens="$(cfg LAB_MAX_NUM_BATCHED_TOKENS 32768)"
video_fps="$(cfg LAB_VIDEO_FPS 2)"
video_max_frames="$(cfg LAB_VIDEO_MAX_FRAMES 128)"
vllm_image="$(cfg LAB_VLLM_IMAGE vllm/vllm-openai:v0.20.0)"
caddy_image="$(cfg LAB_CADDY_IMAGE caddy:2.10.2-alpine)"
hf_cache="$(absolute_path "$(cfg LAB_HF_CACHE_DIR ./runtime/lab-hf-cache)")"
vllm_cache="$(absolute_path "$(cfg LAB_VLLM_CACHE_DIR ./runtime/lab-vllm-cache)")"
caddy_data="$(absolute_path "$(cfg LAB_CADDY_DATA_DIR ./runtime/lab-caddy/data)")"
caddy_config="$(absolute_path "$(cfg LAB_CADDY_CONFIG_DIR ./runtime/lab-caddy/config)")"
secret_file="$(absolute_path "$(cfg LAB_API_KEY_FILE ./secrets/lab_api_key)")"
validate_managed_paths \
  "${hf_cache}" "${vllm_cache}" "${caddy_data}" "${caddy_config}" \
  "${secret_file}"
[[ -n "${model}" && "${model}" != *[[:space:]]* ]] || fail "LAB_MODEL must be a remote model identity"
[[ "${model_profile}" == "nemotron3-nano-omni" || "${model_profile}" == "generic-vlm" ]] \
  || fail "LAB_MODEL_PROFILE must be nemotron3-nano-omni or generic-vlm"
if [[ "${model_profile}" == "nemotron3-nano-omni" ]]; then
  [[ "${model}" == nvidia/Nemotron-3-Nano-Omni-* ]] \
    || fail "the nemotron3-nano-omni profile requires the NVIDIA Nemotron 3 Nano Omni model family"
fi
python3 - "${max_model_len}" "${gpu_memory_utilization}" \
  "${max_batched_tokens}" "${video_fps}" "${video_max_frames}" <<'PY'
import math
import sys

max_len = int(sys.argv[1])
memory = float(sys.argv[2])
batched = int(sys.argv[3])
fps = float(sys.argv[4])
frames = int(sys.argv[5])
if not 4096 <= max_len <= 131072:
    raise SystemExit("LAB_MAX_MODEL_LEN must be between 4096 and 131072")
if not math.isfinite(memory) or not 0.10 <= memory <= 0.95:
    raise SystemExit("LAB_GPU_MEMORY_UTILIZATION must be between 0.10 and 0.95")
if not 1024 <= batched <= max_len:
    raise SystemExit("LAB_MAX_NUM_BATCHED_TOKENS must be between 1024 and LAB_MAX_MODEL_LEN")
if not math.isfinite(fps) or not 0.1 <= fps <= 10:
    raise SystemExit("LAB_VIDEO_FPS must be between 0.1 and 10")
if not 1 <= frames <= 512:
    raise SystemExit("LAB_VIDEO_MAX_FRAMES must be between 1 and 512")
PY
[[ "${revision}" =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]] \
  || fail "LAB_MODEL_REVISION must be a 40- or 64-character immutable commit"
revision="${revision,,}"
set_env_value LAB_MODEL_REVISION "${revision}"
source_commit="$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}')"
[[ "$(cfg RELICSCOPE_LAB_GIT_COMMIT)" == "${source_commit}" ]] \
  || fail "RELICSCOPE_LAB_GIT_COMMIT does not match the checked-out source"
[[ "$(cfg LAB_UID)" == "$(id -u)" && "$(cfg LAB_GID)" == "$(id -g)" ]] \
  || fail "LAB_UID/LAB_GID do not match this operator; rerun the lab installer"
for managed_dir in "${hf_cache}" "${vllm_cache}" "${caddy_data}" "${caddy_config}"; do
  [[ -d "${managed_dir}" && ! -L "${managed_dir}" ]] \
    || fail "managed directory is unavailable or is a symlink: ${managed_dir}"
  [[ "$(stat -c '%u:%g' "${managed_dir}")" == "$(id -u):$(id -g)" ]] \
    || fail "managed directory owner does not match LAB_UID/LAB_GID: ${managed_dir}"
done
[[ -f "${secret_file}" && ! -L "${secret_file}" && -s "${secret_file}" ]] \
  || fail "lab API key must be a non-symlink regular file"
[[ "$(stat -c '%a:%u:%g' "${secret_file}")" == "600:$(id -u):$(id -g)" ]] \
  || fail "lab API key must be mode 600 and owned by LAB_UID/LAB_GID"
install -d -m 700 -- "${hf_cache}" "${project_root}/runtime/lab-preparation"

pin_registry_image() {
  local requested="$1"
  local digests=""
  local selected=""
  docker pull --platform linux/arm64 "${requested}" >&2
  if [[ "${requested}" =~ @sha256:[0-9a-fA-F]{64}$ ]]; then
    printf '%s' "${requested}"
    return
  fi
  digests="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${requested}")"
  while IFS= read -r candidate; do
    if [[ "${candidate}" =~ @sha256:[0-9a-fA-F]{64}$ ]]; then
      selected="${candidate}"
      break
    fi
  done <<<"${digests}"
  [[ -n "${selected}" ]] || fail "registry did not return an immutable digest for ${requested}"
  printf '%s' "${selected}"
}

printf 'Pulling ARM64 runtime images and recording registry digests...\n'
vllm_image="$(pin_registry_image "${vllm_image}")"
caddy_image="$(pin_registry_image "${caddy_image}")"
set_env_value LAB_VLLM_IMAGE "${vllm_image}"
set_env_value LAB_CADDY_IMAGE "${caddy_image}"
if [[ "${model_profile}" == "nemotron3-nano-omni" ]]; then
  docker run --rm --platform linux/arm64 --entrypoint python "${vllm_image}" \
    -c 'import vllm; assert vllm.__version__ == "0.20.0", vllm.__version__' \
    || fail "the Nemotron 3 Nano Omni profile requires vLLM 0.20.0"
fi

if [[ ! -x "${project_root}/.venv-v2/bin/python" ]]; then
  python3 -m venv "${project_root}/.venv-v2" \
    || fail "python3 venv is unavailable; install python3-venv in the approved window"
fi
printf 'Installing pinned host benchmark dependencies into .venv-v2...\n'
"${project_root}/.venv-v2/bin/python" -m pip install \
  --disable-pip-version-check \
  --constraint "${project_root}/requirements.lock" \
  httpx==0.28.1 pillow==11.3.0
"${project_root}/.venv-v2/bin/python" -c \
  'import httpx, PIL; assert httpx.__version__ == "0.28.1"; assert PIL.__version__ == "11.3.0"'

token_mount=()
if [[ -n "${hf_token_file}" ]]; then
  token_path="$(cd "$(dirname "${hf_token_file}")" && pwd)/$(basename "${hf_token_file}")"
  token_mount=(--mount "type=bind,source=${token_path},target=/run/secrets/hf_token,readonly")
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
resolved = pathlib.Path(snapshot_download(
    repo_id=repo_id,
    revision=revision,
    cache_dir="/model-cache/hub",
    token=token,
))
if resolved.name.lower() != revision.lower():
    raise SystemExit(f"resolved snapshot does not match requested revision: {resolved}")
if not any(path.is_file() for path in resolved.rglob("*")):
    raise SystemExit(f"resolved snapshot is empty: {resolved}")
print(resolved)
' "${model}" "${revision}"

docker compose --env-file "${env_file}" -f "${project_root}/compose.v2.lab.yml" config --quiet
manifest_tmp="$(mktemp "${project_root}/runtime/lab-preparation/.manifest.XXXXXX")"
{
  printf 'prepared_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_commit=%s\n' "${source_commit}"
  printf 'model_profile=%s\n' "${model_profile}"
  printf 'model=%s\n' "${model}"
  printf 'model_revision=%s\n' "${revision}"
  printf 'max_model_len=%s\n' "${max_model_len}"
  printf 'gpu_memory_utilization=%s\n' "${gpu_memory_utilization}"
  printf 'max_batched_tokens=%s\n' "${max_batched_tokens}"
  printf 'video_fps=%s\n' "${video_fps}"
  printf 'video_max_frames=%s\n' "${video_max_frames}"
  printf 'container_image=%s\n' "${vllm_image}"
  printf 'container_image=%s\n' "${caddy_image}"
} >"${manifest_tmp}"
chmod 600 "${manifest_tmp}"
mv -f -- "${manifest_tmp}" "${project_root}/runtime/lab-preparation/runtime-manifest.txt"

printf '%s\n' \
  'Online lab preparation is complete; no service was started.' \
  'Close the approved network window, then run ./deploy/v2-lab-preflight.sh.'
