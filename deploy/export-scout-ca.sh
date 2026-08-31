#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_ENV_FILE:-${project_root}/.env.v2}"

command -v python3 >/dev/null 2>&1 || {
  printf 'Python 3 is required to read .env.v2 safely.\n' >&2
  exit 1
}

cfg() {
  local key="$1"
  local fallback="${2-}"
  python3 "${project_root}/deploy/read-v2-env.py" \
    --file "${env_file}" --key "${key}" --default "${fallback}"
}

caddy_data="$(cfg CADDY_DATA_DIR ./runtime/caddy/data)"
[[ "${caddy_data}" == /* ]] || caddy_data="${project_root}/${caddy_data}"
source_ca="${caddy_data}/caddy/pki/authorities/local/root.crt"
target_dir="${project_root}/runtime/provisioning"
target_ca="${target_dir}/scout-local-ca.crt"

[[ -s "${source_ca}" ]] || {
  printf 'Caddy local CA is not available. Start compose.v2.yml first.\n' >&2
  exit 1
}
install -d -m 0700 "${target_dir}"
install -m 0644 "${source_ca}" "${target_ca}"
printf '%s\n' "${target_ca}"
