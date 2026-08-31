#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
ROLE=""
SERVICE_KEY_SOURCE=""
GENERATE_KEY=0

usage() {
  printf '%s\n' \
    "Usage: $0 --role spark-a|spark-b|single [--service-key FILE | --generate-key]" \
    "Bootstraps local directories, permissions and configuration. It does not" \
    "install OS packages, download models, open firewall ports or start services."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    --service-key)
      (($# >= 2)) || die "--service-key requires a file"
      SERVICE_KEY_SOURCE="$2"
      shift 2
      ;;
    --generate-key) GENERATE_KEY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$ROLE" in
  spark-a|spark-b|single) ;;
  *) die "--role must be spark-a, spark-b, or single" ;;
esac
if [[ -n "$SERVICE_KEY_SOURCE" && "$GENERATE_KEY" == "1" ]]; then
  die "use either --service-key or --generate-key, not both"
fi

for command_name in awk install mktemp python3 docker; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command not found: ${command_name}"
done
[[ "$(uname -s)" == "Linux" ]] || die "target deployment requires Linux"
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) die "target deployment requires ARM64; detected $(uname -m)" ;;
esac
docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"
compose_version="$(docker compose version --short 2>/dev/null)" \
  || die "Docker Compose 2.30 or newer is unavailable"
python3 - "$compose_version" <<'PY'
import re
import sys

match = re.search(r"(\d+)\.(\d+)", sys.argv[1])
if not match or tuple(map(int, match.groups())) < (2, 30):
    raise SystemExit(f"Docker Compose 2.30 or newer is required; detected {sys.argv[1]!r}")
PY
command -v nvidia-smi >/dev/null 2>&1 || die "nvidia-smi is required"
nvidia-smi >/dev/null 2>&1 || die "nvidia-smi failed"

if [[ ! -f "$ENV_FILE" ]]; then
  install -m 600 "${PROJECT_DIR}/.env.example" "$ENV_FILE"
  printf 'Created configuration template: %s\n' "$ENV_FILE"
else
  chmod 600 "$ENV_FILE"
  printf 'Preserved existing configuration: %s\n' "$ENV_FILE"
fi

set_env_value() {
  local key="$1"
  local value="$2"
  local tmp=""
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")"
  awk -v key="$key" -v value="$value" '
    BEGIN { found=0 }
    $0 ~ "^[[:space:]]*" key "=" {
      if (!found) print key "=" value
      found=1
      next
    }
    { print }
    END { if (!found) print key "=" value }
  ' "$ENV_FILE" >"$tmp"
  chmod 600 "$tmp"
  mv -f -- "$tmp" "$ENV_FILE"
}

set_env_value APP_UID "$(id -u)"
set_env_value APP_GID "$(id -g)"
set_env_value DEPLOYMENT_ROLE "$ROLE"

cfg() {
  local key="$1"
  local fallback="${2-}"
  local value=""
  value="$(awk -v wanted="$key" '$0 ~ "^[[:space:]]*" wanted "=" {sub("^[[:space:]]*" wanted "=", "", $0); found=$0} END {print found}' "$ENV_FILE")"
  value="${value%$'\r'}"
  [[ "$value" == \"*\" && "$value" == *\" ]] && value="${value:1:${#value}-2}"
  [[ "$value" == \'*\' && "$value" == *\' ]] && value="${value:1:${#value}-2}"
  printf '%s' "${value:-$fallback}"
}

absolute_path() { [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "$PROJECT_DIR" "$1"; }
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

data_dir="$(safe_managed_dir "$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/data)")")"
hf_cache_dir="$(safe_managed_dir "$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")")"
vllm_cache_dir="$(safe_managed_dir "$(absolute_path "$(cfg VLLM_CACHE_DIR ./runtime/vllm-cache)")")"
backup_dir="$(safe_managed_dir "$(absolute_path "$(cfg BACKUP_DIR ./runtime/backups)")")"
package_dir="$(safe_managed_dir "$(absolute_path "$(cfg PACKAGE_DIR ./runtime/packages)")")"
secret_file="$(absolute_path "$(cfg SERVICE_API_KEY_FILE ./secrets/service_api_key)")"
[[ "$secret_file" != "/" && "$secret_file" != "$PROJECT_DIR" && "$(dirname -- "$secret_file")" != "/" ]] \
  || die "unsafe service key path: ${secret_file}"

install -d -m 700 -- \
  "$data_dir" "$data_dir/reference-library" \
  "$hf_cache_dir" "$vllm_cache_dir" "$backup_dir" "$package_dir" \
  "$(dirname -- "$secret_file")"

if [[ -n "$SERVICE_KEY_SOURCE" ]]; then
  [[ -f "$SERVICE_KEY_SOURCE" ]] || die "service key source is missing: ${SERVICE_KEY_SOURCE}"
  install -m 600 "$SERVICE_KEY_SOURCE" "$secret_file"
  printf 'Installed shared service key from an explicit source.\n'
elif [[ "$GENERATE_KEY" == "1" ]]; then
  command -v openssl >/dev/null 2>&1 || die "openssl is required for --generate-key"
  if [[ -s "$secret_file" ]]; then
    printf 'Preserved existing service key: %s\n' "$secret_file"
  else
    tmp_key="$(mktemp "$(dirname -- "$secret_file")/.service-key.XXXXXX")"
    openssl rand -hex 48 >"$tmp_key"
    chmod 600 "$tmp_key"
    mv -f -- "$tmp_key" "$secret_file"
    printf 'Generated service key: %s\n' "$secret_file"
  fi
elif [[ ! -s "$secret_file" ]]; then
  die "service key is absent; generate it once on Spark B, then securely copy the same file to Spark A with --service-key"
fi

python3 - "$secret_file" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
raw = open(path, "rb").read().strip()
if len(raw) < 32:
    raise SystemExit("service key must contain at least 32 bytes")
mode = stat.S_IMODE(os.stat(path).st_mode)
if mode & 0o077:
    raise SystemExit(f"service key permissions are too broad: {mode:03o}")
PY

printf '%s\n' \
  "Bootstrap complete for role=${ROLE}." \
  "Controlled reference-library directory: ${data_dir}/reference-library" \
  "Next: edit ${ENV_FILE}; configure the fixed private IPs, storage, model policy, and interconnect." \
  "During an approved online preparation window, run: make prefetch ROLE=${ROLE}" \
  "Then restore the offline flags and run: make preflight ROLE=${ROLE}" \
  "Do not start on a fresh machine before the required image and model cache checks pass."
