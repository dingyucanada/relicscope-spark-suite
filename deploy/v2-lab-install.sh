#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_LAB_ENV_FILE:-${project_root}/.env.v2.lab}"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

[[ "$(uname -s)" == "Linux" ]] || fail "the lab node must run DGX Spark Linux"
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) fail "the lab node requires ARM64" ;;
esac
[[ "$(id -u)" != "0" ]] || fail "run the installer as the non-root Spark operator"
for command_name in awk docker git install mktemp nvidia-smi openssl python3 stat tr; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
docker info >/dev/null 2>&1 || fail "Docker is unavailable to this user"
compose_version="$(docker compose version --short 2>/dev/null)" \
  || fail "Docker Compose v2 is unavailable"
python3 - "${compose_version}" <<'PY'
import re
import sys

match = re.search(r"(\d+)\.(\d+)", sys.argv[1])
if not match or tuple(map(int, match.groups())) < (2, 30):
    raise SystemExit(f"Docker Compose 2.30 or newer is required; detected {sys.argv[1]!r}")
PY
nvidia-smi -L >/dev/null 2>&1 || fail "NVIDIA GPU is unavailable"
gpu_names="$(nvidia-smi --query-gpu=name --format=csv,noheader)"
[[ "${gpu_names,,}" == *"gb10"* ]] || fail "the GB10 GPU identity was not verified"
hardware_model=""
for model_path in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
  if [[ -r "${model_path}" ]]; then
    hardware_model="$(tr -d '\000' <"${model_path}")"
    break
  fi
done
[[ "${hardware_model,,}" == *"dgx spark"* ]] \
  || fail "hardware identity is not NVIDIA DGX Spark: ${hardware_model:-unknown}"
[[ -z "$(git -C "${project_root}" status --porcelain --untracked-files=all)" ]] \
  || fail "installation requires a clean checked-out source tree"

if [[ ! -f "${env_file}" ]]; then
  install -m 600 "${project_root}/.env.v2.lab.example" "${env_file}"
  printf 'Created %s\n' "${env_file}"
else
  chmod 600 "${env_file}"
  printf 'Preserved %s\n' "${env_file}"
fi

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

set_env_value LAB_UID "$(id -u)"
set_env_value LAB_GID "$(id -g)"
source_commit="$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}')"
[[ "${source_commit}" =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]] \
  || fail "the checked-out source commit is not immutable"
set_env_value RELICSCOPE_LAB_GIT_COMMIT "${source_commit}"
set_env_value LAB_OFFLINE_MODE true

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

absolute_path() {
  [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "${project_root}" "$1"
}

validate_managed_paths() {
  python3 "${project_root}/deploy/validate-v2-managed-paths.py" \
    --project-root "${project_root}" "$@"
}

hf_cache="$(absolute_path "$(cfg LAB_HF_CACHE_DIR ./runtime/lab-hf-cache)")"
vllm_cache="$(absolute_path "$(cfg LAB_VLLM_CACHE_DIR ./runtime/lab-vllm-cache)")"
caddy_data="$(absolute_path "$(cfg LAB_CADDY_DATA_DIR ./runtime/lab-caddy/data)")"
caddy_config="$(absolute_path "$(cfg LAB_CADDY_CONFIG_DIR ./runtime/lab-caddy/config)")"
secret_file="$(absolute_path "$(cfg LAB_API_KEY_FILE ./secrets/lab_api_key)")"
validate_managed_paths \
  "${hf_cache}" "${vllm_cache}" "${caddy_data}" "${caddy_config}" \
  "${secret_file}"

install -d -m 700 -- \
  "${hf_cache}" "${vllm_cache}" "${caddy_data}" "${caddy_config}" \
  "$(dirname "${secret_file}")" "${project_root}/runtime/lab-preparation"
for managed_dir in "${hf_cache}" "${vllm_cache}" "${caddy_data}" "${caddy_config}"; do
  [[ -d "${managed_dir}" && ! -L "${managed_dir}" ]] \
    || fail "managed directory is unavailable or became a symlink: ${managed_dir}"
done
if [[ ! -s "${secret_file}" ]]; then
  temporary_key="$(mktemp "$(dirname "${secret_file}")/.lab-key.XXXXXX")"
  openssl rand -hex 48 >"${temporary_key}"
  chmod 600 "${temporary_key}"
  mv -- "${temporary_key}" "${secret_file}"
  printf 'Generated a private lab model API key.\n'
else
  [[ -f "${secret_file}" && ! -L "${secret_file}" ]] \
    || fail "lab API key must be a non-symlink regular file"
  chmod 600 "${secret_file}"
  printf 'Preserved the existing lab model API key.\n'
fi
[[ "$(stat -c '%a:%u:%g' "${secret_file}")" == "600:$(id -u):$(id -g)" ]] \
  || fail "lab API key must be mode 600 and owned by LAB_UID/LAB_GID"

if [[ ! -x "${project_root}/.venv-v2/bin/python" ]]; then
  python3 -m venv "${project_root}/.venv-v2" \
    || fail "python3 venv is unavailable; install python3-venv in an approved maintenance window"
fi

printf '%s\n' \
  'Lab host identity, non-root directories, secret and lightweight host environment are ready.' \
  "Set an immutable LAB_MODEL_REVISION in ${env_file}, then run:" \
  '  ./deploy/v2-lab-prepare-online.sh --allow-network'
