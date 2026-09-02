#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_ENV_FILE:-${project_root}/.env.v2}"
env_template="${V2_ENV_TEMPLATE:-${project_root}/.env.v2.example}"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

[[ "$(uname -s)" == "Linux" ]] || fail "V2 installation must run on the DGX Spark Linux host"
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) fail "V2 installation requires ARM64" ;;
esac
[[ "$(id -u)" != "0" ]] || fail "run V2 installation as the non-root Spark operator"
for command_name in awk docker git install mktemp openssl python3; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable to this user"
compose_version="$(docker compose version --short 2>/dev/null)" \
  || fail "Docker Compose v2 is unavailable"
python3 - "${compose_version}" <<'PY'
import re
import sys

match = re.search(r"(\d+)\.(\d+)", sys.argv[1])
if not match or tuple(map(int, match.groups())) < (2, 30):
    raise SystemExit(f"Docker Compose 2.30 or newer is required; detected {sys.argv[1]!r}")
PY
[[ -z "$(git -C "${project_root}" status --porcelain --untracked-files=all)" ]] \
  || fail "installation requires a clean checked-out source tree"

if [[ ! -f "${env_file}" ]]; then
  [[ -f "${env_template}" && ! -L "${env_template}" ]] \
    || fail "V2 environment template is unavailable: ${env_template}"
  install -m 600 "${env_template}" "${env_file}"
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

set_env_value APP_UID "$(id -u)"
set_env_value APP_GID "$(id -g)"
source_commit="$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}' 2>/dev/null || true)"
if [[ "${source_commit}" =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]]; then
  set_env_value RELICSCOPE_GIT_COMMIT "${source_commit}"
fi

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

data_dir="$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/v2-data)")"
hf_cache_dir="$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")"
vllm_cache_dir="$(absolute_path "$(cfg VLLM_CACHE_DIR ./runtime/vllm-cache)")"
nim_cache_dir="$(absolute_path "$(cfg NIM_CACHE_DIR ./runtime/nim-cache)")"
caddy_data_dir="$(absolute_path "$(cfg CADDY_DATA_DIR ./runtime/caddy/data)")"
caddy_config_dir="$(absolute_path "$(cfg CADDY_CONFIG_DIR ./runtime/caddy/config)")"
secret_file="$(absolute_path "$(cfg SERVICE_API_KEY_FILE ./secrets/service_api_key)")"
validate_managed_paths \
  "${data_dir}" "${hf_cache_dir}" "${vllm_cache_dir}" "${nim_cache_dir}" \
  "${caddy_data_dir}" "${caddy_config_dir}" "${secret_file}"

install -d -m 700 -- \
  "${data_dir}" "${data_dir}/scout-media" \
  "${hf_cache_dir}" "${vllm_cache_dir}" "${nim_cache_dir}" \
  "${caddy_data_dir}" "${caddy_config_dir}" \
  "$(dirname "${secret_file}")" "${project_root}/runtime/provisioning"
for managed_dir in \
  "${data_dir}" "${hf_cache_dir}" "${vllm_cache_dir}" "${nim_cache_dir}" \
  "${caddy_data_dir}" "${caddy_config_dir}"; do
  [[ -d "${managed_dir}" && ! -L "${managed_dir}" ]] \
    || fail "managed directory is unavailable or became a symlink: ${managed_dir}"
done

if [[ ! -s "${secret_file}" ]]; then
  temporary_key="$(mktemp "$(dirname "${secret_file}")/.service-key.XXXXXX")"
  openssl rand -hex 48 >"${temporary_key}"
  chmod 600 "${temporary_key}"
  mv -- "${temporary_key}" "${secret_file}"
  printf 'Generated a new private gateway/model service key.\n'
else
  [[ -f "${secret_file}" && ! -L "${secret_file}" ]] \
    || fail "service API key must be a non-symlink regular file"
  chmod 600 "${secret_file}"
  printf 'Preserved the existing service key.\n'
fi
python3 - "${secret_file}" <<'PY'
import pathlib
import re
import sys

value = pathlib.Path(sys.argv[1]).read_text(encoding="ascii").strip()
if not re.fullmatch(r"[A-Za-z0-9._~-]{32,256}", value):
    raise SystemExit("service API key must be one safe ASCII token of 32-256 characters")
PY

printf '%s\n' \
  'V2 host directories, non-root container identity, configuration and secret are ready.' \
  "Review ${env_file}; set SCOUT_BIND_IP to this Spark private LAN address and" \
  'follow the runtime-specific prepare, preflight and start commands in the deployment guide.'
