#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
ROLE="all"
RESTORE_APP=0
SINGLE=0

usage() {
  printf '%s\n' \
    "Usage: $0 [--role spark-a|spark-b|single|all] [--restore-app]" \
    "Stops only RelicScope containers. Persistent session data and model caches" \
    "are always preserved. --restore-app requires PREVIOUS_APP_IMAGE."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    --restore-app) RESTORE_APP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$ROLE" in
  spark-a|spark-b|single|all) ;;
  *) die "invalid role: ${ROLE}" ;;
esac

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

prepare_reference_embedding_revision() {
  local configured source cache_dir cache_name root revision container_id
  configured="$(cfg REFERENCE_EMBEDDING_MODEL_REVISION '')"
  if [[ "$configured" =~ ^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$ ]]; then
    export REFERENCE_EMBEDDING_MODEL_REVISION="${configured,,}"
    return
  fi
  source="$(cfg REFERENCE_EMBEDDING_MODEL_SOURCE Qwen/Qwen3-VL-Embedding-2B)"
  cache_dir="$(cfg HF_CACHE_DIR ./runtime/hf-cache)"
  [[ "$cache_dir" == /* ]] || cache_dir="${PROJECT_DIR}/${cache_dir}"
  cache_name="models--${source//\//--}"
  for root in "${cache_dir}/hub/${cache_name}" "${cache_dir}/${cache_name}"; do
    [[ -f "${root}/refs/main" ]] || continue
    revision="$(tr -d '\r\n' <"${root}/refs/main")"
    if [[ "$revision" =~ ^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$ ]]; then
      export REFERENCE_EMBEDDING_MODEL_REVISION="${revision,,}"
      return
    fi
  done
  container_id="$(docker container ls --all \
    --filter label=com.docker.compose.service=reference-embedding \
    --format '{{.ID}}' | head -n 1)"
  if [[ -n "$container_id" ]]; then
    revision="$(docker inspect --format '{{index .Config.Labels "ai.relicscope.model.revision"}}' "$container_id" 2>/dev/null || true)"
    if [[ "$revision" =~ ^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$ ]]; then
      export REFERENCE_EMBEDDING_MODEL_REVISION="${revision,,}"
      return
    fi
  fi
  return 1
}

stop_known_container() {
  local name="$1"
  if docker container inspect "$name" >/dev/null 2>&1; then
    docker container rm --force "$name" >/dev/null
    printf 'Stopped and removed container: %s\n' "$name"
  else
    printf 'Container already absent: %s\n' "$name"
  fi
}

command -v docker >/dev/null 2>&1 || die "Docker is required"
docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"

if [[ "$ROLE" == "spark-a" || "$ROLE" == "all" ]]; then
  stop_known_container relicscope-vision
  stop_known_container relicscope-embedding
fi

if [[ "$ROLE" == "spark-b" || "$ROLE" == "all" ]]; then
  if [[ -f "$ENV_FILE" ]]; then
    (
      cd "$PROJECT_DIR"
      docker compose --env-file "$ENV_FILE" -f compose.yml \
        --profile reasoner down --remove-orphans
    )
  fi
  stop_known_container relicscope-reasoner
fi

if [[ "$ROLE" == "single" || "$ROLE" == "all" ]]; then
  SINGLE=1
  if [[ -f "$ENV_FILE" ]]; then
    if prepare_reference_embedding_revision; then
      (
        cd "$PROJECT_DIR"
        docker compose --env-file "$ENV_FILE" -f compose.single.yml \
          down --remove-orphans
      )
    elif [[ "$ROLE" == "single" ]]; then
      die "cannot resolve the immutable reference embedding revision required to stop the single-Spark Compose project"
    else
      printf '%s\n' 'Single-Spark Compose teardown skipped: no immutable reference embedding revision/cache/container was found.' >&2
    fi
  fi
fi

if [[ "$RESTORE_APP" == "1" ]]; then
  [[ "$ROLE" == "spark-b" || "$ROLE" == "single" ]] \
    || die "--restore-app requires --role spark-b or --role single"
  [[ -f "$ENV_FILE" ]] || die "environment file is missing: ${ENV_FILE}"
  previous_image="$(cfg PREVIOUS_APP_IMAGE '')"
  [[ -n "$previous_image" ]] || die "PREVIOUS_APP_IMAGE is empty"
  [[ "$previous_image" == *@sha256:* || ( "$previous_image" == *:* && "$previous_image" != *:latest ) ]] \
    || die "PREVIOUS_APP_IMAGE must use a digest or fixed non-latest tag"
  docker image inspect "$previous_image" >/dev/null 2>&1 \
    || die "previous application image is not cached locally: ${previous_image}"
  compose_file="compose.yml"
  [[ "$SINGLE" == "1" ]] && compose_file="compose.single.yml"
  (
    cd "$PROJECT_DIR"
    APP_IMAGE="$previous_image" OFFLINE_RUNTIME=1 \
      docker compose --env-file "$ENV_FILE" -f "$compose_file" \
        up --detach --no-build --pull never app
  )
  printf 'Restored application image: %s\n' "$previous_image"
fi

printf '%s\n' 'Preserved: runtime/data, runtime/hf-cache, runtime/vllm-cache, images, and all source files.'
printf '%s\n' 'No persistent volume, session database, knowledge file, or model weight was deleted.'
