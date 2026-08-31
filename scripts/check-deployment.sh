#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

failures=0
checked=0
while IFS= read -r -d '' script; do
  checked=$((checked + 1))
  if ! bash -n "$script"; then failures=$((failures + 1)); fi
  if ! grep -Eq '^set -[^#]*E[^#]*e[^#]*u[^#]*o[[:space:]]+pipefail|^set -Eeuo pipefail' "$script"; then
    printf 'ERROR: strict shell mode is missing: %s\n' "$script" >&2
    failures=$((failures + 1))
  fi
done < <(find "${PROJECT_DIR}/deploy" "${PROJECT_DIR}/scripts" -type f -name '*.sh' -print0 | sort -z)

if command -v shellcheck >/dev/null 2>&1; then
  while IFS= read -r -d '' script; do
    shellcheck -x "$script" || failures=$((failures + 1))
  done < <(find "${PROJECT_DIR}/deploy" "${PROJECT_DIR}/scripts" -type f -name '*.sh' -print0 | sort -z)
else
  printf '%s\n' 'INFO: shellcheck is not installed; bash -n and policy checks were run.'
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  (
    cd "$PROJECT_DIR"
    docker compose --env-file .env.example -f compose.yml config --quiet
    REFERENCE_EMBEDDING_MODEL_REVISION=0000000000000000000000000000000000000000 \
      docker compose --env-file .env.example -f compose.single.yml config --quiet
    REFERENCE_EMBEDDING_MODEL_REVISION=0000000000000000000000000000000000000000 \
      docker compose --env-file .env.example -f compose.single.yml config --format json \
      | python3 -c '
import json
import sys

config = json.load(sys.stdin)
services = config["services"]
embedding = services["reference-embedding"]
app = services["app"]
assert not embedding.get("ports"), "reference embedding must not publish a port"
assert embedding.get("read_only") is True, "reference embedding root must be read-only"
assert embedding.get("cap_drop") == ["ALL"], "reference embedding capabilities must be dropped"
assert embedding.get("gpus") or embedding.get("deploy"), "reference embedding GPU request is missing"
secret_sources = {
    item if isinstance(item, str) else item.get("source")
    for item in embedding.get("secrets", [])
}
assert "service_api_key" in secret_sources, "reference embedding service key is missing"
assert app["depends_on"]["reference-embedding"]["condition"] == "service_healthy"
assert app["environment"]["REFERENCE_EMBEDDING_BASE_URL"] == "http://reference-embedding:8010/v1"
network_name = next(iter(embedding["networks"]))
assert config["networks"][network_name].get("internal") is True
'
  ) || failures=$((failures + 1))
else
  printf '%s\n' 'INFO: Docker Compose is unavailable; compose static parsing was skipped.'
fi

for required in \
  Dockerfile.embedding requirements-embedding.lock embedding_server/main.py \
  scripts/import-reference-library.py scripts/build-reference-vector-index.py \
  scripts/evaluate-reference-recognition.py scripts/seal-reference-calibration.py \
  deploy/reference-library.sh docs/REFERENCE_LIBRARY_DEPLOYMENT.md; do
  if [[ ! -f "${PROJECT_DIR}/${required}" ]]; then
    printf 'ERROR: reference deployment file is missing: %s\n' "$required" >&2
    failures=$((failures + 1))
  fi
done
for allowlisted in \
  '!Dockerfile.embedding' '!requirements-embedding.lock' '!embedding_server/**' \
  '!scripts/import-reference-library.py' '!scripts/build-reference-vector-index.py' \
  '!scripts/evaluate-reference-recognition.py' '!scripts/seal-reference-calibration.py'; do
  if ! grep -Fqx "$allowlisted" "${PROJECT_DIR}/.dockerignore"; then
    printf 'ERROR: embedding build input is excluded by .dockerignore: %s\n' "$allowlisted" >&2
    failures=$((failures + 1))
  fi
done
if ! grep -Fqx 'sentence-transformers[image]==5.4.0' "${PROJECT_DIR}/requirements-embedding.lock"; then
  printf '%s\n' 'ERROR: multimodal Sentence Transformers 5.4.0 is not pinned.' >&2
  failures=$((failures + 1))
fi
for copied_cli in \
  scripts/import-reference-library.py scripts/build-reference-vector-index.py \
  scripts/evaluate-reference-recognition.py scripts/seal-reference-calibration.py; do
  if ! grep -Fq "$copied_cli" "${PROJECT_DIR}/Dockerfile"; then
    printf 'ERROR: application image does not copy reference CLI: %s\n' "$copied_cli" >&2
    failures=$((failures + 1))
  fi
done
if ! grep -Fqx 'transformers==4.57.3' "${PROJECT_DIR}/requirements-embedding.lock"; then
  printf '%s\n' 'ERROR: the Qwen3-VL-compatible Transformers version is not pinned.' >&2
  failures=$((failures + 1))
fi

if ((failures)); then
  printf 'Deployment checks failed: %s issue(s), %s shell script(s) inspected.\n' "$failures" "$checked" >&2
  exit 1
fi
printf 'Deployment checks passed: %s shell script(s); Compose checked when available.\n' "$checked"
