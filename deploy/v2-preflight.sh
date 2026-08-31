#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_ENV_FILE:-${project_root}/.env.v2}"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

case "$(uname -m)" in
  aarch64|arm64) ;;
  *) fail "V2 hardware preflight must run on DGX Spark ARM64" ;;
esac
for command_name in awk docker git grep ip nvidia-smi python3 stat; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable to this user"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
nvidia-smi -L >/dev/null 2>&1 || fail "NVIDIA GPU is unavailable"
hardware_model=""
for model_path in /proc/device-tree/model /sys/firmware/devicetree/base/model; do
  if [[ -r "${model_path}" ]]; then
    hardware_model="$(tr -d '\000' <"${model_path}")"
    break
  fi
done
[[ "${hardware_model,,}" == *"dgx spark"* ]] \
  || fail "hardware identity is not an NVIDIA DGX Spark: ${hardware_model:-unknown}"
gpu_names="$(nvidia-smi --query-gpu=name --format=csv,noheader)"
[[ "${gpu_names,,}" == *"gb10"* ]] || fail "DGX Spark GB10 GPU identity was not verified"
[[ -f "${env_file}" ]] || fail "run deploy/v2-install.sh and review .env.v2"

cfg() {
  local key="$1"
  local fallback="${2-}"
  local value=""
  value="$(awk -v wanted="${key}" '$0 ~ "^[[:space:]]*" wanted "=" {sub("^[[:space:]]*" wanted "=", "", $0); found=$0} END {print found}' "${env_file}")"
  value="${value%$'\r'}"
  [[ "${value}" == \"*\" && "${value}" == *\" ]] && value="${value:1:${#value}-2}"
  [[ "${value}" == \'*\' && "${value}" == *\' ]] && value="${value:1:${#value}-2}"
  printf '%s' "${value:-${fallback}}"
}

absolute_path() {
  [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "${project_root}" "$1"
}

validate_managed_paths() {
  python3 "${project_root}/deploy/validate-v2-managed-paths.py" \
    --project-root "${project_root}" "$@"
}

[[ "$(cfg RELICSCOPE_OFFLINE_MODE)" == "true" ]] || fail "runtime must be restored to offline mode"
scout_bind_ip="$(cfg SCOUT_BIND_IP 127.0.0.1)"
scout_hostname="$(cfg SCOUT_HOSTNAME scout.spark.local)"
scout_port="$(cfg SCOUT_HTTPS_PORT 8443)"
python3 - "${scout_bind_ip}" "${scout_hostname}" "${scout_port}" <<'PY'
import ipaddress
import re
import sys

address = ipaddress.ip_address(sys.argv[1])
if address.version != 4 or not address.is_private or address.is_loopback:
    raise SystemExit("SCOUT_BIND_IP must be an explicit non-loopback private IPv4 address")
if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", sys.argv[2]):
    raise SystemExit("SCOUT_HOSTNAME is invalid")
port = int(sys.argv[3])
if not 1024 <= port <= 65535:
    raise SystemExit("SCOUT_HTTPS_PORT must be an unprivileged TCP port")
PY
host_ipv4="$(ip -o -4 address show | awk '{split($4, value, "/"); print value[1]}')"
grep -Fxq -- "${scout_bind_ip}" <<<"${host_ipv4}" \
  || fail "SCOUT_BIND_IP is not assigned to this DGX Spark"
max_upload_bytes="$(cfg RELICSCOPE_MAX_UPLOAD_BYTES 12582912)"
max_images="$(cfg RELICSCOPE_SCOUT_MAX_IMAGES_PER_JOB 8)"
[[ "${max_upload_bytes}" =~ ^[0-9]+$ && "${max_images}" =~ ^[0-9]+$ ]] \
  || fail "Scout upload limits must be integers"
((max_images >= 1 && max_images <= 8)) || fail "Scout image limit must be between one and eight"
((max_upload_bytes >= 1 && max_upload_bytes <= 100000000)) \
  || fail "Scout per-image byte limit is outside the safe range"
((max_upload_bytes * max_images + 1048576 <= 110000000)) \
  || fail "configured Scout upload envelope exceeds the Caddy 110MB request limit"
revision="$(cfg VISION_MODEL_REVISION)"
model="$(cfg VISION_MODEL Qwen/Qwen3-VL-30B-A3B-Instruct)"
model_source="$(cfg VISION_MODEL_SOURCE Qwen/Qwen3-VL-30B-A3B-Instruct)"
max_model_len="$(cfg VISION_MAX_MODEL_LEN 16384)"
gpu_memory_utilization="$(cfg VISION_GPU_MEMORY_UTILIZATION 0.72)"
[[ -n "${model}" && "${model}" != *[[:space:]]* ]] \
  || fail "VISION_MODEL must be a non-empty model identity without whitespace"
python3 - "${max_model_len}" "${gpu_memory_utilization}" <<'PY'
import math
import sys

max_len = int(sys.argv[1])
memory = float(sys.argv[2])
if not 4096 <= max_len <= 131072:
    raise SystemExit("VISION_MAX_MODEL_LEN must be between 4096 and 131072")
if not math.isfinite(memory) or not 0.10 <= memory <= 0.95:
    raise SystemExit("VISION_GPU_MEMORY_UTILIZATION must be between 0.10 and 0.95")
PY
[[ "${revision}" =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]] || fail "VISION_MODEL_REVISION must be an immutable commit"
[[ "${revision}" == "${revision,,}" ]] \
  || fail "VISION_MODEL_REVISION must be lowercase; rerun online preparation"
[[ "${model_source}" == "${model}" ]] \
  || fail "VISION_MODEL_SOURCE must equal the V2 vLLM model identity"
[[ "$(cfg APP_UID)" == "$(id -u)" && "$(cfg APP_GID)" == "$(id -g)" ]] \
  || fail "APP_UID/APP_GID must match the non-root operator; rerun deploy/v2-install.sh"

source_commit="$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}')"
[[ -z "$(git -C "${project_root}" status --porcelain --untracked-files=all)" ]] \
  || fail "deployment source tree must be clean"
[[ "$(cfg RELICSCOPE_GIT_COMMIT)" == "${source_commit}" ]] \
  || fail "RELICSCOPE_GIT_COMMIT does not match the checked-out source"
[[ -x "${project_root}/.venv-v2/bin/python" ]] \
  || fail "V2 host tools are missing; rerun online preparation"
"${project_root}/.venv-v2/bin/python" -c \
  'import hashlib, httpx, PIL, pydantic; assert hasattr(hashlib, "scrypt"); assert httpx.__version__ == "0.28.1"; assert PIL.__version__ == "11.3.0"; assert pydantic.__version__ == "2.11.7"' \
  || fail "V2 host tools cannot provide HTTPS, image proof reproduction, schema validation or device-token hashing"

secret_file="$(absolute_path "$(cfg SERVICE_API_KEY_FILE ./secrets/service_api_key)")"
[[ -f "${secret_file}" && ! -L "${secret_file}" && -s "${secret_file}" ]] \
  || fail "service API key must be a non-symlink regular file"
permissions="$(stat -c '%a' "${secret_file}")"
[[ "${permissions}" == "600" || "${permissions}" == "400" ]] \
  || fail "service API key permissions must be 600 or 400"
python3 - "${secret_file}" <<'PY'
import pathlib
import re
import sys

value = pathlib.Path(sys.argv[1]).read_text(encoding="ascii").strip()
if not re.fullmatch(r"[A-Za-z0-9._~-]{32,256}", value):
    raise SystemExit("service API key must be one safe ASCII token of 32-256 characters")
PY

data_dir="$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/v2-data)")"
hf_cache="$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")"
vllm_cache="$(absolute_path "$(cfg VLLM_CACHE_DIR ./runtime/vllm-cache)")"
caddy_data="$(absolute_path "$(cfg CADDY_DATA_DIR ./runtime/caddy/data)")"
caddy_config="$(absolute_path "$(cfg CADDY_CONFIG_DIR ./runtime/caddy/config)")"
minimum_free_bytes="$(cfg RELICSCOPE_SCOUT_MIN_FREE_BYTES 21474836480)"
validate_managed_paths \
  "${data_dir}" "${hf_cache}" "${vllm_cache}" \
  "${caddy_data}" "${caddy_config}" "${secret_file}"
[[ "${minimum_free_bytes}" =~ ^[0-9]+$ ]] \
  || fail "RELICSCOPE_SCOUT_MIN_FREE_BYTES must be an integer"
for writable_dir in "${data_dir}" "${vllm_cache}" "${caddy_data}" "${caddy_config}"; do
  [[ -d "${writable_dir}" && -w "${writable_dir}" ]] || fail "required writable directory is unavailable: ${writable_dir}"
  [[ "$(stat -c '%u:%g' "${writable_dir}")" == "$(id -u):$(id -g)" ]] \
    || fail "directory owner does not match APP_UID/APP_GID: ${writable_dir}"
done
python3 - "${data_dir}" "${minimum_free_bytes}" <<'PY'
import shutil
import sys

path, minimum = sys.argv[1], int(sys.argv[2])
free = shutil.disk_usage(path).free
if free < minimum:
    raise SystemExit(f"Scout data volume has {free} free bytes; requires {minimum}")
PY
[[ -d "${hf_cache}" ]] || fail "Hugging Face cache is missing"
model_cache_name="models--${model//\//--}"
snapshot_dir="${hf_cache}/hub/${model_cache_name}/snapshots/${revision}"
[[ -d "${snapshot_dir}" ]] || fail "the exact configured model revision is not cached: ${snapshot_dir}"

python_image="$(cfg PYTHON_IMAGE python:3.12.11-slim-bookworm)"
vllm_image="$(cfg VLLM_IMAGE nvcr.io/nvidia/vllm:26.05.post1-py3)"
caddy_image="$(cfg CADDY_IMAGE caddy:2.10.2-alpine)"
gateway_image="$(cfg SCOUT_GATEWAY_IMAGE relicscope-scout-gateway:2.0.0-arm64)"
[[ "${vllm_image}" =~ @sha256:[0-9a-fA-F]{64}$ ]] \
  || fail "VLLM_IMAGE must be pinned by registry digest; rerun online preparation"
[[ "${caddy_image}" =~ @sha256:[0-9a-fA-F]{64}$ ]] \
  || fail "CADDY_IMAGE must be pinned by registry digest; rerun online preparation"
[[ "${python_image}" =~ @sha256:[0-9a-fA-F]{64}$ ]] \
  || fail "PYTHON_IMAGE must be pinned by registry digest; rerun online preparation"
for image in "${python_image}" "${vllm_image}" "${caddy_image}" "${gateway_image}"; do
  docker image inspect "${image}" >/dev/null 2>&1 || fail "required container image is not cached: ${image}"
done
gateway_commit="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${gateway_image}")"
[[ "${gateway_commit}" == "${source_commit}" ]] \
  || fail "gateway image was not built from the checked-out source commit"
docker run --rm \
  --platform linux/arm64 \
  --user "$(id -u):$(id -g)" \
  --gpus all \
  --network none \
  --read-only \
  --tmpfs /tmp:size=256m,mode=1777 \
  --env HOME=/tmp \
  --env HF_HOME=/model-cache \
  --env HF_HUB_OFFLINE=1 \
  --env TRANSFORMERS_OFFLINE=1 \
  --mount "type=bind,source=${hf_cache},target=/model-cache,readonly" \
  --entrypoint python \
  "${vllm_image}" \
  -c '
import pathlib
import sys
import torch
from huggingface_hub import snapshot_download

repo_id, revision = sys.argv[1:3]
assert torch.cuda.is_available(), "CUDA is unavailable"
assert "GB10" in torch.cuda.get_device_name(0).upper(), torch.cuda.get_device_name(0)
resolved = pathlib.Path(snapshot_download(
    repo_id=repo_id,
    revision=revision,
    cache_dir="/model-cache/hub",
    local_files_only=True,
))
assert resolved.name.lower() == revision.lower(), resolved
assert any(path.is_file() for path in resolved.rglob("*")), resolved
' "${model}" "${revision}" >/dev/null \
  || fail "the pinned vLLM container cannot resolve the complete offline model on GB10"

manifest="${project_root}/runtime/preparation/v2-runtime-manifest.txt"
[[ -s "${manifest}" && "$(stat -c '%a' "${manifest}")" == "600" ]] \
  || fail "the private V2 preparation manifest is missing or has unsafe permissions"
gateway_image_id="$(docker image inspect --format '{{.Id}}' "${gateway_image}")"
gateway_image_user="$(docker image inspect --format '{{.Config.User}}' "${gateway_image}")"
expected_gateway_user="$(cfg APP_UID):$(cfg APP_GID)"
[[ "${gateway_image_user}" == "${expected_gateway_user}" ]] \
  || fail "gateway image does not run as the configured non-root identity"
python3 - "${manifest}" "${source_commit}" "${model}" "${revision}" \
  "${max_model_len}" "${gpu_memory_utilization}" \
  "${python_image}" "${vllm_image}" "${caddy_image}" "${gateway_image}" \
  "${gateway_image_id}" "${gateway_image_user}" <<'PY'
import pathlib
import sys

(
    path, commit, model, revision, max_model_len, gpu_memory_utilization,
    python_image, vllm_image, caddy_image,
    gateway_image, gateway_image_id, gateway_image_user,
) = sys.argv[1:]
lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
required = {
    f"source_commit={commit}",
    f"model={model}",
    f"model_revision={revision}",
    f"max_model_len={max_model_len}",
    f"gpu_memory_utilization={gpu_memory_utilization}",
    f"python_image={python_image}",
    f"vllm_image={vllm_image}",
    f"caddy_image={caddy_image}",
    f"gateway_image={gateway_image}",
    f"gateway_image_id={gateway_image_id}",
    f"gateway_image_user={gateway_image_user}",
}
missing = required.difference(lines)
if missing:
    raise SystemExit(f"V2 preparation manifest mismatch: {sorted(missing)}")
PY

compose_json="$(
  docker compose --env-file "${env_file}" -f "${project_root}/compose.v2.yml" config --format json
)"
COMPOSE_JSON="${compose_json}" python3 - \
  "${model}" "${revision}" "${source_commit}" "$(id -u):$(id -g)" \
  "${max_model_len}" "${gpu_memory_utilization}" <<'PY'
import json
import os
import sys

(
    expected_model, expected_revision, expected_commit, expected_user,
    max_model_len, gpu_memory_utilization,
) = sys.argv[1:]
config = json.loads(os.environ["COMPOSE_JSON"])
services = config["services"]
gateway = services["gateway"]
vision = services["vision"]
ingress = services["ingress"]
assert not gateway.get("ports"), "gateway must not publish a host port"
assert not vision.get("ports"), "model service must not publish a host port"
assert ingress.get("ports"), "HTTPS ingress must publish the Scout port"
assert "vision" not in (gateway.get("depends_on") or {}), "gateway must survive model downtime"
assert vision.get("read_only") is True and gateway.get("read_only") is True
assert vision.get("cap_drop") == ["ALL"] and gateway.get("cap_drop") == ["ALL"]
assert vision.get("user") == expected_user, "vision must run as the non-root operator"
assert vision.get("ipc") != "host", "vision must not share host IPC"
assert vision.get("gpus") or vision.get("deploy"), "vision GPU request is missing"
assert all(service.get("pull_policy") == "never" for service in services.values())
assert gateway["environment"]["RELICSCOPE_OFFLINE_MODE"] == "true"
assert gateway["environment"]["VISION_MODEL"] == expected_model
assert gateway["environment"]["VISION_MODEL_SOURCE"] == expected_model
assert gateway["environment"]["VISION_MODEL_REVISION"] == expected_revision
assert gateway["environment"]["VISION_RUNTIME_IMAGE"] == vision["image"]
assert gateway["environment"]["RELICSCOPE_GIT_COMMIT"] == expected_commit
assert str(vision["environment"]["VISION_MAX_MODEL_LEN"]) == max_model_len
assert str(vision["environment"]["VISION_GPU_MEMORY_UTILIZATION"]) == gpu_memory_utilization
gateway_networks = set(gateway["networks"])
vision_networks = set(vision["networks"])
ingress_networks = set(ingress["networks"])
assert gateway_networks == {"gateway-private", "model-private"}
assert vision_networks == {"model-private"}
assert "gateway-private" in ingress_networks
for name in ("gateway-private", "model-private"):
    assert config["networks"][name].get("internal") is True, f"{name} must be internal"
PY

printf '%s\n' \
  'PASS: DGX Spark ARM64, GPU, Docker, immutable source/model, local cache,' \
  'non-root storage, secrets, offline flags and isolated V2 Compose validated.'
