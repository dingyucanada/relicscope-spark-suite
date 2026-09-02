#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_ENV_FILE:-${project_root}/.env.v2.nim}"
allow_network=0
ngc_key_file=""

fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
usage() {
  printf '%s\n' \
    'Usage: v2-nim-list-profiles.sh [--allow-network --ngc-key-file FILE]' \
    'Without --allow-network, the digest-pinned NIM image must already be local.'
}
while (($#)); do
  case "$1" in
    --allow-network) allow_network=1; shift ;;
    --ngc-key-file)
      [[ $# -ge 2 ]] || fail "--ngc-key-file needs a path"
      ngc_key_file="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown option: $1" ;;
  esac
done
[[ "$(uname -s)" == "Linux" ]] || fail "run this command on the DGX Spark"
case "$(uname -m)" in aarch64|arm64) ;; *) fail "DGX Spark ARM64 is required" ;; esac
for command_name in chmod docker mktemp python3 rm stat tr; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
[[ -f "${env_file}" ]] || fail "run make v2-nim-install first"

cfg() {
  python3 "${project_root}/deploy/read-v2-env.py" \
    --file "${env_file}" --key "$1" --default "${2-}"
}

image="$(cfg NIM_VLM_IMAGE nvcr.io/nim/qwen/qwen3.6-35b-a3b:1.7.1-variant)"
[[ "${image}" =~ ^nvcr\.io/nim/qwen/qwen3\.6-35b-a3b:(1\.7\.1-variant)$ \
   || "${image}" =~ ^nvcr\.io/nim/qwen/qwen3\.6-35b-a3b@sha256:[0-9a-fA-F]{64}$ ]] \
  || fail "NIM_VLM_IMAGE must be the approved Qwen3.6 NVIDIA NIM repository and release"
if [[ "${allow_network}" == "1" ]]; then
  [[ -n "${ngc_key_file}" && -f "${ngc_key_file}" && ! -L "${ngc_key_file}" ]] \
    || fail "a non-symlink --ngc-key-file is required in the online window"
  permissions="$(stat -c '%a' "${ngc_key_file}")"
  [[ "${permissions}" == "600" || "${permissions}" == "400" ]] \
    || fail "NGC key file permissions must be 600 or 400"
  ngc_key="$(tr -d '\r\n' <"${ngc_key_file}")"
  [[ "${#ngc_key}" -ge 16 ]] || fail "NGC key file is empty or malformed"
  docker_config="$(mktemp -d "${TMPDIR:-/tmp}/relicscope-nvcr.XXXXXX")"
  chmod 700 "${docker_config}"
  cleanup_registry() {
    if [[ -n "${docker_config:-}" && -d "${docker_config}" ]]; then
      DOCKER_CONFIG="${docker_config}" docker logout nvcr.io >/dev/null 2>&1 || true
      case "${docker_config}" in
        "${TMPDIR:-/tmp}"/relicscope-nvcr.*) rm -rf -- "${docker_config}" ;;
      esac
    fi
    unset ngc_key
  }
  trap cleanup_registry EXIT
  printf '%s' "${ngc_key}" \
    | DOCKER_CONFIG="${docker_config}" docker login nvcr.io \
        --username '$oauthtoken' --password-stdin >/dev/null
  unset ngc_key
  DOCKER_CONFIG="${docker_config}" docker pull "${image}"
fi
docker image inspect "${image}" >/dev/null 2>&1 \
  || fail "NIM image is not local; rerun with --allow-network in the approved window"

printf '%s\n' \
  'Compatible profiles reported by the selected NIM on this exact GPU:' \
  'Copy one 64-character profile ID into NIM_MODEL_PROFILE in .env.v2.nim.'
docker run --rm \
  --platform linux/arm64 \
  --runtime=nvidia \
  --gpus all \
  --network none \
  "${image}" list-model-profiles
