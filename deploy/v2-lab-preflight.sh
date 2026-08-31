#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_LAB_ENV_FILE:-${project_root}/.env.v2.lab}"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

[[ "$(uname -s)" == "Linux" ]] || fail "lab preflight must run on DGX Spark Linux"
case "$(uname -m)" in
  aarch64|arm64) ;;
  *) fail "lab preflight requires ARM64" ;;
esac
[[ "$(id -u)" != "0" ]] || fail "run preflight as the non-root Spark operator"
for command_name in awk docker git grep ip nvidia-smi python3 stat tr; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
docker info >/dev/null 2>&1 || fail "Docker is unavailable to this user"
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
  || fail "hardware identity is not NVIDIA DGX Spark: ${hardware_model:-unknown}"
gpu_names="$(nvidia-smi --query-gpu=name --format=csv,noheader)"
[[ "${gpu_names,,}" == *"gb10"* ]] || fail "the DGX Spark GB10 GPU identity was not verified"
[[ -f "${env_file}" ]] || fail "run deploy/v2-lab-install.sh and review .env.v2.lab"

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

[[ "$(cfg LAB_OFFLINE_MODE)" == "true" ]] \
  || fail "LAB_OFFLINE_MODE must be true before preflight and runtime"
lab_bind_ip="$(cfg LAB_BIND_IP 127.0.0.1)"
lab_port="$(cfg LAB_HTTPS_PORT 8444)"
python3 - "${lab_bind_ip}" "${lab_port}" <<'PY'
import ipaddress
import sys

address = ipaddress.ip_address(sys.argv[1])
if address.version != 4 or (not address.is_loopback and not address.is_private):
    raise SystemExit("LAB_BIND_IP must be loopback or an explicit private IPv4 address")
port = int(sys.argv[2])
if not 1024 <= port <= 65535:
    raise SystemExit("LAB_HTTPS_PORT must be an unprivileged TCP port")
PY
if [[ "${lab_bind_ip}" != "127.0.0.1" ]]; then
  host_ipv4="$(ip -o -4 address show | awk '{split($4, value, "/"); print value[1]}')"
  grep -Fxq -- "${lab_bind_ip}" <<<"${host_ipv4}" \
    || fail "LAB_BIND_IP is not assigned to this DGX Spark"
fi

model="$(cfg LAB_MODEL)"
model_profile="$(cfg LAB_MODEL_PROFILE nemotron3-nano-omni)"
revision="$(cfg LAB_MODEL_REVISION)"
max_model_len="$(cfg LAB_MAX_MODEL_LEN 32768)"
gpu_memory_utilization="$(cfg LAB_GPU_MEMORY_UTILIZATION 0.70)"
max_batched_tokens="$(cfg LAB_MAX_NUM_BATCHED_TOKENS 32768)"
video_fps="$(cfg LAB_VIDEO_FPS 2)"
video_max_frames="$(cfg LAB_VIDEO_MAX_FRAMES 128)"
[[ -n "${model}" && "${model}" != *[[:space:]]* ]] || fail "LAB_MODEL is invalid"
[[ "${model_profile}" == "nemotron3-nano-omni" || "${model_profile}" == "generic-vlm" ]] \
  || fail "LAB_MODEL_PROFILE must be nemotron3-nano-omni or generic-vlm"
if [[ "${model_profile}" == "nemotron3-nano-omni" ]]; then
  [[ "${model}" == nvidia/Nemotron-3-Nano-Omni-* ]] \
    || fail "the nemotron3-nano-omni profile requires the NVIDIA Nemotron 3 Nano Omni model family"
fi
python3 - "${max_model_len}" "${gpu_memory_utilization}" \
  "${max_batched_tokens}" "${video_fps}" "${video_max_frames}" <<'PY'
import math
import sys

max_len = int(sys.argv[1])
memory = float(sys.argv[2])
batched = int(sys.argv[3])
fps = float(sys.argv[4])
frames = int(sys.argv[5])
if not 4096 <= max_len <= 131072:
    raise SystemExit("LAB_MAX_MODEL_LEN must be between 4096 and 131072")
if not math.isfinite(memory) or not 0.10 <= memory <= 0.95:
    raise SystemExit("LAB_GPU_MEMORY_UTILIZATION must be between 0.10 and 0.95")
if not 1024 <= batched <= max_len:
    raise SystemExit("LAB_MAX_NUM_BATCHED_TOKENS must be between 1024 and LAB_MAX_MODEL_LEN")
if not math.isfinite(fps) or not 0.1 <= fps <= 10:
    raise SystemExit("LAB_VIDEO_FPS must be between 0.1 and 10")
if not 1 <= frames <= 512:
    raise SystemExit("LAB_VIDEO_MAX_FRAMES must be between 1 and 512")
PY
[[ "${revision}" =~ ^[0-9a-fA-F]{40}$|^[0-9a-fA-F]{64}$ ]] \
  || fail "LAB_MODEL_REVISION must be a 40- or 64-character immutable commit"
[[ "$(cfg LAB_UID)" == "$(id -u)" && "$(cfg LAB_GID)" == "$(id -g)" ]] \
  || fail "LAB_UID/LAB_GID must match the non-root operator"
source_commit="$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}')"
[[ -z "$(git -C "${project_root}" status --porcelain --untracked-files=all)" ]] \
  || fail "lab deployment requires a clean checked-out source tree"
[[ "$(cfg RELICSCOPE_LAB_GIT_COMMIT)" == "${source_commit}" ]] \
  || fail "RELICSCOPE_LAB_GIT_COMMIT does not match the checked-out source"

[[ -x "${project_root}/.venv-v2/bin/python" ]] \
  || fail ".venv-v2 is missing; rerun the approved online preparation"
"${project_root}/.venv-v2/bin/python" -c \
  'import httpx, PIL; assert httpx.__version__ == "0.28.1"; assert PIL.__version__ == "11.3.0"' \
  || fail "the host benchmark environment lacks pinned httpx/Pillow"

hf_cache="$(absolute_path "$(cfg LAB_HF_CACHE_DIR ./runtime/lab-hf-cache)")"
vllm_cache="$(absolute_path "$(cfg LAB_VLLM_CACHE_DIR ./runtime/lab-vllm-cache)")"
caddy_data="$(absolute_path "$(cfg LAB_CADDY_DATA_DIR ./runtime/lab-caddy/data)")"
caddy_config="$(absolute_path "$(cfg LAB_CADDY_CONFIG_DIR ./runtime/lab-caddy/config)")"
secret_file="$(absolute_path "$(cfg LAB_API_KEY_FILE ./secrets/lab_api_key)")"
validate_managed_paths \
  "${hf_cache}" "${vllm_cache}" "${caddy_data}" "${caddy_config}" \
  "${secret_file}"
[[ -f "${secret_file}" && ! -L "${secret_file}" && -s "${secret_file}" ]] \
  || fail "lab API key must be a non-symlink regular file"
[[ "$(stat -c '%a' "${secret_file}")" == "600" ]] \
  || fail "lab API key permissions must be exactly 600"
[[ "$(stat -c '%u:%g' "${secret_file}")" == "$(id -u):$(id -g)" ]] \
  || fail "lab API key owner does not match LAB_UID/LAB_GID"
python3 - "${secret_file}" <<'PY'
import pathlib
import re
import sys

value = pathlib.Path(sys.argv[1]).read_text(encoding="ascii").strip()
if not re.fullmatch(r"[A-Za-z0-9._~-]{32,256}", value):
    raise SystemExit("lab API key must be a single safe ASCII token of 32-256 characters")
PY

for writable_dir in "${vllm_cache}" "${caddy_data}" "${caddy_config}"; do
  [[ -d "${writable_dir}" && ! -L "${writable_dir}" && -w "${writable_dir}" ]] \
    || fail "required writable directory is unavailable: ${writable_dir}"
  [[ "$(stat -c '%u:%g' "${writable_dir}")" == "$(id -u):$(id -g)" ]] \
    || fail "directory owner does not match LAB_UID/LAB_GID: ${writable_dir}"
done
[[ -d "${hf_cache}" && ! -L "${hf_cache}" ]] \
  || fail "the lab Hugging Face cache is missing or is a symlink"
[[ "$(stat -c '%u:%g' "${hf_cache}")" == "$(id -u):$(id -g)" ]] \
  || fail "model cache owner does not match LAB_UID/LAB_GID"
model_cache_name="models--${model//\//--}"
snapshot_dir="${hf_cache}/hub/${model_cache_name}/snapshots/${revision,,}"
[[ -d "${snapshot_dir}" ]] \
  || fail "the exact configured model revision is not cached: ${snapshot_dir}"

vllm_image="$(cfg LAB_VLLM_IMAGE)"
caddy_image="$(cfg LAB_CADDY_IMAGE)"
[[ "${vllm_image}" =~ @sha256:[0-9a-fA-F]{64}$ ]] \
  || fail "LAB_VLLM_IMAGE must be pinned by registry digest"
[[ "${caddy_image}" =~ @sha256:[0-9a-fA-F]{64}$ ]] \
  || fail "LAB_CADDY_IMAGE must be pinned by registry digest"
for image in "${vllm_image}" "${caddy_image}"; do
  docker image inspect "${image}" >/dev/null 2>&1 \
    || fail "required container image is not cached: ${image}"
done

docker run --rm \
  --platform linux/arm64 \
  --user "$(id -u):$(id -g)" \
  --gpus all \
  --network none \
  --read-only \
  --tmpfs /tmp:size=512m,mode=1777 \
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
import vllm
from huggingface_hub import snapshot_download

repo_id, revision, profile = sys.argv[1:4]
assert torch.cuda.is_available(), "CUDA is unavailable"
assert "GB10" in torch.cuda.get_device_name(0).upper(), torch.cuda.get_device_name(0)
if profile == "nemotron3-nano-omni":
    assert vllm.__version__ == "0.20.0", vllm.__version__
resolved = pathlib.Path(snapshot_download(
    repo_id=repo_id,
    revision=revision,
    cache_dir="/model-cache/hub",
    local_files_only=True,
))
assert resolved.name.lower() == revision.lower(), resolved
assert any(path.is_file() for path in resolved.rglob("*")), resolved
' "${model}" "${revision}" "${model_profile}" >/dev/null \
  || fail "offline container validation of GB10 or the exact model cache failed"

manifest="${project_root}/runtime/lab-preparation/runtime-manifest.txt"
[[ -s "${manifest}" && "$(stat -c '%a' "${manifest}")" == "600" ]] \
  || fail "the private lab preparation manifest is missing or has unsafe permissions"
python3 - "${manifest}" "${source_commit}" "${model_profile}" "${model}" "${revision}" \
  "${max_model_len}" "${gpu_memory_utilization}" "${max_batched_tokens}" \
  "${video_fps}" "${video_max_frames}" "${vllm_image}" "${caddy_image}" <<'PY'
import pathlib
import sys

(
    path, commit, model_profile, model, revision, max_model_len,
    gpu_memory_utilization, max_batched_tokens, video_fps, video_max_frames,
    vllm_image, caddy_image,
) = sys.argv[1:]
lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
required = {
    f"source_commit={commit}",
    f"model_profile={model_profile}",
    f"model={model}",
    f"model_revision={revision}",
    f"max_model_len={max_model_len}",
    f"gpu_memory_utilization={gpu_memory_utilization}",
    f"max_batched_tokens={max_batched_tokens}",
    f"video_fps={video_fps}",
    f"video_max_frames={video_max_frames}",
    f"container_image={vllm_image}",
    f"container_image={caddy_image}",
}
missing = required.difference(lines)
if missing:
    raise SystemExit(f"lab preparation manifest mismatch: {sorted(missing)}")
PY

compose_json="$(
  docker compose --env-file "${env_file}" -f "${project_root}/compose.v2.lab.yml" config --format json
)"
COMPOSE_JSON="${compose_json}" python3 - \
  "${model_profile}" "${model}" "${revision}" "$(id -u):$(id -g)" \
  "${lab_bind_ip}" "${lab_port}" "${max_model_len}" \
  "${gpu_memory_utilization}" "${max_batched_tokens}" \
  "${video_fps}" "${video_max_frames}" <<'PY'
import json
import os
import sys

(
    expected_profile, expected_model, expected_revision, expected_user,
    bind_ip, port, max_model_len, gpu_memory_utilization,
    max_batched_tokens, video_fps, video_max_frames,
) = sys.argv[1:]
config = json.loads(os.environ["COMPOSE_JSON"])
services = config["services"]
vision = services["lab-vision"]
ingress = services["lab-ingress"]
assert not vision.get("ports"), "the model service must not publish a host port"
ports = ingress.get("ports") or []
assert len(ports) == 1, "HTTPS ingress must publish exactly one host port"
published = ports[0]
assert str(published.get("host_ip")) == bind_ip
assert str(published.get("published")) == port
assert str(published.get("target")) == port
assert vision.get("user") == expected_user and ingress.get("user") == expected_user
assert vision.get("read_only") is True and ingress.get("read_only") is True
assert vision.get("cap_drop") == ["ALL"] and ingress.get("cap_drop") == ["ALL"]
assert vision.get("ipc") != "host", "the lab model must not share host IPC"
assert vision.get("gpus") or vision.get("deploy"), "GB10 GPU request is missing"
assert all(service.get("pull_policy") == "never" for service in services.values())
assert vision["environment"]["LAB_MODEL_PROFILE"] == expected_profile
assert vision["environment"]["LAB_MODEL"] == expected_model
assert vision["environment"]["LAB_MODEL_REVISION"] == expected_revision
assert str(vision["environment"]["LAB_MAX_MODEL_LEN"]) == max_model_len
assert str(vision["environment"]["LAB_GPU_MEMORY_UTILIZATION"]) == gpu_memory_utilization
assert str(vision["environment"]["LAB_MAX_NUM_BATCHED_TOKENS"]) == max_batched_tokens
assert str(vision["environment"]["LAB_VIDEO_FPS"]) == video_fps
assert str(vision["environment"]["LAB_VIDEO_MAX_FRAMES"]) == video_max_frames
assert vision["environment"]["HF_HUB_OFFLINE"] == "1"
assert vision["environment"]["TRANSFORMERS_OFFLINE"] == "1"
assert set(vision["networks"]) == {"lab-private"}
assert set(ingress["networks"]) == {"lab-edge", "lab-private"}
assert config["networks"]["lab-private"].get("internal") is True
assert not config["networks"]["lab-edge"].get("internal", False)
assert "lab-vision" not in (ingress.get("depends_on") or {})
PY

printf '%s\n' \
  'PASS: DGX Spark ARM64/GB10, clean source, non-root identity, exact offline model cache,' \
  '600-mode secret, digest-pinned images, pull-never policy and private model network validated.'
