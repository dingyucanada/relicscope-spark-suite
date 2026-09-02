#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077
export COPYFILE_DISABLE=1

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
for command_name in awk git tar sha256sum mktemp; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command not found: ${command_name}"
done

required_nim_source_files=(
  .env.v2.nim.example
  compose.v2.nim.yml
  deploy/v2-nim-list-profiles.sh
  deploy/v2-nim-prepare-online.sh
  deploy/v2-nim-preflight.sh
)
for required in "${required_nim_source_files[@]}"; do
  [[ -f "${PROJECT_DIR}/${required}" ]] \
    || die "single-Spark NIM source artifact is missing: ${required}"
done
for executable in \
  deploy/v2-nim-list-profiles.sh deploy/v2-nim-prepare-online.sh \
  deploy/v2-nim-preflight.sh; do
  [[ -x "${PROJECT_DIR}/${executable}" ]] \
    || die "single-Spark NIM script is not executable: ${executable}"
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

git_root="$(git -C "$PROJECT_DIR" rev-parse --show-toplevel 2>/dev/null)" \
  || die "release packaging requires a Git worktree with an immutable HEAD"
[[ "$git_root" == "$PROJECT_DIR" ]] \
  || die "package script must run from the repository root: ${PROJECT_DIR}"
source_commit="$(git -C "$PROJECT_DIR" rev-parse --verify 'HEAD^{commit}')" \
  || die "unable to resolve the release source commit"
[[ "$source_commit" =~ ^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$ ]] \
  || die "release source commit is not an immutable 40- or 64-hex revision"
[[ -z "$(git -C "$PROJECT_DIR" status --porcelain --untracked-files=all)" ]] \
  || die "release source tree is not clean; commit or remove tracked and untracked changes before packaging"

release_version="$(cfg RELICSCOPE_RELEASE_VERSION 1.2.0)"
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
  .agents .github .dockerignore .env.example .env.v2.example .env.v2.lab.example .env.v2.nim.example .gitattributes .gitignore
  AGENTS.md Dockerfile Dockerfile.vllm Dockerfile.embedding Makefile NOTICE.md README.md THIRD_PARTY_NOTICES.md
  requirements.txt requirements.lock requirements-embedding.lock requirements-dev.txt requirements-dev.lock pytest.ini
  app demo_media deploy docs embedding_server openspec scout-android scripts tests
  data/knowledge_manifest.json data/reference_library/README.md
  data/reference_library/manifest.schema.json data/reference_library/evaluation-manifest.schema.json
  compose.yml compose.single.yml compose.v2.yml compose.v2.lab.yml compose.v2.nim.yml run_local.sh
)
existing_entries=()
for entry in "${entries[@]}"; do
  [[ -e "${PROJECT_DIR}/${entry}" ]] && existing_entries+=("$entry")
done
git -C "$PROJECT_DIR" archive \
  --format=tar.gz \
  --output "$release_archive" \
  "$source_commit" -- "${existing_entries[@]}"

# The source release intentionally carries only public synthetic demo material and
# data-contract schemas. Reject runtime/customer data, secrets and model payloads
# even if a future commit accidentally places them below another packaged tree.
while IFS= read -r archived_path; do
  archived_path="${archived_path#./}"
  case "$archived_path" in
    data/|data/reference_library/|data/knowledge_manifest.json|\
    data/reference_library/README.md|data/reference_library/manifest.schema.json|\
    data/reference_library/evaluation-manifest.schema.json)
      ;;
    data/*)
      die "non-allowlisted data entered the source archive: ${archived_path}"
      ;;
  esac
  case "/${archived_path}" in
    */runtime/*|*/secrets/*|*/work/*|*/nim-cache/*|*/hf-cache/*|*/vllm-cache/*)
      die "private runtime path entered the source archive: ${archived_path}"
      ;;
  esac
  archive_name="${archived_path##*/}"
  case "$archive_name" in
    .env|.env.v2|.env.v2.nim|ngc_api_key|service_api_key)
      die "credential-bearing file entered the source archive: ${archived_path}"
      ;;
  esac
  case "$archived_path" in
    *.safetensors|*.gguf|*.ckpt|*.pt|*.pth|*.onnx|*.bin|*.engine|*.plan|*.nemo|*.tflite|*.mlmodel)
      die "model weight entered the source archive: ${archived_path}"
      ;;
  esac
done < <(tar -tzf "$release_archive")

{
  printf 'release_version=%s\n' "$release_version"
  printf 'generated_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'source_commit=%s\n' "$source_commit"
  printf 'source_tree_clean=true\n'
  printf 'source_archive=git-object\n'
  printf 'role=%s\n' "$ROLE"
  printf 'offline_payload=%s\n' "$OFFLINE"
  printf 'application_service_version=%s\n' "$(cfg RELICSCOPE_SERVICE_VERSION 1.2.0)"
  printf 'contains_env=false\n'
  printf 'contains_secrets=false\n'
  printf 'contains_runtime_evidence=false\n'
  printf 'contains_third_party_model_weights=false\n'
  printf 'contains_third_party_datasets=false\n'
  printf 'contains_controlled_reference_media=false\n'
  printf 'contains_expert_or_counterfeit_evidence=false\n'
  printf 'contains_private_artwork_data=false\n'
  printf 'contains_ngc_credentials=false\n'
  printf 'contains_nim_cache_or_model_weights=false\n'
  printf 'single_spark_nim_source_path=included\n'
  printf 'single_spark_nim_offline_payload=not-included\n'
  printf 'nvidia_target_mapping=deployment-ready-hardware-acceptance-pending-see-docs/RUNTIME_BOUNDARY.md\n'
} >"${staging}/MANIFEST.txt"

if [[ "$OFFLINE" == "1" ]]; then
  [[ -f "$ENV_FILE" ]] || die "--offline requires a configured .env"
  command -v docker >/dev/null 2>&1 || die "Docker is required for --offline"
  docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"
  app_image="$(cfg APP_IMAGE relicscope-ai-demo:1.2.0-arm64)"
  vllm_image="$(cfg VLLM_IMAGE relicscope-multimodal-vllm:0.20.0-arm64)"
  reference_embedding_image="$(cfg REFERENCE_EMBEDDING_IMAGE relicscope-reference-embedding:1.0.0-arm64)"
  images=()
  models=()
  case "$ROLE" in
    spark-a)
      images+=("$vllm_image")
      models+=("$(cfg VISION_MODEL_SOURCE Qwen/Qwen3-VL-30B-A3B-Instruct)")
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
      models+=("$(cfg VISION_MODEL_SOURCE Qwen/Qwen3-VL-30B-A3B-Instruct)")
      if [[ "$(cfg PREFETCH_AB_MODELS 1)" == "1" ]]; then
        models+=("$(cfg AB_NEMOTRON_MODEL_SOURCE nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4)")
      fi
      if [[ "$(cfg PREFETCH_REFERENCE_EMBEDDING 1)" == "1" ]]; then
        images+=("$reference_embedding_image")
        models+=("$(cfg REFERENCE_EMBEDDING_MODEL_SOURCE Qwen/Qwen3-VL-Embedding-2B)")
      fi
      [[ "$(cfg EMBEDDING_ENABLED 0)" == "1" ]] && models+=("$(cfg EMBEDDING_MODEL Qwen/Qwen3-VL-Embedding-2B)")
      [[ "$(cfg REASONER_ENABLED 0)" == "1" ]] && models+=("$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)")
      ;;
  esac
  for image in "${images[@]}"; do
    docker image inspect "$image" >/dev/null 2>&1 || die "offline image is not cached: ${image}"
    image_id="$(docker image inspect --format '{{.Id}}' "$image")"
    image_source_commit="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")"
    [[ "$image_id" =~ ^sha256:[0-9A-Fa-f]{64}$ ]] \
      || die "offline image does not expose an immutable image ID: ${image}"
    [[ "$image_source_commit" == "$source_commit" ]] \
      || die "offline image was not built from release commit ${source_commit}: ${image}"
    {
      printf 'container_image=%s\n' "$image"
      printf 'container_image_id=%s@%s\n' "$image" "$image_id"
      printf 'container_image_source_commit=%s@%s\n' "$image" "$image_source_commit"
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
      [[ "$revision" =~ ^([0-9A-Fa-f]{40}|[0-9A-Fa-f]{64})$ ]] \
        || die "cached model revision is not immutable for ${model}"
      printf 'model_revision=%s@%s\n' "$model" "$revision" >>"${staging}/MANIFEST.txt"
      printf 'observed_cached_revision=%s\n' "$revision" >>"${staging}/MODEL_REQUIREMENTS.txt"
    else
      die "offline package requires an immutable cached model revision for ${model}"
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
