#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
ROLE="all"
OFFLINE=0
OUTPUT_DIR=""

usage() {
  printf '%s\n' \
    "Usage: $0 [--role spark-a|spark-b|single|all] [--offline] [--output-dir DIR]" \
    "Default: source/deployment release only. --offline additionally exports" \
    "the role's cached container images and a model-requirements manifest." \
    "Third-party model weights/data, secrets, .env and runtime evidence are excluded."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    --offline) OFFLINE=1; shift ;;
    --output-dir)
      (($# >= 2)) || die "--output-dir requires a value"
      OUTPUT_DIR="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
case "$ROLE" in spark-a|spark-b|single|all) ;; *) die "invalid role: ${ROLE}" ;; esac
for command_name in awk tar sha256sum mktemp; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command not found: ${command_name}"
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
absolute_path() { [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "$PROJECT_DIR" "$1"; }

release_version="$(cfg RELICSCOPE_RELEASE_VERSION 1.1.0)"
[[ "$release_version" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid RELICSCOPE_RELEASE_VERSION"
output_dir="$(absolute_path "${OUTPUT_DIR:-$(cfg PACKAGE_DIR ./runtime/packages)}")"
[[ "$output_dir" != "/" && "$output_dir" != "$PROJECT_DIR" ]] || die "unsafe package directory: ${output_dir}"
install -d -m 700 -- "$output_dir"
staging="$(mktemp -d "${output_dir}/.relicscope-package.XXXXXX")"

cleanup() {
  if [[ "$staging" == "${output_dir}/.relicscope-package."* && -d "$staging" ]]; then
    rm -rf -- "$staging"
  fi
}
trap cleanup EXIT

release_archive="${staging}/relicscope-release-${release_version}.tar.gz"
entries=(
  .agents .github .dockerignore .env.example .gitattributes .gitignore
  AGENTS.md Dockerfile Makefile NOTICE.md README.md THIRD_PARTY_NOTICES.md
  requirements.txt requirements.lock requirements-dev.txt requirements-dev.lock pytest.ini
  app data demo_media deploy docs openspec scripts tests
  compose.yml compose.single.yml run_local.sh
)
existing_entries=()
for entry in "${entries[@]}"; do
  [[ -e "${PROJECT_DIR}/${entry}" ]] && existing_entries+=("$entry")
done
tar -C "$PROJECT_DIR" \
  --exclude='.pytest_cache' --exclude='.ruff_cache' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='.env' --exclude='secrets' --exclude='runtime' \
  -czf "$release_archive" "${existing_entries[@]}"

{
  printf 'release_version=%s\n' "$release_version"
  printf 'generated_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'role=%s\n' "$ROLE"
  printf 'offline_payload=%s\n' "$OFFLINE"
  printf 'application_service_version=%s\n' "$(cfg RELICSCOPE_SERVICE_VERSION 1.1.0)"
  printf 'contains_env=false\n'
  printf 'contains_secrets=false\n'
  printf 'contains_runtime_evidence=false\n'
  printf 'contains_third_party_model_weights=false\n'
  printf 'contains_third_party_datasets=false\n'
  printf 'nvidia_target_mapping=planned-and-partial-current-stack-see-docs/RUNTIME_BOUNDARY.md\n'
} >"${staging}/MANIFEST.txt"

if [[ "$OFFLINE" == "1" ]]; then
  [[ -f "$ENV_FILE" ]] || die "--offline requires a configured .env"
  command -v docker >/dev/null 2>&1 || die "Docker is required for --offline"
  docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"
  app_image="$(cfg APP_IMAGE relicscope-ai-demo:1.1.0-arm64)"
  vllm_image="$(cfg VLLM_IMAGE nvcr.io/nvidia/vllm:26.05.post1-py3)"
  images=()
  models=()
  case "$ROLE" in
    spark-a)
      images+=("$vllm_image")
      models+=("$(cfg VISION_MODEL nvidia/Qwen2.5-VL-7B-Instruct-NVFP4)")
      [[ "$(cfg EMBEDDING_ENABLED 0)" == "1" ]] && models+=("$(cfg EMBEDDING_MODEL Qwen/Qwen3-VL-Embedding-2B)")
      ;;
    spark-b)
      images+=("$app_image")
      if [[ "$(cfg REASONER_ENABLED 0)" == "1" ]]; then
        images+=("$vllm_image")
        models+=("$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)")
      fi
      ;;
    single|all)
      images+=("$app_image" "$vllm_image")
      models+=("$(cfg VISION_MODEL nvidia/Qwen2.5-VL-7B-Instruct-NVFP4)")
      [[ "$(cfg EMBEDDING_ENABLED 0)" == "1" ]] && models+=("$(cfg EMBEDDING_MODEL Qwen/Qwen3-VL-Embedding-2B)")
      [[ "$(cfg REASONER_ENABLED 0)" == "1" ]] && models+=("$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)")
      ;;
  esac
  for image in "${images[@]}"; do
    docker image inspect "$image" >/dev/null 2>&1 || die "offline image is not cached: ${image}"
    {
      printf 'container_image=%s\n' "$image"
      printf 'container_image_id=%s@%s\n' "$image" "$(docker image inspect --format '{{.Id}}' "$image")"
    } >>"${staging}/MANIFEST.txt"
  done
  docker save --output "${staging}/container-images.tar" "${images[@]}"

  hf_cache="$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")"
  {
    printf '%s\n' \
      '# Model requirements only — no weights are redistributed in this bundle.' \
      '# Obtain each model through an institution-approved channel after license,' \
      '# geography and redistribution review, then place it under HF_CACHE_DIR.'
  } >"${staging}/MODEL_REQUIREMENTS.txt"
  for model in "${models[@]}"; do
    cache_name="models--${model//\//--}"
    model_root=""
    if [[ -d "${hf_cache}/hub/${cache_name}" ]]; then
      model_root="${hf_cache}/hub/${cache_name}"
    elif [[ -d "${hf_cache}/${cache_name}" ]]; then
      model_root="${hf_cache}/${cache_name}"
    fi
    printf 'model=%s\n' "$model" >>"${staging}/MANIFEST.txt"
    printf 'model_id=%s\n' "$model" >>"${staging}/MODEL_REQUIREMENTS.txt"
    if [[ -n "$model_root" && -f "${model_root}/refs/main" ]]; then
      revision="$(tr -d '\r\n' <"${model_root}/refs/main")"
      printf 'model_revision=%s@%s\n' "$model" "$revision" >>"${staging}/MANIFEST.txt"
      printf 'observed_cached_revision=%s\n' "$revision" >>"${staging}/MODEL_REQUIREMENTS.txt"
    else
      printf 'model_revision=%s@unknown\n' "$model" >>"${staging}/MANIFEST.txt"
      printf 'observed_cached_revision=unknown\n' >>"${staging}/MODEL_REQUIREMENTS.txt"
    fi
    printf 'redistributed=false\n\n' >>"${staging}/MODEL_REQUIREMENTS.txt"
  done
fi

(
  cd "$staging"
  sha256sum relicscope-release-*.tar.gz MANIFEST.txt >SHA256SUMS
  if [[ "$OFFLINE" == "1" ]]; then
    sha256sum container-images.tar MODEL_REQUIREMENTS.txt >>SHA256SUMS
  fi
)

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
kind="release"
[[ "$OFFLINE" == "1" ]] && kind="offline"
bundle="${output_dir}/relicscope-${kind}-${ROLE}-${release_version}-${timestamp}.tar.gz"
tar -C "$staging" -czf "$bundle" .
chmod 600 "$bundle"
bundle_name="${bundle##*/}"
(
  cd "$output_dir"
  sha256sum "$bundle_name" >"${bundle_name}.sha256"
  chmod 600 "${bundle_name}.sha256"
)

cleanup
trap - EXIT
printf 'Package complete: %s\n' "$bundle"
printf 'Checksum: %s\n' "${bundle}.sha256"
if [[ "$OFFLINE" == "1" ]]; then
  printf '%s\n' 'Offline payload contains container images and model requirements, but no third-party model weights/data, credentials or runtime evidence.'
fi
