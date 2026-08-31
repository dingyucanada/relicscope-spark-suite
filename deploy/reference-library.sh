#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
COMMAND="${1-}"
[[ -n "$COMMAND" ]] && shift
CALIBRATION_INPUT="calibration-input.json"
EVALUATION_MANIFEST="evaluation-manifest.json"
TARGET_FAR="0.02"
TOP_K="5"

usage() {
  printf '%s\n' \
    "Usage: $0 verify|import|build|evaluate|seal|status [options]" \
    "Options: --evaluation-manifest FILE --target-far RATE --top-k N --calibration-input FILE" \
    "Operates only on RELICSCOPE_DATA_HOST_DIR/reference-library." \
    "The build command starts the private embedding sidecar without publishing a port."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --calibration-input)
      (($# >= 2)) || die "--calibration-input requires a filename"
      CALIBRATION_INPUT="$2"
      shift 2
      ;;
    --evaluation-manifest)
      (($# >= 2)) || die "--evaluation-manifest requires a filename"
      EVALUATION_MANIFEST="$2"
      shift 2
      ;;
    --target-far)
      (($# >= 2)) || die "--target-far requires a rate"
      TARGET_FAR="$2"
      shift 2
      ;;
    --top-k)
      (($# >= 2)) || die "--top-k requires an integer"
      TOP_K="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
case "$COMMAND" in -h|--help) usage; exit 0 ;; esac
case "$COMMAND" in verify|import|build|evaluate|seal|status) ;; *) usage; die "invalid command: ${COMMAND:-missing}" ;; esac

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

absolute_path() { [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "$PROJECT_DIR" "$1"; }

safe_reference_dir() {
  python3 - "$1" "$PROJECT_DIR" <<'PY'
import os
import sys

value, project = sys.argv[1:3]
path = os.path.realpath(value)
forbidden = {
    "/", "/home", "/opt", "/srv", "/tmp", "/usr", "/var",
    os.path.realpath(os.path.expanduser("~")), os.path.realpath(project),
}
if path in forbidden or not path.endswith("/reference-library"):
    raise SystemExit(f"refusing unsafe reference-library directory: {path}")
print(path)
PY
}

cached_model_revision() {
  local model_id="$1" cache_dir cache_name root revision=""
  cache_dir="$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")"
  cache_name="models--${model_id//\//--}"
  for root in "${cache_dir}/hub/${cache_name}" "${cache_dir}/${cache_name}"; do
    [[ -f "${root}/refs/main" ]] || continue
    revision="$(tr -d '\r\n' <"${root}/refs/main")"
    [[ "$revision" =~ ^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$ ]] || continue
    [[ -f "${root}/snapshots/${revision}/config.json" ]] || continue
    printf '%s' "${revision,,}"
    return
  done
  die "immutable cached model revision is unavailable; run make prefetch ROLE=single first"
}

wait_for_embedding() {
  local wait_seconds deadline container_id health
  wait_seconds="$(cfg HEALTH_WAIT_SECONDS 900)"
  [[ "$wait_seconds" =~ ^[0-9]+$ ]] || die "HEALTH_WAIT_SECONDS must be a non-negative integer"
  deadline=$((SECONDS + wait_seconds))
  while true; do
    container_id="$({
      cd "$PROJECT_DIR"
      docker compose --env-file "$ENV_FILE" -f compose.single.yml ps -q reference-embedding
    })"
    health=""
    if [[ -n "$container_id" ]]; then
      health="$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)"
    fi
    [[ "$health" == "healthy" ]] && return
    ((SECONDS < deadline)) || die "reference embedding sidecar did not become healthy in ${wait_seconds}s"
    sleep 5
  done
}

[[ -f "$ENV_FILE" ]] || die "environment file is missing: ${ENV_FILE}"
for command_name in awk install python3; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command not found: ${command_name}"
done
if [[ "$COMMAND" != "status" ]]; then
  command -v docker >/dev/null 2>&1 || die "required command not found: docker"
  docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"
fi

data_dir="$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/data)")"
reference_dir_input="${data_dir}/reference-library"
[[ ! -L "$reference_dir_input" ]] || die "reference-library directory must not be a symbolic link"
reference_dir="$(safe_reference_dir "$reference_dir_input")"
install -d -m 700 -- "$reference_dir"

for controlled_filename in "$CALIBRATION_INPUT" "$EVALUATION_MANIFEST"; do
  case "$controlled_filename" in
    -*|*/*|.|..) die "controlled inputs must be ordinary filenames inside the reference-library directory" ;;
  esac
done
python3 - "$TARGET_FAR" "$TOP_K" <<'PY'
import math
import sys

try:
    target_far = float(sys.argv[1])
    top_k = int(sys.argv[2])
except ValueError as exc:
    raise SystemExit("--target-far must be numeric and --top-k must be an integer") from exc
if not math.isfinite(target_far) or not 0.0 <= target_far < 1.0:
    raise SystemExit("--target-far must be finite, at least 0 and below 1")
if not 1 <= top_k <= 10:
    raise SystemExit("--top-k must be between 1 and 10")
PY
manifest_container=/var/lib/relicscope/reference-library/manifest.json
index_container=/var/lib/relicscope/reference-library/index.sqlite3
evaluation_manifest_container="/var/lib/relicscope/reference-library/${EVALUATION_MANIFEST}"
calibration_input_container="/var/lib/relicscope/reference-library/${CALIBRATION_INPUT}"
calibration_container=/var/lib/relicscope/reference-library/calibration.json
compose=(docker compose --env-file "$ENV_FILE" -f compose.single.yml)

if [[ "$COMMAND" != "status" ]]; then
  reference_source="$(cfg REFERENCE_EMBEDDING_MODEL_SOURCE Qwen/Qwen3-VL-Embedding-2B)"
  export REFERENCE_EMBEDDING_MODEL_REVISION="$(cached_model_revision "$reference_source")"
fi

case "$COMMAND" in
  verify)
    [[ -f "${reference_dir}/manifest.json" ]] || die "controlled manifest is missing: ${reference_dir}/manifest.json"
    (
      cd "$PROJECT_DIR"
      "${compose[@]}" run --rm --no-deps app \
        python /opt/relicscope/scripts/import-reference-library.py \
        "$manifest_container" --media-root /var/lib/relicscope/reference-library \
        --index "$index_container" \
        --expected-reference-count "$(cfg RELICSCOPE_REFERENCE_LIBRARY_MIN_ARTIFACTS 50)" \
        --minimum-images-per-artifact "$(cfg RELICSCOPE_REFERENCE_LIBRARY_MIN_VIEWS 5)" \
        --minimum-counterfeit-records "$(cfg RELICSCOPE_COUNTERFEIT_LIBRARY_MIN_RECORDS 10)" \
        --verify-only
    )
    ;;
  import)
    [[ -f "${reference_dir}/manifest.json" ]] || die "controlled manifest is missing: ${reference_dir}/manifest.json"
    (
      cd "$PROJECT_DIR"
      "${compose[@]}" run --rm --no-deps app \
        python /opt/relicscope/scripts/import-reference-library.py \
        "$manifest_container" --media-root /var/lib/relicscope/reference-library \
        --index "$index_container" \
        --expected-reference-count "$(cfg RELICSCOPE_REFERENCE_LIBRARY_MIN_ARTIFACTS 50)" \
        --minimum-images-per-artifact "$(cfg RELICSCOPE_REFERENCE_LIBRARY_MIN_VIEWS 5)" \
        --minimum-counterfeit-records "$(cfg RELICSCOPE_COUNTERFEIT_LIBRARY_MIN_RECORDS 10)"
    )
    ;;
  build)
    [[ -f "${reference_dir}/index.sqlite3" ]] || die "metadata index is missing; run reference-import first"
    (
      cd "$PROJECT_DIR"
      "${compose[@]}" up --detach --no-build --pull never reference-embedding
    )
    wait_for_embedding
    (
      cd "$PROJECT_DIR"
      "${compose[@]}" run --rm --no-deps app /bin/sh -ec '
        service_key="$(cat /run/secrets/service_api_key)"
        if [ "${#service_key}" -lt 32 ]; then
          echo "FATAL: service API key is missing or too short" >&2
          exit 78
        fi
        export REFERENCE_EMBEDDING_API_KEY="$service_key"
        exec python /opt/relicscope/scripts/build-reference-vector-index.py \
          --metadata-index /var/lib/relicscope/reference-library/index.sqlite3 \
          --output /var/lib/relicscope/reference-library/embeddings.npz \
          --batch-size "${REFERENCE_EMBEDDING_BATCH_SIZE:-4}"
      '
    )
    ;;
  evaluate)
    [[ -f "${reference_dir}/index.sqlite3" ]] || die "metadata index is missing; run reference-import first"
    [[ -f "${reference_dir}/embeddings.npz" ]] || die "vector index is missing; run reference-build first"
    [[ -f "${reference_dir}/${EVALUATION_MANIFEST}" ]] \
      || die "frozen held-out evaluation manifest is missing: ${reference_dir}/${EVALUATION_MANIFEST}"
    (
      cd "$PROJECT_DIR"
      "${compose[@]}" up --detach --no-build --pull never reference-embedding
    )
    wait_for_embedding
    (
      cd "$PROJECT_DIR"
      "${compose[@]}" run --rm --no-deps app /bin/sh -ec '
        service_key="$(cat /run/secrets/service_api_key)"
        if [ "${#service_key}" -lt 32 ]; then
          echo "FATAL: service API key is missing or too short" >&2
          exit 78
        fi
        export REFERENCE_EMBEDDING_API_KEY="$service_key"
        exec python /opt/relicscope/scripts/evaluate-reference-recognition.py \
          "$1" --metadata-index /var/lib/relicscope/reference-library/index.sqlite3 \
          --vector-index /var/lib/relicscope/reference-library/embeddings.npz \
          --output "$2" --target-far "$3" --top-k "$4"
      ' reference-evaluate \
        "$evaluation_manifest_container" "$calibration_input_container" \
        "$TARGET_FAR" "$TOP_K"
    )
    ;;
  seal)
    [[ -f "${reference_dir}/${CALIBRATION_INPUT}" ]] \
      || die "held-out calibration input is missing: ${reference_dir}/${CALIBRATION_INPUT}"
    [[ -f "${reference_dir}/embeddings.npz" ]] || die "vector index is missing; run reference-build first"
    (
      cd "$PROJECT_DIR"
      "${compose[@]}" run --rm --no-deps app \
        python /opt/relicscope/scripts/seal-reference-calibration.py \
        "$calibration_input_container" --output "$calibration_container"
    )
    ;;
  status)
    python3 - "$reference_dir" "$EVALUATION_MANIFEST" "$CALIBRATION_INPUT" <<'PY'
import hashlib
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
evaluation_manifest = sys.argv[2]
calibration_input = sys.argv[3]
files = {
    "manifest": root / "manifest.json",
    "metadata_index": root / "index.sqlite3",
    "vector_index": root / "embeddings.npz",
    "evaluation_manifest": root / evaluation_manifest,
    "unsigned_calibration": root / calibration_input,
    "calibration": root / "calibration.json",
}
payload = {"reference_library_dir": str(root), "files": {}}
for name, path in files.items():
    item = {"present": path.is_file() and not path.is_symlink()}
    if item["present"]:
        item["bytes"] = path.stat().st_size
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        item["sha256"] = digest.hexdigest()
    payload["files"][name] = item
ready = all(item["present"] for item in payload["files"].values())
payload["deployment_gate"] = "READY_FOR_RUNTIME_VALIDATION" if ready else "CALIBRATION_REQUIRED"
payload["authenticity_state"] = "NOT_ASSESSED"
print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
PY
    ;;
esac
