#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_ENV_FILE:-${project_root}/.env.v2.nim}"
fail() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }

[[ "$(uname -s)" == "Linux" ]] || fail "run preflight on the DGX Spark"
case "$(uname -m)" in aarch64|arm64) ;; *) fail "DGX Spark ARM64 is required" ;; esac
for command_name in docker find git grep head ip nvidia-smi python3 sed stat tr; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
[[ -f "${env_file}" ]] || fail ".env.v2.nim is missing"
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable"
nvidia-smi -L >/dev/null 2>&1 || fail "NVIDIA GPU is unavailable"
hardware_model="$(tr -d '\000' </proc/device-tree/model 2>/dev/null || true)"
[[ "${hardware_model,,}" == *"dgx spark"* ]] || fail "host is not identified as DGX Spark"
gpu_names="$(nvidia-smi --query-gpu=name --format=csv,noheader)"
[[ "${gpu_names,,}" == *"gb10"* ]] || fail "GB10 GPU identity was not verified"

driver_version="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1)"
docker_version="$(docker version --format '{{.Server.Version}}')"
compose_version="$(docker compose version --short)"
python3 - "${driver_version}" "${docker_version}" "${compose_version}" <<'PY'
import re
import sys


def version(value: str, label: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", value)
    if not match:
        raise SystemExit(f"could not parse {label} version: {value!r}")
    return tuple(int(part or 0) for part in match.groups())


driver, docker, compose = sys.argv[1:]
if version(driver, "NVIDIA driver") < (580, 0, 0):
    raise SystemExit(f"NVIDIA driver 580+ is required; detected {driver}")
if version(docker, "Docker") < (24, 0, 0):
    raise SystemExit(f"Docker 24+ is required; detected {docker}")
if version(compose, "Docker Compose") < (2, 30, 0):
    raise SystemExit(f"Docker Compose 2.30+ is required; detected {compose}")
print(f"Runtime versions: driver={driver}; docker={docker}; compose={compose}")
PY

cfg() {
  python3 "${project_root}/deploy/read-v2-env.py" \
    --file "${env_file}" --key "$1" --default "${2-}"
}
absolute_path() { [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "${project_root}" "$1"; }

transport_mode="$(cfg SCOUT_TRANSPORT_MODE private_lan)"
scout_bind_ip="$(cfg SCOUT_BIND_IP 127.0.0.1)"
python3 - "${transport_mode}" "${scout_bind_ip}" <<'PY'
import ipaddress
import json
import subprocess
import sys

mode, raw_ip = sys.argv[1:]
try:
    address = ipaddress.ip_address(raw_ip)
except ValueError as exc:
    raise SystemExit(f"SCOUT_BIND_IP is not a literal IP address: {raw_ip!r}") from exc

if mode == "private_lan":
    networks = tuple(
        ipaddress.ip_network(value)
        for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    )
    if address.version != 4 or not any(address in network for network in networks):
        raise SystemExit("private_lan requires an explicit RFC1918 IPv4 SCOUT_BIND_IP")
    payload = json.loads(subprocess.check_output(["ip", "-j", "address", "show"]))
    present = {
        entry.get("local")
        for interface in payload
        for entry in interface.get("addr_info", [])
    }
    if raw_ip not in present:
        raise SystemExit(f"SCOUT_BIND_IP is not assigned to this Spark: {raw_ip}")
elif mode == "adb_reverse":
    if not address.is_loopback:
        raise SystemExit("adb_reverse requires a loopback SCOUT_BIND_IP")
else:
    raise SystemExit("SCOUT_TRANSPORT_MODE must be private_lan or adb_reverse")
print(f"Scout transport: mode={mode}; bind={raw_ip}")
PY

[[ "$(cfg RELICSCOPE_OFFLINE_MODE)" == "true" ]] || fail "runtime must be in offline mode"
[[ "$(cfg NIM_DISABLE_MODEL_DOWNLOAD)" == "1" ]] || fail "NIM model download must be disabled"
profile="$(cfg NIM_MODEL_PROFILE)"
[[ "${profile}" =~ ^[0-9a-f]{64}$ ]] || fail "NIM_MODEL_PROFILE must be a lowercase 64-character ID"
[[ "$(cfg VISION_MODEL_REVISION)" == "${profile}" ]] \
  || fail "VISION_MODEL_REVISION must bind the same NIM profile"
vision_model="$(cfg VISION_MODEL)"
vision_source="$(cfg VISION_MODEL_SOURCE)"
nim_served_model="$(cfg NIM_SERVED_MODEL_NAME)"
[[ -n "${vision_model}" && "${vision_model}" == "${vision_source}" ]] \
  || fail "VISION_MODEL and VISION_MODEL_SOURCE must be identical and non-empty"
[[ "${vision_model}" == "${nim_served_model}" ]] \
  || fail "NIM_SERVED_MODEL_NAME must equal the gateway model identity"
[[ "$(cfg NIM_MAX_VIDEOS_PER_PROMPT 0)" == "0" ]] || fail "phase-one video must stay disabled"
gateway_images="$(cfg RELICSCOPE_SCOUT_MAX_IMAGES_PER_JOB 8)"
nim_images="$(cfg NIM_MAX_IMAGES_PER_PROMPT 8)"
[[ "${gateway_images}" =~ ^[0-9]+$ && "${nim_images}" =~ ^[0-9]+$ ]] \
  || fail "Scout and NIM image limits must be integers"
((gateway_images >= 1 && gateway_images <= nim_images && nim_images <= 8)) \
  || fail "image limits must satisfy 1 <= Scout job limit <= NIM prompt limit <= 8"

source_commit="$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}')"
[[ -z "$(git -C "${project_root}" status --porcelain --untracked-files=all)" ]] \
  || fail "deployment source tree must be clean"
[[ "$(cfg RELICSCOPE_GIT_COMMIT)" == "${source_commit}" ]] \
  || fail "configured source commit differs from checkout"

nim_image="$(cfg NIM_VLM_IMAGE)"
python_image="$(cfg PYTHON_IMAGE)"
caddy_image="$(cfg CADDY_IMAGE)"
[[ "${nim_image}" =~ ^nvcr\.io/nim/qwen/qwen3\.6-35b-a3b@sha256:[0-9a-fA-F]{64}$ ]] \
  || fail "NIM_VLM_IMAGE must be a digest-pinned image from the approved Qwen3.6 NVIDIA NIM repository"
for image in "${nim_image}" "${python_image}" "${caddy_image}"; do
  [[ "${image}" =~ @sha256:[0-9a-fA-F]{64}$ ]] || fail "runtime image is not digest-pinned: ${image}"
  docker image inspect "${image}" >/dev/null 2>&1 || fail "runtime image is not local: ${image}"
done

min_available_memory="$(cfg NIM_PREFLIGHT_MIN_AVAILABLE_MEMORY_BYTES 68719476736)"
[[ "${min_available_memory}" =~ ^[0-9]+$ && "${min_available_memory}" -gt 0 ]] \
  || fail "NIM_PREFLIGHT_MIN_AVAILABLE_MEMORY_BYTES must be a positive integer"
python3 - "${min_available_memory}" <<'PY'
import pathlib
import re
import sys

minimum = int(sys.argv[1])
text = pathlib.Path("/proc/meminfo").read_text(encoding="ascii")
match = re.search(r"^MemAvailable:\s+(\d+)\s+kB$", text, flags=re.MULTILINE)
if not match:
    raise SystemExit("could not read MemAvailable from /proc/meminfo")
available = int(match.group(1)) * 1024
if available < minimum:
    raise SystemExit(
        f"insufficient available unified memory: need {minimum} bytes, have {available}"
    )
print(f"Available unified memory before startup: {available} bytes")
PY
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader \
  2>/dev/null | sed 's/^/Existing GPU process: /' || true
gateway_image="$(cfg SCOUT_GATEWAY_IMAGE relicscope-scout-gateway:2.1.0-arm64)"
docker image inspect "${gateway_image}" >/dev/null 2>&1 || fail "gateway image is not local"
gateway_commit="$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "${gateway_image}")"
[[ "${gateway_commit}" == "${source_commit}" ]] || fail "gateway image commit differs from checkout"

nim_cache="$(absolute_path "$(cfg NIM_CACHE_DIR ./runtime/nim-cache)")"
data_dir="$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/v2-data)")"
caddy_data="$(absolute_path "$(cfg CADDY_DATA_DIR ./runtime/caddy/data)")"
caddy_config="$(absolute_path "$(cfg CADDY_CONFIG_DIR ./runtime/caddy/config)")"
secret_file="$(absolute_path "$(cfg SERVICE_API_KEY_FILE ./secrets/service_api_key)")"
python3 "${project_root}/deploy/validate-v2-managed-paths.py" --project-root "${project_root}" \
  "${nim_cache}" "${data_dir}" "${caddy_data}" "${caddy_config}" "${secret_file}"
[[ -d "${nim_cache}" && ! -L "${nim_cache}" ]] || fail "NIM cache is unavailable"
find "${nim_cache}" -type f -print -quit | grep -q . || fail "NIM cache is empty"
[[ -f "${secret_file}" && ! -L "${secret_file}" ]] || fail "service key is unavailable"
permissions="$(stat -c '%a' "${secret_file}")"
[[ "${permissions}" == "600" || "${permissions}" == "400" ]] || fail "service key permissions are unsafe"

profiles="$({ docker run --rm --platform linux/arm64 --runtime=nvidia --gpus all \
  --network none "${nim_image}" list-model-profiles; } 2>&1)" \
  || fail "NIM profile compatibility probe failed"
grep -Fq -- "${profile}" <<<"${profiles}" || fail "frozen profile is not compatible with this Spark"

compose_json="$(docker compose --env-file "${env_file}" -f "${project_root}/compose.v2.nim.yml" config --format json)"
COMPOSE_JSON="${compose_json}" python3 - \
  "${profile}" "${nim_image}" "${vision_model}" "${gateway_images}" "${nim_images}" <<'PY'
import json, os, sys
profile, image, model, gateway_images, nim_images = sys.argv[1:]
services = json.loads(os.environ["COMPOSE_JSON"])["services"]
gateway, vision, ingress = (services[name] for name in ("gateway", "vision", "ingress"))
assert not gateway.get("ports"), "gateway must not publish a host port"
assert not vision.get("ports"), "NIM must not publish a host port"
assert ingress.get("ports"), "HTTPS ingress must publish the Scout port"
assert vision["image"] == image and vision.get("pull_policy") == "never"
assert not vision.get("user"), "Qwen3.6 Spark NIM does not support a custom container user"
assert not vision.get("privileged", False), "NIM must not run privileged"
assert not vision.get("pid") == "host", "NIM must not share the host PID namespace"
assert not vision.get("ipc") == "host", "NIM must not share host IPC"
env = vision["environment"]
assert env["NIM_MODEL_PROFILE"] == profile
assert env["NIM_SERVED_MODEL_NAME"] == model
assert str(env["NIM_DISABLE_MODEL_DOWNLOAD"]) == "1"
assert str(env["NIM_MAX_IMAGES_PER_PROMPT"]) == nim_images
assert str(env["NIM_MAX_VIDEOS_PER_PROMPT"]) == "0"
assert "NGC_API_KEY" not in env and "HF_TOKEN" not in env
assert {"model-private"} == set(vision["networks"])
assert {"gateway-private", "model-private"} == set(gateway["networks"])
assert services["gateway"]["environment"]["VISION_MODEL_REVISION"] == profile
assert services["gateway"]["environment"]["VISION_MODEL"] == model
assert str(services["gateway"]["environment"]["RELICSCOPE_SCOUT_MAX_IMAGES_PER_JOB"]) == gateway_images
volumes = vision.get("volumes") or []
cache = next(v for v in volumes if v.get("target") == "/opt/nim/.cache")
assert not cache.get("read_only"), "NIM cache must stay writable for this container variant"
PY

manifest="${project_root}/runtime/preparation/v2-nim-runtime-manifest.txt"
[[ -s "${manifest}" && "$(stat -c '%a' "${manifest}")" == "600" ]] \
  || fail "private preparation manifest is missing or unsafe"
grep -Fxq "source_commit=${source_commit}" "${manifest}" || fail "manifest source commit differs"
grep -Fxq "nim_profile=${profile}" "${manifest}" || fail "manifest NIM profile differs"
grep -Fxq "nim_image=${nim_image}" "${manifest}" || fail "manifest NIM image differs"
printf '%s\n' 'PASS: DGX Spark, frozen source, private cache, NIM profile, images and network boundary are ready.'
