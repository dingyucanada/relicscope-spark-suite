#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_ENV_FILE:-${project_root}/.env.v2}"
compose_file="${V2_COMPOSE_FILE:-${project_root}/compose.v2.yml}"
output_dir=""
staging_parent=""
gateway_was_running=false
ingress_was_running=false

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: deploy/v2-backup.sh --output-dir /absolute/path

Creates a verified V2 application-data/Caddy-state backup outside the source
tree. Runtime environment files, service/NGC credentials, model caches and
container images are deliberately excluded. NVIDIA NIM backups also exclude
Caddy PKI/private-key state; the target keeps or provisions its own TLS identity.
EOF
}

while (($#)); do
  case "$1" in
    --output-dir)
      (($# >= 2)) || fail "--output-dir requires a value"
      output_dir="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ -n "${output_dir}" ]] || fail "--output-dir is required"
[[ "${output_dir}" == /* ]] || fail "--output-dir must be an absolute path"
[[ "$(id -u)" != "0" ]] || fail "run backup as the non-root Spark operator"
[[ -f "${env_file}" && ! -L "${env_file}" ]] || fail "V2 environment file is missing or is a symlink: ${env_file}"
[[ -f "${compose_file}" && ! -L "${compose_file}" ]] || fail "V2 Compose file is missing or is a symlink: ${compose_file}"
for command_name in awk basename date dirname docker find flock git grep hostname mkdir mktemp mv python3 rm rsync sed sha256sum sort sync tar xargs; do
  command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable to this user"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"
mkdir -p -- "${project_root}/runtime"
exec 9>"${project_root}/runtime/.v2-maintenance.lock"
flock -n 9 || fail "another V2 maintenance or device mutation is active"

cfg() {
  local key="$1"
  local fallback="${2-}"
  python3 "${project_root}/deploy/read-v2-env.py" \
    --file "${env_file}" --key "${key}" --default "${fallback}"
}

absolute_path() {
  [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "${project_root}" "$1"
}

canonical_path() {
  python3 - "$1" <<'PY'
import os
import sys

print(os.path.realpath(sys.argv[1]))
PY
}

safe_managed_dir() {
  python3 - "$1" "${project_root}" <<'PY'
import os
import sys

value, project = sys.argv[1:3]
path = os.path.realpath(value)
forbidden = {
    "/", "/home", "/mnt", "/opt", "/srv", "/tmp", "/usr", "/var",
    os.path.realpath(os.path.expanduser("~")), os.path.realpath(project),
}
if path in forbidden:
    raise SystemExit(f"refusing broad managed-directory path: {path}")
print(path)
PY
}

assert_separate_paths() {
  python3 - "$@" <<'PY'
import os
import sys

paths = [os.path.realpath(value) for value in sys.argv[1:]]
for index, left in enumerate(paths):
    for right in paths[index + 1:]:
        if left == right or os.path.commonpath((left, right)) in {left, right}:
            raise SystemExit(f"managed paths must be separate and non-nested: {left} ; {right}")
PY
}

assert_excluded_path() {
  python3 - "$@" <<'PY'
import os
import sys

protected = os.path.realpath(sys.argv[1])
for source in map(os.path.realpath, sys.argv[2:]):
    if os.path.commonpath((protected, source)) == source:
        raise SystemExit(f"excluded path is inside backup scope: {protected}")
PY
}

data_dir="$(safe_managed_dir "$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/v2-data)")")"
caddy_data_dir="$(safe_managed_dir "$(absolute_path "$(cfg CADDY_DATA_DIR ./runtime/caddy/data)")")"
caddy_config_dir="$(safe_managed_dir "$(absolute_path "$(cfg CADDY_CONFIG_DIR ./runtime/caddy/config)")")"
hf_cache_dir="$(safe_managed_dir "$(absolute_path "$(cfg HF_CACHE_DIR ./runtime/hf-cache)")")"
vllm_cache_dir="$(safe_managed_dir "$(absolute_path "$(cfg VLLM_CACHE_DIR ./runtime/vllm-cache)")")"
nim_cache_dir="$(safe_managed_dir "$(absolute_path "$(cfg NIM_CACHE_DIR ./runtime/nim-cache)")")"
secret_file="$(canonical_path "$(absolute_path "$(cfg SERVICE_API_KEY_FILE ./secrets/service_api_key)")")"
env_file="$(canonical_path "${env_file}")"
compose_file="$(canonical_path "${compose_file}")"
output_dir="$(safe_managed_dir "${output_dir}")"

if [[ -n "$(cfg NIM_MODEL_PROFILE)" ]]; then
  expected_runtime_kind="nvidia-nim"
  include_caddy_data=false
else
  expected_runtime_kind="vllm"
  include_caddy_data=true
fi

python3 - "${output_dir}" "${project_root}" <<'PY'
import os
import sys

output, project = map(os.path.realpath, sys.argv[1:])
if os.path.commonpath((output, project)) == project:
    raise SystemExit("backup output must be outside the checked-out source tree")
PY

for source_dir in "${data_dir}" "${caddy_config_dir}"; do
  [[ -d "${source_dir}" && -r "${source_dir}" ]] || fail "backup source is unavailable: ${source_dir}"
done
if [[ "${include_caddy_data}" == "true" ]]; then
  [[ -d "${caddy_data_dir}" && -r "${caddy_data_dir}" ]] \
    || fail "backup source is unavailable: ${caddy_data_dir}"
fi
[[ -d "${data_dir}/scout-media" ]] || fail "Scout media directory is missing: ${data_dir}/scout-media"
assert_separate_paths "${data_dir}" "${caddy_data_dir}" "${caddy_config_dir}" "${output_dir}"
backup_sources=("${data_dir}" "${caddy_config_dir}")
if [[ "${include_caddy_data}" == "true" ]]; then
  backup_sources+=("${caddy_data_dir}")
fi
for excluded in "${secret_file}" "${env_file}" "${hf_cache_dir}" "${vllm_cache_dir}" "${nim_cache_dir}"; do
  assert_excluded_path "${excluded}" "${backup_sources[@]}"
done
if [[ "${include_caddy_data}" != "true" ]]; then
  assert_excluded_path "${caddy_data_dir}" "${backup_sources[@]}"
fi

mkdir -p -- "${output_dir}"
[[ -d "${output_dir}" && -w "${output_dir}" ]] || fail "backup output is not writable: ${output_dir}"

compose() {
  docker compose --env-file "${env_file}" -f "${compose_file}" "$@"
}

source_commit="$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}' 2>/dev/null)" \
  || fail "checked-out source is not an immutable Git commit"
[[ -z "$(git -C "${project_root}" status --porcelain --untracked-files=all)" ]] \
  || fail "backup requires a clean checked-out source tree"
[[ "$(cfg RELICSCOPE_GIT_COMMIT)" == "${source_commit}" ]] \
  || fail "RELICSCOPE_GIT_COMMIT does not match the checked-out source"

gateway_container="$(compose ps --all --quiet gateway)"
vision_container="$(compose ps --all --quiet vision)"
ingress_container="$(compose ps --all --quiet ingress)"
[[ -n "${gateway_container}" && -n "${vision_container}" && -n "${ingress_container}" ]] \
  || fail "gateway, vision and ingress containers must exist before a versioned backup"
runtime_values="$(docker inspect \
  "${gateway_container}" "${vision_container}" "${ingress_container}" | python3 -c '
import json
import sys

gateway, vision, ingress = json.load(sys.stdin)
gateway_env = dict(item.split("=", 1) for item in gateway["Config"].get("Env", []) if "=" in item)
vision_env = dict(item.split("=", 1) for item in vision["Config"].get("Env", []) if "=" in item)
values = (
    gateway["Image"],
    gateway["Config"].get("Labels", {}).get("org.opencontainers.image.revision", ""),
    gateway_env.get("RELICSCOPE_GIT_COMMIT", ""),
    gateway_env.get("RELICSCOPE_SERVICE_VERSION", ""),
    gateway_env.get("VISION_MODEL", ""),
    gateway_env.get("VISION_MODEL_SOURCE", ""),
    gateway_env.get("VISION_MODEL_REVISION", ""),
    gateway_env.get("VISION_RUNTIME_IMAGE", ""),
    gateway_env.get("MODEL_PROFILE", ""),
    vision["Image"],
    vision["Config"].get("Image", ""),
    vision_env.get("VISION_MODEL", ""),
    vision_env.get("VISION_MODEL_REVISION", ""),
    vision_env.get("NIM_SERVED_MODEL_NAME", ""),
    vision_env.get("NIM_MODEL_PROFILE", ""),
    ingress["Config"].get("Image", ""),
    ingress["Image"],
)
if any("\n" in value or "\r" in value for value in values):
    raise SystemExit("runtime provenance contains invalid line breaks")
print("\n".join(values))
')" || fail "could not inspect running V2 container provenance"
runtime_gateway_image_id="$(sed -n '1p' <<<"${runtime_values}")"
runtime_gateway_label_commit="$(sed -n '2p' <<<"${runtime_values}")"
runtime_gateway_env_commit="$(sed -n '3p' <<<"${runtime_values}")"
runtime_service_version="$(sed -n '4p' <<<"${runtime_values}")"
runtime_model="$(sed -n '5p' <<<"${runtime_values}")"
runtime_model_source="$(sed -n '6p' <<<"${runtime_values}")"
runtime_revision="$(sed -n '7p' <<<"${runtime_values}")"
runtime_gateway_runtime_image="$(sed -n '8p' <<<"${runtime_values}")"
runtime_gateway_model_profile="$(sed -n '9p' <<<"${runtime_values}")"
runtime_vision_image_id="$(sed -n '10p' <<<"${runtime_values}")"
runtime_vision_image="$(sed -n '11p' <<<"${runtime_values}")"
runtime_vision_model="$(sed -n '12p' <<<"${runtime_values}")"
runtime_vision_revision="$(sed -n '13p' <<<"${runtime_values}")"
runtime_nim_served_model="$(sed -n '14p' <<<"${runtime_values}")"
runtime_nim_profile="$(sed -n '15p' <<<"${runtime_values}")"
runtime_ingress_caddy_image="$(sed -n '16p' <<<"${runtime_values}")"
runtime_ingress_caddy_image_id="$(sed -n '17p' <<<"${runtime_values}")"
[[ "${runtime_gateway_label_commit}" == "${source_commit}" \
   && "${runtime_gateway_env_commit}" == "${source_commit}" ]] \
  || fail "the actual gateway container does not match the checked-out source commit"
[[ "${runtime_service_version}" == "$(cfg RELICSCOPE_SERVICE_VERSION 2.0.0)" ]] \
  || fail "the actual gateway service version differs from the selected V2 environment"
configured_gateway_image="$(cfg SCOUT_GATEWAY_IMAGE "relicscope-scout-gateway:${runtime_service_version}-arm64")"
configured_gateway_image_id="$(docker image inspect \
  --format '{{.Id}}' "${configured_gateway_image}" 2>/dev/null)" \
  || fail "the configured Scout gateway image is not present locally"
[[ "${runtime_gateway_image_id}" == "${configured_gateway_image_id}" \
   && "${runtime_gateway_image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || fail "the actual gateway container differs from the configured local image ID"
[[ "${runtime_model}" == "$(cfg VISION_MODEL)" \
   && "${runtime_model_source}" == "$(cfg VISION_MODEL_SOURCE)" ]] \
  || fail "the actual gateway model/source identity differs from the selected V2 environment"

if [[ -n "${runtime_nim_profile}" || -n "${runtime_nim_served_model}" ]]; then
  runtime_kind="nvidia-nim"
  [[ -n "${runtime_nim_profile}" && -n "${runtime_nim_served_model}" \
     && -z "${runtime_vision_model}" && -z "${runtime_vision_revision}" ]] \
    || fail "the actual vision container has an ambiguous NIM/vLLM runtime identity"
  configured_runtime_image="$(cfg NIM_VLM_IMAGE)"
  configured_runtime_profile="$(cfg NIM_MODEL_PROFILE)"
  configured_served_model="$(cfg NIM_SERVED_MODEL_NAME)"
  [[ "${runtime_revision}" == "${configured_runtime_profile}" \
     && "${runtime_nim_profile}" == "${configured_runtime_profile}" \
     && "${runtime_nim_served_model}" == "${configured_served_model}" \
     && "${configured_served_model}" == "${runtime_model}" \
     && "${runtime_model_source}" == "${runtime_model}" ]] \
    || fail "the actual gateway/NIM container differs from the frozen model/profile identity"
  [[ "${configured_runtime_image}" =~ @sha256:[0-9a-fA-F]{64}$ ]] \
    || fail "NIM_VLM_IMAGE must be pinned by registry digest before backup"
else
  runtime_kind="vllm"
  configured_runtime_image="$(cfg VLLM_IMAGE)"
  configured_runtime_profile="${runtime_revision}"
  configured_served_model="${runtime_vision_model}"
  [[ -n "${runtime_vision_model}" && -n "${runtime_vision_revision}" \
     && "${runtime_revision}" == "$(cfg VISION_MODEL_REVISION)" \
     && "${runtime_vision_model}" == "${runtime_model}" \
     && "${runtime_vision_revision}" == "${runtime_revision}" ]] \
    || fail "the actual gateway/vLLM container differs from the configured model identity"
fi
[[ "${runtime_kind}" == "${expected_runtime_kind}" ]] \
  || fail "the actual vision runtime kind differs from the selected V2 environment"
[[ "${runtime_gateway_runtime_image}" == "${configured_runtime_image}" \
   && "${runtime_vision_image}" == "${configured_runtime_image}" ]] \
  || fail "the actual model container image differs from the configured runtime image"
configured_runtime_image_id="$(docker image inspect \
  --format '{{.Id}}' "${configured_runtime_image}" 2>/dev/null)" \
  || fail "the configured vision runtime image is not present locally"
[[ "${runtime_vision_image_id}" == "${configured_runtime_image_id}" \
   && "${runtime_vision_image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || fail "the actual vision container differs from the configured local image ID"
configured_caddy_image="$(cfg CADDY_IMAGE)"
[[ "${configured_caddy_image}" =~ @sha256:[0-9a-fA-F]{64}$ ]] \
  || fail "CADDY_IMAGE must be pinned by registry digest before backup"
configured_caddy_image_id="$(docker image inspect \
  --format '{{.Id}}' "${configured_caddy_image}" 2>/dev/null)" \
  || fail "the configured Caddy image is not present locally"
[[ "${runtime_ingress_caddy_image}" == "${configured_caddy_image}" \
   && "${runtime_ingress_caddy_image_id}" == "${configured_caddy_image_id}" \
   && "${runtime_ingress_caddy_image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
  || fail "the actual ingress container differs from the pinned CADDY_IMAGE"

service_is_running() {
  compose ps --status running --services 2>/dev/null | grep -Fxq -- "$1"
}

restart_previous_services() {
  local status=0
  if [[ "${gateway_was_running}" == "true" ]]; then
    compose start gateway >/dev/null || status=$?
  fi
  if [[ "${ingress_was_running}" == "true" ]]; then
    compose start ingress >/dev/null || status=$?
  fi
  gateway_was_running=false
  ingress_was_running=false
  return "${status}"
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ "${gateway_was_running}" == "true" || "${ingress_was_running}" == "true" ]]; then
    if ! restart_previous_services; then
      printf 'WARNING: backup cleanup could not restore the previous gateway/ingress state.\n' >&2
      status=1
    fi
  fi
  if [[ -n "${staging_parent}" && "${staging_parent}" == "${output_dir}/.relicscope-v2-backup."* ]]; then
    rm -rf -- "${staging_parent}"
  fi
  exit "${status}"
}
trap cleanup EXIT INT TERM

staging_parent="$(mktemp -d "${output_dir}/.relicscope-v2-backup.XXXXXX")"
backup_timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
backup_id="relicscope-v2-backup-${backup_timestamp}"
backup_root="${staging_parent}/${backup_id}"
payload_root="${backup_root}/payload"
mkdir -p -- "${payload_root}/data" "${payload_root}/caddy-config"
if [[ "${include_caddy_data}" == "true" ]]; then
  mkdir -p -- "${payload_root}/caddy-data"
fi

sync_payload() {
  rsync -a --delete --safe-links --no-owner --no-group -- \
    "${data_dir}/" "${payload_root}/data/"
  if [[ "${include_caddy_data}" == "true" ]]; then
    rsync -a --delete --safe-links --no-owner --no-group -- \
      "${caddy_data_dir}/" "${payload_root}/caddy-data/"
  fi
  rsync -a --delete --safe-links --no-owner --no-group -- \
    "${caddy_config_dir}/" "${payload_root}/caddy-config/"
}

# Copy the large, mostly immutable media set while services are still available.
sync_payload
service_is_running gateway && gateway_was_running=true
service_is_running ingress && ingress_was_running=true
compose stop --timeout 30 ingress gateway >/dev/null
sync
# A final delta while writers are stopped captures SQLite, WAL/SHM and TLS state coherently.
sync_payload
restart_previous_services || fail "backup was captured, but gateway/ingress could not be restarted"

python3 - "${payload_root}" <<'PY'
import os
import pathlib
import stat
import sys

root = pathlib.Path(sys.argv[1])
for path in root.rglob("*"):
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
        raise SystemExit(f"unsupported payload entry: {path}")
    relative = path.relative_to(root).as_posix()
    if any(character in relative for character in ("\\", "\n", "\r")):
        raise SystemExit(f"unsupported payload filename: {relative!r}")
PY

if [[ "${runtime_kind}" == "nvidia-nim" ]]; then
  python3 - "${payload_root}/caddy-config" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
for path in root.rglob("*"):
    if not path.is_file():
        continue
    content = path.read_bytes()
    if b"PRIVATE KEY-----" in content or b"nvapi-" in content:
        raise SystemExit(f"Caddy configuration contains private-key/NGC credential material: {path}")
PY
fi

python3 - "${backup_root}/manifest.json" "${backup_id}" "${backup_timestamp}" \
  "${source_commit}" "${runtime_service_version}" \
  "${runtime_model}" "${runtime_model_source}" "${runtime_revision}" "$(hostname)" \
  "${runtime_kind}" "${runtime_gateway_model_profile}" \
  "${configured_runtime_profile}" "${configured_served_model}" \
  "${runtime_gateway_image_id}" "${runtime_vision_image_id}" \
  "${runtime_vision_image}" "${runtime_ingress_caddy_image}" \
  "${runtime_ingress_caddy_image_id}" "${include_caddy_data}" <<'PY'
import datetime
import json
import pathlib
import sys

(
    output, backup_id, compact_time, source_commit, service_version,
    model, model_source, model_revision, host, runtime_kind,
    gateway_model_profile, runtime_profile, served_model, gateway_image_id,
    vision_image_id, vision_runtime_image, ingress_caddy_image,
    ingress_caddy_image_id, include_caddy_data,
) = sys.argv[1:]
created = datetime.datetime.strptime(compact_time, "%Y%m%dT%H%M%SZ").replace(
    tzinfo=datetime.timezone.utc
)
payload = [
    {"path": "payload/data", "purpose": "V2 SQLite, WAL/SHM and scout-media"},
    {"path": "payload/caddy-config", "purpose": "Caddy runtime configuration state"},
]
excluded = [
    "V2 runtime environment file",
    "service API key",
    "NGC API key",
    "Hugging Face model cache",
    "vLLM cache",
    "NVIDIA NIM cache and model artifacts",
    "container images",
]
format_version = 2 if runtime_kind == "nvidia-nim" else 1
if include_caddy_data == "true":
    payload.insert(
        1,
        {"path": "payload/caddy-data", "purpose": "Caddy PKI, certificates and TLS identity"},
    )
    excluded.append("plaintext application credentials")
else:
    excluded.extend(["Caddy PKI and private-key state", "plaintext private-key values"])

manifest = {
    "format": "relicscope-scout-spark-v2-backup",
    "format_version": format_version,
    "backup_id": backup_id,
    "created_at": created.isoformat().replace("+00:00", "Z"),
    "source": {
        "host": host,
        "git_commit": source_commit,
        "service_version": service_version,
        "vision_runtime_kind": runtime_kind,
        "vision_model": model,
        "vision_model_source": model_source,
        "vision_model_revision": model_revision,
        "gateway_model_profile": gateway_model_profile,
        "vision_runtime_profile": runtime_profile,
        "vision_served_model": served_model,
        "gateway_image_id": gateway_image_id,
        "vision_image_id": vision_image_id,
        "vision_runtime_image": vision_runtime_image,
        "ingress_caddy_image": ingress_caddy_image,
        "ingress_caddy_image_id": ingress_caddy_image_id,
    },
    "payload": payload,
    "excluded": excluded,
    "integrity": {"algorithm": "SHA-256", "file": "SHA256SUMS"},
}
pathlib.Path(output).write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
PY

(
  cd "${backup_root}"
  find manifest.json payload -type f -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum >SHA256SUMS
)

archive_path="${output_dir}/${backup_id}.tar.gz"
archive_sidecar="${archive_path}.sha256"
[[ ! -e "${archive_path}" && ! -e "${archive_sidecar}" ]] \
  || fail "backup output already exists: ${archive_path}"
temporary_archive="${staging_parent}/${backup_id}.tar.gz"
tar --create --gzip --file "${temporary_archive}" --directory "${staging_parent}" "${backup_id}"
archive_digest="$(sha256sum "${temporary_archive}" | awk '{print $1}')"
printf '%s  %s\n' "${archive_digest}" "$(basename "${archive_path}")" \
  >"${staging_parent}/${backup_id}.tar.gz.sha256"
mv -- "${temporary_archive}" "${archive_path}"
mv -- "${staging_parent}/${backup_id}.tar.gz.sha256" "${archive_sidecar}"

printf '%s\n' \
  "PASS: V2 backup created: ${archive_path}" \
  "PASS: archive digest: ${archive_sidecar}" \
  "PASS: recorded vision runtime: ${runtime_kind}; model/profile/image provenance is frozen in the manifest." \
  'Runtime environment files, service/NGC credentials, model caches and container images were not included.'
if [[ "${include_caddy_data}" != "true" ]]; then
  printf '%s\n' 'NIM backup excluded Caddy PKI/private-key state; retain or reprovision the target TLS identity separately.'
fi
