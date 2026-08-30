#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
ROLE="all"
CHECK_RUNNING=0
REQUIRE_VISION=0
REQUIRE_EMBEDDING=0
REQUIRE_REASONER=0
MODEL_REQUIREMENTS_EXPLICIT=0
SKIP_HARDWARE_CHECKS="${SKIP_HARDWARE_CHECKS:-0}"

usage() {
  printf '%s\n' \
    "Usage: $0 [--role spark-a|spark-b|single|all] [model requirements] [--check-running]" \
    "Model requirements: --require-vision --require-embedding --require-reasoner" \
    "Validates configuration and locks runtime directory permissions. It never downloads images or models."
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

warn() {
  printf 'WARN: %s\n' "$*" >&2
}

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    --check-running)
      CHECK_RUNNING=1
      shift
      ;;
    --require-vision)
      REQUIRE_VISION=1
      MODEL_REQUIREMENTS_EXPLICIT=1
      shift
      ;;
    --require-embedding)
      REQUIRE_EMBEDDING=1
      MODEL_REQUIREMENTS_EXPLICIT=1
      shift
      ;;
    --require-reasoner)
      REQUIRE_REASONER=1
      MODEL_REQUIREMENTS_EXPLICIT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
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
  if [[ -n "$direct" ]]; then
    printf '%s' "$direct"
    return
  fi
  if [[ -f "$ENV_FILE" ]]; then
    value="$(awk -v wanted="$key" '
      $0 ~ "^[[:space:]]*" wanted "=" {
        sub("^[[:space:]]*" wanted "=", "", $0)
        found=$0
      }
      END { print found }
    ' "$ENV_FILE")"
    value="${value%$'\r'}"
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
  fi
  printf '%s' "${value:-$fallback}"
}

absolute_path() {
  local value="$1"
  if [[ "$value" == /* ]]; then
    printf '%s' "$value"
  else
    printf '%s/%s' "$PROJECT_DIR" "$value"
  fi
}

safe_managed_dir() {
  python3 - "$1" "$PROJECT_DIR" <<'PY'
import os
import sys

value, project = sys.argv[1:3]
path = os.path.realpath(value)
forbidden = {
    "/", "/home", "/opt", "/srv", "/tmp", "/usr", "/var",
    os.path.realpath(os.path.expanduser("~")), os.path.realpath(project),
}
if path in forbidden:
    raise SystemExit(f"refusing broad managed-directory path: {path}")
print(path)
PY
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

check_secret_file() {
  local path="$1"
  [[ -f "$path" ]] || die "secret file is missing: ${path}"
  python3 - "$path" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
raw = open(path, "rb").read().strip()
if len(raw) < 32:
    raise SystemExit("secret must contain at least 32 bytes")
lower = raw.lower()
for marker in (b"changeme", b"replace-me", b"replace_me", b"example-key", b"demo-key"):
    if marker in lower:
        raise SystemExit("secret contains an example/default marker and is rejected")
mode = stat.S_IMODE(os.stat(path).st_mode)
if mode & 0o077:
    raise SystemExit(f"secret permissions are too broad: {mode:03o}; run chmod 600")
PY
}

check_private_value() {
  local label="$1"
  local value="$2"
  local cidrs="$3"
  [[ -n "$value" ]] || return 0
  python3 - "$label" "$value" "$cidrs" <<'PY'
import ipaddress
import sys
from urllib.parse import urlparse

label, value, cidr_text = sys.argv[1:4]
if "://" in value:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise SystemExit(f"{label}: endpoint must be http(s) without embedded credentials")
    host = parsed.hostname
else:
    host = value
if not host:
    raise SystemExit(f"{label}: invalid address")
if host in {"localhost", "vision", "reasoner", "embedding"}:
    raise SystemExit(0)
try:
    address = ipaddress.ip_address(host)
except ValueError:
    raise SystemExit(f"{label}: DNS hostnames are not accepted by preflight; use a pinned private IP")
networks = [ipaddress.ip_network(item.strip()) for item in cidr_text.split(",") if item.strip()]
if address.is_loopback or address.is_link_local or any(address in network for network in networks):
    raise SystemExit(0)
raise SystemExit(f"{label}: public or unapproved address rejected: {host}")
PY
}

check_cache_for_model() {
  local cache_dir="$1"
  local model_id="$2"
  local model_cache="models--${model_id//\//--}"
  local model_root=""
  local revision=""
  for candidate in "${cache_dir}/hub/${model_cache}" "${cache_dir}/${model_cache}"; do
    [[ -f "${candidate}/refs/main" ]] || continue
    revision="$(<"${candidate}/refs/main")"
    [[ "$revision" =~ ^[a-fA-F0-9]{7,64}$ ]] || continue
    [[ -f "${candidate}/snapshots/${revision}/config.json" ]] || continue
    model_root="$candidate"
    break
  done
  [[ -n "$model_root" ]] \
    || die "offline model cache is missing or incomplete: ${model_id}; run deploy/prefetch.sh during the approved preparation window"
}

check_openai_endpoint_auth_model() {
  local label="$1"
  local base_url="$2"
  local key_path="$3"
  local expected_model="$4"
  [[ -n "$base_url" ]] || die "${label}: endpoint is empty"
  python3 - "$label" "$base_url" "$key_path" "$expected_model" <<'PY'
import json
import sys
import urllib.error
import urllib.request

label, base_url, key_path, expected_model = sys.argv[1:5]
base_url = base_url.rstrip("/")
url = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"
try:
    urllib.request.urlopen(url, timeout=4).read()
except urllib.error.HTTPError as exc:
    if exc.code not in {401, 403}:
        raise SystemExit(f"{label}: unexpected unauthenticated status: {exc.code}")
else:
    raise SystemExit(f"{label}: /v1 endpoint accepted a request without an API key")

key = open(key_path, "rb").read().strip().decode("utf-8")
request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
with urllib.request.urlopen(request, timeout=5) as response:
    payload = json.load(response)
model_ids = {item.get("id") for item in payload.get("data", [])}
if expected_model not in model_ids:
    raise SystemExit(
        f"{label}: endpoint is ready but does not report expected model {expected_model}"
    )
PY
}

check_compose_endpoint_auth_model() {
  local label="$1"
  local compose_file="$2"
  local service="$3"
  local base_url="$4"
  local expected_model="$5"
  local container_id=""
  container_id="$({
    cd "$PROJECT_DIR"
    docker compose --env-file "$ENV_FILE" -f "$compose_file" \
      --profile "$service" ps --status running -q "$service"
  })"
  [[ -n "$container_id" ]] || die "${label}: required Compose service is not running"
  docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null \
    | grep -qx healthy || die "${label}: Compose service is not healthy"
  (
    cd "$PROJECT_DIR"
    docker compose --env-file "$ENV_FILE" -f "$compose_file" \
      --profile "$service" exec -T app \
      python - "$label" "$base_url" "$expected_model" <<'PY'
import json
import sys
import urllib.error
import urllib.request

label, base_url, expected_model = sys.argv[1:4]
base_url = base_url.rstrip("/")
url = f"{base_url}/models" if base_url.endswith("/v1") else f"{base_url}/v1/models"
try:
    urllib.request.urlopen(url, timeout=4).read()
except urllib.error.HTTPError as exc:
    if exc.code not in {401, 403}:
        raise SystemExit(f"{label}: unexpected unauthenticated status: {exc.code}")
else:
    raise SystemExit(f"{label}: /v1 endpoint accepted a request without an API key")

key = open("/run/secrets/service_api_key", "rb").read().strip().decode("utf-8")
request = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
with urllib.request.urlopen(request, timeout=5) as response:
    payload = json.load(response)
model_ids = {item.get("id") for item in payload.get("data", [])}
if expected_model not in model_ids:
    raise SystemExit(
        f"{label}: endpoint is ready but does not report expected model {expected_model}"
    )
PY
  )
}

require_command awk
require_command docker
require_command python3

max_upload_bytes="$(cfg RELICSCOPE_MAX_UPLOAD_BYTES 8388608)"
max_video_bytes="$(cfg RELICSCOPE_MAX_VIDEO_BYTES 268435456)"
max_video_frames="$(cfg RELICSCOPE_MAX_VIDEO_FRAMES 12)"
max_frame_bytes="$(cfg RELICSCOPE_MAX_FRAME_BYTES 2097152)"
for item in \
  "RELICSCOPE_MAX_UPLOAD_BYTES:${max_upload_bytes}" \
  "RELICSCOPE_MAX_VIDEO_BYTES:${max_video_bytes}" \
  "RELICSCOPE_MAX_VIDEO_FRAMES:${max_video_frames}" \
  "RELICSCOPE_MAX_FRAME_BYTES:${max_frame_bytes}"; do
  key="${item%%:*}"
  value="${item#*:}"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || ((value <= 0)); then
    die "${key} must be a positive integer"
  fi
done
((max_video_bytes >= max_frame_bytes)) \
  || die "RELICSCOPE_MAX_VIDEO_BYTES must not be smaller than RELICSCOPE_MAX_FRAME_BYTES"
((max_frame_bytes <= max_upload_bytes)) \
  || die "RELICSCOPE_MAX_FRAME_BYTES must not exceed RELICSCOPE_MAX_UPLOAD_BYTES"
((max_video_frames >= 3 && max_video_frames <= 24)) \
  || die "RELICSCOPE_MAX_VIDEO_FRAMES must be between 3 and 24"

if [[ "$SKIP_HARDWARE_CHECKS" != "1" ]]; then
  [[ "$(uname -s)" == "Linux" ]] || die "DGX Spark deployment requires Linux"
  case "$(uname -m)" in
    aarch64|arm64) ;;
    *) die "DGX Spark deployment requires ARM64; detected $(uname -m)" ;;
  esac
  require_command nvidia-smi
  nvidia-smi >/dev/null 2>&1 || die "nvidia-smi failed"
fi

docker info >/dev/null 2>&1 || die "Docker daemon is unavailable"
compose_version="$(docker compose version --short 2>/dev/null)" \
  || die "Docker Compose 2.30 or newer is unavailable"
python3 - "$compose_version" <<'PY'
import re
import sys

match = re.search(r"(\d+)\.(\d+)", sys.argv[1])
if not match or tuple(map(int, match.groups())) < (2, 30):
    raise SystemExit(f"Docker Compose 2.30 or newer is required; detected {sys.argv[1]!r}")
PY

service_key_file="$(absolute_path "$(cfg SERVICE_API_KEY_FILE ./secrets/service_api_key)")"
check_secret_file "$service_key_file" || die "invalid service API key file"

allowed_cidrs="$(cfg ALLOWED_PRIVATE_CIDRS '10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,169.254.0.0/16')"
check_private_value SPARK_A_IP "$(cfg SPARK_A_IP '')" "$allowed_cidrs"
check_private_value SPARK_B_IP "$(cfg SPARK_B_IP '')" "$allowed_cidrs"
check_private_value SPARK_A_BIND_IP "$(cfg SPARK_A_BIND_IP '')" "$allowed_cidrs"
check_private_value SPARK_B_BIND_IP "$(cfg SPARK_B_BIND_IP '')" "$allowed_cidrs"
check_private_value APP_BIND_IP "$(cfg APP_BIND_IP '127.0.0.1')" "$allowed_cidrs"
check_private_value VISION_BASE_URL "$(cfg VISION_BASE_URL '')" "$allowed_cidrs"
check_private_value EMBEDDING_BASE_URL "$(cfg EMBEDDING_BASE_URL '')" "$allowed_cidrs"
check_private_value REASONER_BASE_URL "$(cfg REASONER_BASE_URL '')" "$allowed_cidrs"
check_private_value SINGLE_VISION_BASE_URL "$(cfg SINGLE_VISION_BASE_URL '')" "$allowed_cidrs"
check_private_value SINGLE_EMBEDDING_BASE_URL "$(cfg SINGLE_EMBEDDING_BASE_URL '')" "$allowed_cidrs"
check_private_value SINGLE_REASONER_BASE_URL "$(cfg SINGLE_REASONER_BASE_URL '')" "$allowed_cidrs"

data_dir="$(safe_managed_dir "$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/data)")")"
cache_dir="$(safe_managed_dir "$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")")"
vllm_cache_dir="$(safe_managed_dir "$(absolute_path "$(cfg VLLM_CACHE_DIR ./runtime/vllm-cache)")")"
mkdir -p -- "$data_dir" "$cache_dir" "$vllm_cache_dir"
chmod 700 "$data_dir" "$cache_dir" "$vllm_cache_dir"

app_uid="$(cfg APP_UID "$(id -u)")"
app_gid="$(cfg APP_GID "$(id -g)")"
if [[ "$ROLE" == "spark-b" || "$ROLE" == "single" || "$ROLE" == "all" ]]; then
  [[ "$app_uid" =~ ^[0-9]+$ && "$app_gid" =~ ^[0-9]+$ ]] \
    || die "APP_UID and APP_GID must be numeric"
  ((app_uid > 0 && app_gid > 0)) \
    || die "APP_UID and APP_GID must be non-root values"
  data_uid="$(stat -c '%u' "$data_dir")"
  data_gid="$(stat -c '%g' "$data_dir")"
  [[ "$data_uid" == "$app_uid" && "$data_gid" == "$app_gid" ]] \
    || die "persistent data owner is ${data_uid}:${data_gid}, but the application image expects ${app_uid}:${app_gid}; align APP_UID/APP_GID or fix directory ownership"
fi

min_free_gb="$(cfg MIN_FREE_GB 40)"
[[ "$min_free_gb" =~ ^[0-9]+$ ]] || die "MIN_FREE_GB must be a non-negative integer"
free_kb="$(df -Pk "$data_dir" | awk 'NR==2 {print $4}')"
((free_kb >= min_free_gb * 1024 * 1024)) \
  || die "insufficient free disk space: require ${min_free_gb} GiB"

offline_runtime="$(cfg OFFLINE_RUNTIME 1)"
vllm_image="$(cfg VLLM_IMAGE nvcr.io/nvidia/vllm:26.05.post1-py3)"
app_image="$(cfg APP_IMAGE relicscope-ai-demo:1.1.0-arm64)"

if [[ "$MODEL_REQUIREMENTS_EXPLICIT" == "0" ]]; then
  case "$ROLE" in
    spark-a)
      REQUIRE_VISION=1
      [[ "$(cfg EMBEDDING_ENABLED 0)" == "1" ]] && REQUIRE_EMBEDDING=1
      ;;
    spark-b)
      [[ "$(cfg REASONER_ENABLED 0)" == "1" ]] && REQUIRE_REASONER=1
      ;;
    all)
      REQUIRE_VISION=1
      [[ "$(cfg EMBEDDING_ENABLED 0)" == "1" ]] && REQUIRE_EMBEDDING=1
      [[ "$(cfg REASONER_ENABLED 0)" == "1" ]] && REQUIRE_REASONER=1
      ;;
  esac
fi

needs_app=0
[[ "$ROLE" == "spark-b" || "$ROLE" == "single" || "$ROLE" == "all" ]] && needs_app=1
needs_vllm=0
[[ "$REQUIRE_VISION" == "1" || "$REQUIRE_EMBEDDING" == "1" || "$REQUIRE_REASONER" == "1" ]] \
  && needs_vllm=1

if [[ "$offline_runtime" == "1" ]]; then
  if [[ "$needs_app" == "1" ]]; then
    docker image inspect "$app_image" >/dev/null 2>&1 \
      || die "offline application image is missing: ${app_image}"
    image_uid="$(docker image inspect --format '{{index .Config.Labels "ai.relicscope.app.uid"}}' "$app_image")"
    image_gid="$(docker image inspect --format '{{index .Config.Labels "ai.relicscope.app.gid"}}' "$app_image")"
    image_arch="$(docker image inspect --format '{{.Architecture}}' "$app_image")"
    [[ "$image_uid" == "$app_uid" && "$image_gid" == "$app_gid" ]] \
      || die "application image UID/GID ${image_uid:-unset}:${image_gid:-unset} does not match configured ${app_uid}:${app_gid}; rebuild during prefetch"
    [[ "$image_arch" == "arm64" ]] \
      || die "application image architecture is ${image_arch}; expected arm64"
  fi
  if [[ "$needs_vllm" == "1" ]]; then
    docker image inspect "$vllm_image" >/dev/null 2>&1 \
      || die "offline vLLM image is missing: ${vllm_image}"
    vllm_arch="$(docker image inspect --format '{{.Architecture}}' "$vllm_image")"
    [[ "$vllm_arch" == "arm64" ]] \
      || die "vLLM image architecture is ${vllm_arch}; expected arm64"
  fi
  if [[ "$REQUIRE_VISION" == "1" ]]; then
    check_cache_for_model "$cache_dir" "$(cfg VISION_MODEL nvidia/Qwen2.5-VL-7B-Instruct-NVFP4)"
  fi
  if [[ "$REQUIRE_EMBEDDING" == "1" ]]; then
    check_cache_for_model "$cache_dir" "$(cfg EMBEDDING_MODEL Qwen/Qwen3-VL-Embedding-2B)"
  fi
  if [[ "$REQUIRE_REASONER" == "1" ]]; then
    check_cache_for_model "$cache_dir" "$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)"
  fi
fi

if [[ "$SKIP_HARDWARE_CHECKS" != "1" && "$needs_vllm" == "1" ]] \
    && docker image inspect "$vllm_image" >/dev/null 2>&1; then
  docker run --rm --gpus all --entrypoint nvidia-smi "$vllm_image" >/dev/null 2>&1 \
    || die "NVIDIA Container Toolkit GPU passthrough check failed"
fi

if [[ "$ROLE" == "spark-b" || "$ROLE" == "all" ]]; then
  (
    cd "$PROJECT_DIR"
    docker compose --env-file "$ENV_FILE" -f compose.yml config --quiet
  ) || die "compose.yml validation failed"
elif [[ "$ROLE" == "single" ]]; then
  (
    cd "$PROJECT_DIR"
    docker compose --env-file "$ENV_FILE" -f compose.single.yml config --quiet
  ) || die "compose.single.yml validation failed"
fi

if [[ "$CHECK_RUNNING" == "1" ]]; then
  if [[ "$ROLE" == "spark-a" || "$ROLE" == "all" ]]; then
    if [[ "$REQUIRE_VISION" == "1" ]]; then
      docker inspect --format '{{.State.Health.Status}}' relicscope-vision 2>/dev/null \
        | grep -qx healthy || die "relicscope-vision is not healthy"
      vision_base_url="http://$(cfg SPARK_A_BIND_IP ''):$(cfg VISION_PORT 8001)/v1"
      check_openai_endpoint_auth_model \
        vision "$vision_base_url" "$service_key_file" \
        "$(cfg VISION_MODEL nvidia/Qwen2.5-VL-7B-Instruct-NVFP4)"
    fi
    if [[ "$REQUIRE_EMBEDDING" == "1" ]]; then
      docker inspect --format '{{.State.Health.Status}}' relicscope-embedding 2>/dev/null \
        | grep -qx healthy || die "relicscope-embedding is not healthy"
      embedding_base_url="http://$(cfg SPARK_A_BIND_IP ''):$(cfg EMBEDDING_PORT 8003)/v1"
      check_openai_endpoint_auth_model \
        embedding "$embedding_base_url" "$service_key_file" \
        "$(cfg EMBEDDING_MODEL Qwen/Qwen3-VL-Embedding-2B)"
    fi
  fi
  if [[ "$ROLE" == "spark-b" || "$ROLE" == "single" || "$ROLE" == "all" ]]; then
    app_origin="http://$(cfg APP_BIND_IP 127.0.0.1):$(cfg RELICSCOPE_PORT 8088)"
    ready_url="${app_origin}/health/ready"
    app_url="${app_origin}/api/health"
    expect_app_vision="$REQUIRE_VISION"
    if [[ "$ROLE" != "single" && -n "$(cfg VISION_BASE_URL '')" ]]; then
      expect_app_vision=1
    fi
    expect_app_reasoner="$REQUIRE_REASONER"
    if [[ -n "$(cfg REASONER_BASE_URL '')" ]]; then
      expect_app_reasoner=1
    fi
    expect_app_embedding=0
    if [[ -n "$(cfg EMBEDDING_BASE_URL '')" ]]; then
      expect_app_embedding=1
    fi
    if [[ "$ROLE" == "single" ]]; then
      expected_mode=single-degraded
      expected_gateway_node="$(cfg SINGLE_NODE_ID spark-single)"
      expected_compute_node="$expected_gateway_node"
    else
      expected_mode=dual-node
      expected_gateway_node="$(cfg RELICSCOPE_NODE_ID spark-b)"
      expected_compute_node="$(cfg RELICSCOPE_COMPUTE_NODE_ID spark-a)"
    fi
    python3 - "$ready_url" "$expected_mode" <<'PY'
import json
import sys
import urllib.request

url, expected_mode = sys.argv[1:3]
with urllib.request.urlopen(url, timeout=5) as response:
    payload = json.load(response)
if payload.get("status") != "ready" or payload.get("mode") != expected_mode:
    raise SystemExit(
        f"application readiness is inconsistent: status={payload.get('status')!r}, "
        f"mode={payload.get('mode')!r}, expected_mode={expected_mode!r}"
    )
PY
    python3 - \
      "$app_url" "$expect_app_vision" "$expect_app_embedding" "$expect_app_reasoner" \
      "$expected_mode" "$expected_gateway_node" "$expected_compute_node" \
      "$(cfg RELICSCOPE_SERVICE_VERSION 1.1.0)" \
      "$(cfg VISION_MODEL nvidia/Qwen2.5-VL-7B-Instruct-NVFP4)" \
      "$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)" <<'PY'
import json
import sys
import urllib.request

(
    url,
    require_vision,
    require_embedding,
    require_reasoner,
    expected_mode,
    expected_gateway_node,
    expected_compute_node,
    expected_service_version,
    expected_vision_model,
    expected_reasoner_model,
) = sys.argv[1:11]
with urllib.request.urlopen(url, timeout=5) as response:
    payload = json.load(response)
if payload.get("mode") != expected_mode:
    raise SystemExit(
        f"application health mode is {payload.get('mode')!r}; expected {expected_mode!r}"
    )
if payload.get("offline") is not True:
    raise SystemExit("application health does not confirm offline runtime mode")
topology = payload.get("topology", {})
if topology.get("type") != "APPLICATION_LEVEL_INDEPENDENT_SERVICES":
    raise SystemExit("application health does not identify independent-service topology")
if topology.get("tensor_parallel") is not False:
    raise SystemExit("application health does not identify non-TP topology")
if topology.get("gateway_node") != expected_gateway_node:
    raise SystemExit("application health reports an unexpected gateway node")
if topology.get("compute_node") != expected_compute_node:
    raise SystemExit("application health reports an unexpected compute node")
if not payload.get("knowledge_version") or not payload.get("checked_at"):
    raise SystemExit("application health is missing knowledge version or check time")
components = {item.get("name"): item for item in payload.get("components", [])}
gateway = components.get("gateway-store", {})
if gateway.get("node_id") != expected_gateway_node:
    raise SystemExit("gateway-store reports an unexpected node")
if gateway.get("version") != expected_service_version:
    raise SystemExit("gateway-store reports an unexpected service version")
vision = components.get("spark-a-vision", {})
if require_vision == "1":
    if vision.get("status") != "online":
        raise SystemExit("required vision component is not online")
    if vision.get("node_id") != expected_compute_node:
        raise SystemExit("vision component reports an unexpected compute node")
    if vision.get("model") != expected_vision_model:
        raise SystemExit("vision component reports an unexpected configured model")
knowledge = components.get("local-knowledge", {})
if require_embedding == "1" and knowledge.get("status") != "ready":
    raise SystemExit("required embedding-backed knowledge component is not ready")
reasoner = components.get("spark-b-reasoner", {})
if require_reasoner == "1":
    if reasoner.get("status") != "online":
        raise SystemExit("required reasoner component is not online")
    if reasoner.get("node_id") != expected_gateway_node:
        raise SystemExit("reasoner component reports an unexpected node")
    if reasoner.get("model") != expected_reasoner_model:
        raise SystemExit("reasoner component reports an unexpected configured model")
PY
    vision_base_url="$(cfg VISION_BASE_URL '')"
    embedding_base_url="$(cfg EMBEDDING_BASE_URL '')"
    reasoner_base_url="$(cfg REASONER_BASE_URL '')"
    if [[ "$ROLE" != "single" && -n "$vision_base_url" ]]; then
      check_openai_endpoint_auth_model \
        vision "$vision_base_url" "$service_key_file" \
        "$(cfg VISION_MODEL nvidia/Qwen2.5-VL-7B-Instruct-NVFP4)"
    fi
    if [[ "$ROLE" != "single" && -n "$embedding_base_url" ]]; then
      check_openai_endpoint_auth_model \
        embedding "$embedding_base_url" "$service_key_file" \
        "$(cfg EMBEDDING_MODEL Qwen/Qwen3-VL-Embedding-2B)"
    fi
    if [[ "$ROLE" != "single" && -n "$reasoner_base_url" ]]; then
      check_openai_endpoint_auth_model \
        reasoner "$reasoner_base_url" "$service_key_file" \
        "$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)"
    fi
    if [[ "$ROLE" == "single" && "$REQUIRE_VISION" == "1" ]]; then
      single_vision_base_url="$(cfg SINGLE_VISION_BASE_URL '')"
      if [[ -n "$single_vision_base_url" && "$single_vision_base_url" != http://vision:* ]]; then
        check_openai_endpoint_auth_model \
          vision "$single_vision_base_url" "$service_key_file" \
          "$(cfg VISION_MODEL nvidia/Qwen2.5-VL-7B-Instruct-NVFP4)"
      else
        check_compose_endpoint_auth_model \
          vision compose.single.yml vision http://vision:8000/v1 \
          "$(cfg VISION_MODEL nvidia/Qwen2.5-VL-7B-Instruct-NVFP4)"
      fi
    fi
    if [[ "$REQUIRE_REASONER" == "1" ]]; then
      if [[ "$ROLE" == "single" ]]; then
        single_reasoner_base_url="$(cfg SINGLE_REASONER_BASE_URL '')"
        if [[ -n "$single_reasoner_base_url" && "$single_reasoner_base_url" != http://reasoner:* ]]; then
          check_openai_endpoint_auth_model \
            reasoner "$single_reasoner_base_url" "$service_key_file" \
            "$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)"
        else
          check_compose_endpoint_auth_model \
            reasoner compose.single.yml reasoner http://reasoner:8000/v1 \
            "$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)"
        fi
      elif [[ -z "$reasoner_base_url" ]]; then
        check_compose_endpoint_auth_model \
          reasoner compose.yml reasoner http://reasoner:8000/v1 \
          "$(cfg REASONER_MODEL nvidia/Qwen3-14B-NVFP4)"
      fi
    fi
  fi
fi

printf 'Preflight passed: role=%s, offline=%s, app=%s, vision=%s, embedding=%s, reasoner=%s, secrets=valid, endpoints=private\n' \
  "$ROLE" "$offline_runtime" "$needs_app" "$REQUIRE_VISION" "$REQUIRE_EMBEDDING" "$REQUIRE_REASONER"
