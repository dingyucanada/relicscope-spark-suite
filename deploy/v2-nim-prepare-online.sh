#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_ENV_FILE:-${project_root}/.env.v2.nim}"
ngc_key_file=""
allow_network=0

usage() {
  printf '%s\n' \
    'Usage: v2-nim-prepare-online.sh --allow-network --ngc-key-file /secure/ngc_api_key' \
    'The key is used only by the transient cache-download container and is never copied.'
}
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
while (($#)); do
  case "$1" in
    --allow-network) allow_network=1; shift ;;
    --ngc-key-file) [[ $# -ge 2 ]] || fail "--ngc-key-file needs a path"; ngc_key_file="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done
[[ "${allow_network}" == "1" ]] || fail "explicit --allow-network is required"
[[ "$(uname -s)" == "Linux" ]] || fail "run preparation on the target DGX Spark"
case "$(uname -m)" in aarch64|arm64) ;; *) fail "DGX Spark ARM64 is required" ;; esac
[[ "$(id -u)" != "0" ]] || fail "run as the non-root Spark operator"
for command_name in awk chmod date docker find git grep install mktemp mv nvidia-smi python3 rm stat tr; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
[[ -f "${env_file}" ]] || fail "run make v2-nim-install and review .env.v2.nim"
[[ -n "${ngc_key_file}" && -f "${ngc_key_file}" && ! -L "${ngc_key_file}" ]] \
  || fail "a non-symlink --ngc-key-file is required for this NIM release"
permissions="$(stat -c '%a' "${ngc_key_file}")"
[[ "${permissions}" == "600" || "${permissions}" == "400" ]] \
  || fail "NGC key file permissions must be 600 or 400"
[[ -z "$(git -C "${project_root}" status --porcelain --untracked-files=all)" ]] \
  || fail "preparation requires a clean source tree"
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable to this user"
nvidia-smi -L >/dev/null 2>&1 || fail "NVIDIA GPU is unavailable"

ngc_key="$(tr -d '\r\n' <"${ngc_key_file}")"
[[ "${#ngc_key}" -ge 16 ]] || fail "NGC key file is empty or malformed"
docker_config="$(mktemp -d "${TMPDIR:-/tmp}/relicscope-nvcr.XXXXXX")"
chmod 700 "${docker_config}"
cleanup_registry() {
  if [[ -n "${docker_config:-}" && -d "${docker_config}" ]]; then
    DOCKER_CONFIG="${docker_config}" docker logout nvcr.io >/dev/null 2>&1 || true
    case "${docker_config}" in
      "${TMPDIR:-/tmp}"/relicscope-nvcr.*) rm -rf -- "${docker_config}" ;;
    esac
  fi
  unset ngc_key NGC_API_KEY
}
trap cleanup_registry EXIT
printf '%s' "${ngc_key}" \
  | DOCKER_CONFIG="${docker_config}" docker login nvcr.io \
      --username '$oauthtoken' --password-stdin >/dev/null

cfg() {
  python3 "${project_root}/deploy/read-v2-env.py" \
    --file "${env_file}" --key "$1" --default "${2-}"
}
absolute_path() { [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "${project_root}" "$1"; }
set_env_value() {
  local key="$1" value="$2" temporary
  temporary="$(mktemp "${env_file}.XXXXXX")"
  awk -v wanted="${key}" -v replacement="${value}" '
    BEGIN { found=0 }
    $0 ~ "^[[:space:]]*" wanted "=" { if (!found) print wanted "=" replacement; found=1; next }
    { print }
    END { if (!found) print wanted "=" replacement }
  ' "${env_file}" >"${temporary}"
  chmod 600 "${temporary}"
  mv -f -- "${temporary}" "${env_file}"
}
pin_registry_image() {
  local requested="$1" digests selected
  if [[ "${requested}" == nvcr.io/* ]]; then
    DOCKER_CONFIG="${docker_config}" docker pull "${requested}" >&2
  else
    docker pull "${requested}" >&2
  fi
  if [[ "${requested}" =~ @sha256:[0-9a-fA-F]{64}$ ]]; then printf '%s' "${requested}"; return; fi
  digests="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${requested}")"
  selected="${digests%%$'\n'*}"
  [[ "${selected}" =~ @sha256:[0-9a-fA-F]{64}$ ]] || fail "registry returned no digest for ${requested}"
  printf '%s' "${selected}"
}

profile="$(cfg NIM_MODEL_PROFILE)"
[[ "${profile}" =~ ^[0-9a-fA-F]{64}$ ]] \
  || fail "set NIM_MODEL_PROFILE to one ID returned by make v2-nim-list-profiles"
profile="${profile,,}"
set_env_value NIM_MODEL_PROFILE "${profile}"
set_env_value VISION_MODEL_REVISION "${profile}"
[[ "$(cfg RELICSCOPE_OFFLINE_MODE)" == "true" ]] || fail "RELICSCOPE_OFFLINE_MODE must remain true"
[[ "$(cfg NIM_DISABLE_MODEL_DOWNLOAD)" == "1" ]] || fail "NIM_DISABLE_MODEL_DOWNLOAD must remain 1"

nim_cache="$(absolute_path "$(cfg NIM_CACHE_DIR ./runtime/nim-cache)")"
data_dir="$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/v2-data)")"
caddy_data="$(absolute_path "$(cfg CADDY_DATA_DIR ./runtime/caddy/data)")"
caddy_config="$(absolute_path "$(cfg CADDY_CONFIG_DIR ./runtime/caddy/config)")"
secret_file="$(absolute_path "$(cfg SERVICE_API_KEY_FILE ./secrets/service_api_key)")"
python3 "${project_root}/deploy/validate-v2-managed-paths.py" --project-root "${project_root}" \
  "${nim_cache}" "${data_dir}" "${caddy_data}" "${caddy_config}" "${secret_file}"
install -d -m 700 -- "${nim_cache}" "${project_root}/runtime/preparation"
min_prepare_free="$(cfg NIM_PREPARE_MIN_FREE_BYTES 68719476736)"
[[ "${min_prepare_free}" =~ ^[0-9]+$ && "${min_prepare_free}" -gt 0 ]] \
  || fail "NIM_PREPARE_MIN_FREE_BYTES must be a positive integer"
python3 - "${nim_cache}" "${min_prepare_free}" <<'PY'
import shutil
import sys

cache_path, minimum = sys.argv[1], int(sys.argv[2])
free = shutil.disk_usage(cache_path).free
if free < minimum:
    raise SystemExit(
        f"NIM preparation needs at least {minimum} free bytes; cache volume has {free}"
    )
print(f"NIM cache volume free bytes before preparation: {free}")
PY

printf '%s\n' 'Pulling and pinning the gateway base, Qwen NIM and HTTPS ingress images...'
python_image="$(pin_registry_image "$(cfg PYTHON_IMAGE python:3.12.11-slim-bookworm)")"
requested_nim_image="$(cfg NIM_VLM_IMAGE nvcr.io/nim/qwen/qwen3.6-35b-a3b:1.7.1-variant)"
[[ "${requested_nim_image}" =~ ^nvcr\.io/nim/qwen/qwen3\.6-35b-a3b:(1\.7\.1-variant)$ \
   || "${requested_nim_image}" =~ ^nvcr\.io/nim/qwen/qwen3\.6-35b-a3b@sha256:[0-9a-fA-F]{64}$ ]] \
  || fail "NIM_VLM_IMAGE must be the approved Qwen3.6 NVIDIA NIM repository and release"
nim_image="$(pin_registry_image "${requested_nim_image}")"
[[ "${nim_image}" =~ ^nvcr\.io/nim/qwen/qwen3\.6-35b-a3b@sha256:[0-9a-fA-F]{64}$ ]] \
  || fail "resolved NIM image is outside the approved Qwen3.6 repository"
caddy_image="$(pin_registry_image "$(cfg CADDY_IMAGE caddy:2.10.2-alpine)")"
set_env_value PYTHON_IMAGE "${python_image}"
set_env_value NIM_VLM_IMAGE "${nim_image}"
set_env_value CADDY_IMAGE "${caddy_image}"

profiles="$({ docker run --rm --platform linux/arm64 --runtime=nvidia --gpus all \
  --network none "${nim_image}" list-model-profiles; } 2>&1)" \
  || fail "NIM could not list model profiles on this GPU"
grep -Fq -- "${profile}" <<<"${profiles}" \
  || fail "configured NIM profile is not reported compatible on this exact Spark"
printf '%s\n' "${profiles}" >"${project_root}/runtime/preparation/nim-compatible-profiles.txt"
chmod 600 "${project_root}/runtime/preparation/nim-compatible-profiles.txt"

printf 'Downloading frozen NIM profile %s to the private cache...\n' "${profile}"
NGC_API_KEY="${ngc_key}" docker run --rm \
  --platform linux/arm64 \
  --runtime=nvidia \
  --gpus all \
  --shm-size=16g \
  --env NGC_API_KEY \
  --mount "type=bind,source=${nim_cache},target=/opt/nim/.cache" \
  "${nim_image}" download-to-cache --profile "${profile}"
unset ngc_key NGC_API_KEY
find "${nim_cache}" -type f -print -quit | grep -q . || fail "NIM cache is empty after download"

source_commit="$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}')"
set_env_value RELICSCOPE_GIT_COMMIT "${source_commit}"
printf '%s\n' 'Building the ARM64 Scout gateway from this frozen source commit...'
docker compose --env-file "${env_file}" -f "${project_root}/compose.v2.nim.yml" build --pull gateway
docker compose --env-file "${env_file}" -f "${project_root}/compose.v2.nim.yml" config --quiet

printf '%s\n' 'Preparing the bounded host-side Scout acceptance tools...'
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

gateway_image="$(cfg SCOUT_GATEWAY_IMAGE relicscope-scout-gateway:2.1.0-arm64)"
gateway_image_id="$(docker image inspect --format '{{.Id}}' "${gateway_image}")"
{
  printf 'prepared_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_commit=%s\n' "${source_commit}"
  printf 'model=%s\n' "$(cfg VISION_MODEL)"
  printf 'nim_profile=%s\n' "${profile}"
  printf 'nim_image=%s\n' "${nim_image}"
  printf 'gateway_image=%s\n' "${gateway_image}"
  printf 'gateway_image_id=%s\n' "${gateway_image_id}"
  printf 'python_image=%s\n' "${python_image}"
  printf 'caddy_image=%s\n' "${caddy_image}"
} >"${project_root}/runtime/preparation/v2-nim-runtime-manifest.txt"
chmod 600 "${project_root}/runtime/preparation/v2-nim-runtime-manifest.txt"
printf '%s\n' \
  'NIM cache and images are prepared. The NGC key was not copied.' \
  'Disable the approved download path, then run make v2-nim-preflight.'
