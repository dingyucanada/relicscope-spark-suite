#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
VENV_DIR="${RELICSCOPE_VENV_DIR:-${PROJECT_DIR}/.venv}"
INSTALL=0
INSTALL_ONLY=0
CONFIGURED_MODE=0

usage() {
  printf '%s\n' \
    "Usage: $0 [--install] [--install-only] [--configured-models]" \
    "Default: loopback-only deterministic demo with all model endpoints disabled." \
    "Python: RELICSCOPE_PYTHON override, then python3.12, python3.11, python3; minimum 3.11." \
    "--install explicitly permits Python package installation." \
    "--install-only installs dependencies and exits without starting a service." \
    "--configured-models reads private model endpoints and the service key from .env."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --install) INSTALL=1; shift ;;
    --install-only) INSTALL=1; INSTALL_ONLY=1; shift ;;
    --configured-models) CONFIGURED_MODE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

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

absolute_path() {
  [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "$PROJECT_DIR" "$1"
}

safe_managed_dir() {
  "$PYTHON_BOOTSTRAP" - "$1" "$PROJECT_DIR" <<'PY'
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

select_python() {
  local requested="${RELICSCOPE_PYTHON:-}"
  local candidate=""
  local resolved=""
  local candidates=()
  if [[ -n "$requested" ]]; then
    candidates+=("$requested")
  else
    candidates+=(python3.12 python3.11 python3)
  fi
  for candidate in "${candidates[@]}"; do
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [[ -n "$resolved" ]] || continue
    if "$resolved" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
        >/dev/null 2>&1; then
      printf '%s' "$resolved"
      return
    fi
  done
  if [[ -n "$requested" ]]; then
    die "RELICSCOPE_PYTHON must resolve to Python 3.11 or newer: ${requested}"
  fi
  die "Python 3.11 or newer is required; install Python 3.12/3.11 or set RELICSCOPE_PYTHON to its executable"
}

PYTHON_BOOTSTRAP="$(select_python)"
printf 'Using Python bootstrap: %s\n' "$PYTHON_BOOTSTRAP"

if [[ "$INSTALL" == "1" ]]; then
  "$PYTHON_BOOTSTRAP" -m venv "$VENV_DIR"
  requirements_file="$PROJECT_DIR/requirements.lock"
  if [[ "${RELICSCOPE_INSTALL_DEV:-0}" == "1" ]]; then
    requirements_file="$PROJECT_DIR/requirements-dev.lock"
  fi
  "$VENV_DIR/bin/python" -m pip install --requirement "$requirements_file"
fi

python_bin="${VENV_DIR}/bin/python"
[[ -x "$python_bin" ]] \
  || die "virtual environment is missing; run '$0 --install' during an approved dependency-install window"
"$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
  || die "existing virtual environment uses Python older than 3.11; recreate ${VENV_DIR} with --install and an approved RELICSCOPE_PYTHON"

if [[ "$INSTALL_ONLY" == "1" ]]; then
  printf 'Installed RelicScope dependencies into %s; service was not started.\n' "$VENV_DIR"
  exit 0
fi

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export RELICSCOPE_DATA_DIR="$(safe_managed_dir "$(absolute_path "$(cfg RELICSCOPE_LOCAL_DATA_DIR ./runtime/local-data)")")"
export RELICSCOPE_HOST="127.0.0.1"
export RELICSCOPE_PORT="$(cfg RELICSCOPE_PORT 8088)"
export RELICSCOPE_DEMO_MODE=true
export RELICSCOPE_OFFLINE_MODE=true
export RELICSCOPE_REQUIRE_PRIVATE_ENDPOINTS=true
export RELICSCOPE_RUNTIME_MODE=local-development
export RELICSCOPE_NODE_ID=local-development
export RELICSCOPE_COMPUTE_NODE_ID=local-development
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1

mkdir -p -- "$RELICSCOPE_DATA_DIR"
chmod 700 "$RELICSCOPE_DATA_DIR"

if [[ "$CONFIGURED_MODE" == "1" ]]; then
  [[ -f "$ENV_FILE" ]] || die "environment file is missing: ${ENV_FILE}"
  service_key_file="$(absolute_path "$(cfg SERVICE_API_KEY_FILE ./secrets/service_api_key)")"
  [[ -f "$service_key_file" ]] || die "service API key file is missing: ${service_key_file}"
  "$PYTHON_BOOTSTRAP" - "$service_key_file" <<'PY'
import os
import stat
import sys

mode = stat.S_IMODE(os.stat(sys.argv[1]).st_mode)
if mode & 0o077:
    raise SystemExit(f"service API key permissions are too broad: {mode:03o}; run chmod 600")
PY
  service_key="$(<"$service_key_file")"
  [[ "${#service_key}" -ge 32 ]] || die "service API key must contain at least 32 characters"
  export VISION_BASE_URL="$(cfg VISION_BASE_URL '')"
  export EMBEDDING_BASE_URL="$(cfg EMBEDDING_BASE_URL '')"
  export REASONER_BASE_URL="$(cfg REASONER_BASE_URL '')"
  export MODEL_PROFILE="$(cfg MODEL_PROFILE qwen3-vl)"
  export VISION_MODEL_SOURCE="$(cfg VISION_MODEL_SOURCE Qwen/Qwen3-VL-30B-A3B-Instruct)"
  export VISION_MODEL="$(cfg VISION_MODEL qwen3_vl_30b_a3b)"
  export EMBEDDING_MODEL="$(cfg EMBEDDING_MODEL Qwen/Qwen3-VL-Embedding-2B)"
  export REASONER_MODEL="$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)"
  export VISION_API_KEY="$service_key"
  export EMBEDDING_API_KEY="$service_key"
  export REASONER_API_KEY="$service_key"
  unset service_key
  printf '%s\n' 'Starting local application with configured private model endpoints; no key value will be printed.'
else
  export VISION_BASE_URL=
  export EMBEDDING_BASE_URL=
  export REASONER_BASE_URL=
  export VISION_API_KEY=
  export EMBEDDING_API_KEY=
  export REASONER_API_KEY=
  printf '%s\n' 'Starting deterministic local-development mode; model calls are disabled and visibly degraded.'
fi

cd "$PROJECT_DIR"
printf 'Open http://127.0.0.1:%s (DEMO/SYNTHETIC; non-authentication demo).\n' "$RELICSCOPE_PORT"
exec "$python_bin" -m uvicorn app.main:app \
  --host "$RELICSCOPE_HOST" \
  --port "$RELICSCOPE_PORT" \
  --no-access-log
