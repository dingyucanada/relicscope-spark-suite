#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${RELICSCOPE_SMOKE_PYTHON:-${PROJECT_DIR}/.venv/bin/python}"
PORT="${RELICSCOPE_MEDIA_SMOKE_PORT:-18088}"
MEDIA_DIR="${PROJECT_DIR}/demo_media"

if [[ "$PYTHON_BIN" != */* ]]; then
  PYTHON_BIN="$(command -v "$PYTHON_BIN" 2>/dev/null || true)"
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  printf '%s\n' "ERROR: project environment is missing; run ./run_local.sh --install once, then stop it." >&2
  exit 2
fi

"$PYTHON_BIN" "${PROJECT_DIR}/scripts/verify-demo-media.py" --media-dir "$MEDIA_DIR"

RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/relicscope-media-smoke.XXXXXX")"
SERVER_PID=""
cleanup() {
  if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  rm -rf -- "$RUN_DIR"
}
trap cleanup EXIT INT TERM

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export RELICSCOPE_DATA_DIR="${RUN_DIR}/data"
export RELICSCOPE_HOST="127.0.0.1"
export RELICSCOPE_PORT="$PORT"
export RELICSCOPE_DEMO_MODE=true
export RELICSCOPE_OFFLINE_MODE=true
export RELICSCOPE_REQUIRE_PRIVATE_ENDPOINTS=true
export RELICSCOPE_RUNTIME_MODE=local-development
export RELICSCOPE_NODE_ID=media-smoke
export RELICSCOPE_COMPUTE_NODE_ID=media-smoke
export VISION_BASE_URL=
export EMBEDDING_BASE_URL=
export REASONER_BASE_URL=
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

cd "$PROJECT_DIR"
"$PYTHON_BIN" -m uvicorn app.main:app \
  --host 127.0.0.1 --port "$PORT" --no-access-log \
  >"${RUN_DIR}/server.log" 2>&1 &
SERVER_PID=$!

ready=0
for _ in {1..80}; do
  if "$PYTHON_BIN" - "$PORT" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request

with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/health", timeout=0.5) as response:
    raise SystemExit(0 if json.load(response).get("status") else 1)
PY
  then
    ready=1
    break
  fi
  sleep 0.1
done
if [[ "$ready" != "1" ]]; then
  printf '%s\n' 'ERROR: temporary service did not become ready' >&2
  sed -n '1,160p' "${RUN_DIR}/server.log" >&2
  exit 1
fi

"$PYTHON_BIN" "${PROJECT_DIR}/scripts/media-smoke.py" \
  --base-url "http://127.0.0.1:${PORT}" \
  --image "${MEDIA_DIR}/reference.png" \
  --comparison-image "${MEDIA_DIR}/comparison.png" \
  --video "${MEDIA_DIR}/synthetic_orbit.mp4" \
  --frames-dir "${MEDIA_DIR}/frames" \
  --duration-ms 3000 \
  --max-frames 6 \
  --report-json "${RUN_DIR}/report.json"

printf '%s\n' 'Synthetic image → comparison → video frames → report → integrity smoke passed.'
