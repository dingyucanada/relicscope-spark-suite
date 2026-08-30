#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL=0
CHECK_ONLY=0
CONFIGURED_MODE=0

usage() {
  printf '%s\n' \
    "Usage: $0 [--install] [--check-only] [--configured-models]" \
    "  --install            install runtime/test dependencies, verify, then start" \
    "  --check-only         run repository checks without starting the service" \
    "  --configured-models  use approved private model endpoints from .env"
}

while (($#)); do
  case "$1" in
    --install) INSTALL=1; shift ;;
    --check-only) CHECK_ONLY=1; shift ;;
    --configured-models) CONFIGURED_MODE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'ERROR: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$CHECK_ONLY" == "1" && "$INSTALL" == "1" ]]; then
  printf '%s\n' 'ERROR: --install and --check-only are mutually exclusive.' >&2
  exit 2
fi

cd "$PROJECT_DIR"
printf '%s\n' 'RelicScope repository checks'

if command -v node >/dev/null 2>&1; then
  node --check app/static/app.js
else
  printf '%s\n' 'INFO: node is unavailable; JavaScript syntax check skipped.'
fi

./scripts/check-deployment.sh

if command -v openspec >/dev/null 2>&1; then
  openspec validate build-relicscope-dual-spark-demo --strict
else
  printf '%s\n' 'INFO: openspec is unavailable; strict spec validation skipped.'
fi

if [[ "$INSTALL" == "1" ]]; then
  RELICSCOPE_INSTALL_DEV=1 ./run_local.sh --install-only
fi

if [[ -x .venv/bin/python ]]; then
  .venv/bin/python -m pytest -q
  .venv/bin/python scripts/verify-demo-media.py
elif [[ "$CHECK_ONLY" == "1" ]]; then
  printf '%s\n' 'INFO: .venv is absent; Python tests will run after --install.'
fi

if [[ "$CHECK_ONLY" == "1" ]]; then
  printf '%s\n' 'Repository checks complete.'
  exit 0
fi

printf '%s\n' 'Starting loopback service; open http://127.0.0.1:8088'
if [[ "$CONFIGURED_MODE" == "1" ]]; then
  exec ./run_local.sh --configured-models
fi
exec ./run_local.sh
