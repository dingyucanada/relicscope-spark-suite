#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${V2_ENV_FILE:-${project_root}/.env.v2}"
compose_file="${project_root}/compose.v2.yml"
archive_path=""
confirm_restore=false
allow_version_mismatch=false
extract_parent=""
cutover_started=false
cutover_complete=false
rollback_timestamp=""
data_stage=""
caddy_data_stage=""
caddy_config_stage=""
data_rollback=""
caddy_data_rollback=""
caddy_config_rollback=""

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: deploy/v2-restore.sh --archive /absolute/path/backup.tar.gz --confirm-restore [--allow-version-mismatch]

The adjacent backup.tar.gz.sha256 file is required. Restore stops the V2
Compose stack and leaves it stopped for preflight and health validation.
EOF
}

while (($#)); do
  case "$1" in
    --archive)
      (($# >= 2)) || fail "--archive requires a value"
      archive_path="$2"
      shift 2
      ;;
    --confirm-restore)
      confirm_restore=true
      shift
      ;;
    --allow-version-mismatch)
      allow_version_mismatch=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[[ "${confirm_restore}" == "true" ]] || fail "restore requires the explicit --confirm-restore flag"
[[ -n "${archive_path}" ]] || fail "--archive is required"
[[ "${archive_path}" == /* ]] || fail "--archive must be an absolute path"
[[ "$(id -u)" != "0" ]] || fail "run restore as the non-root Spark operator"
[[ -f "${env_file}" ]] || fail ".env.v2 is missing: ${env_file}"
for command_name in awk basename chmod date dirname docker find flock git mkdir mktemp mv python3 rm rsync sed sha256sum stat sync tar; do
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

data_dir="$(safe_managed_dir "$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/v2-data)")")"
caddy_data_dir="$(safe_managed_dir "$(absolute_path "$(cfg CADDY_DATA_DIR ./runtime/caddy/data)")")"
caddy_config_dir="$(safe_managed_dir "$(absolute_path "$(cfg CADDY_CONFIG_DIR ./runtime/caddy/config)")")"
assert_separate_paths "${data_dir}" "${caddy_data_dir}" "${caddy_config_dir}"
for target in "${data_dir}" "${caddy_data_dir}" "${caddy_config_dir}"; do
  [[ -d "${target}" ]] || fail "current managed directory is missing: ${target}"
  [[ -w "${target}" && -w "$(dirname "${target}")" ]] \
    || fail "managed directory and its parent must be writable: ${target}"
done

[[ ! -L "${archive_path}" ]] || fail "archive path must not be a symlink"
archive_path="$(canonical_path "${archive_path}")"
[[ -f "${archive_path}" && ! -L "${archive_path}" ]] || fail "archive must be a regular non-symlink file"
python3 - "${archive_path}" "${project_root}" "${data_dir}" "${caddy_data_dir}" "${caddy_config_dir}" <<'PY'
import os
import sys

archive = os.path.realpath(sys.argv[1])
project = os.path.realpath(sys.argv[2])
if os.path.commonpath((archive, project)) == project:
    raise SystemExit("restore archive must be outside the checked-out source tree")
for target in map(os.path.realpath, sys.argv[3:]):
    if os.path.commonpath((archive, target)) == target:
        raise SystemExit(f"archive must be outside managed restore targets: {archive}")
PY
case "${archive_path}" in
  *.tar.gz) ;;
  *) fail "archive must end in .tar.gz" ;;
esac
archive_sidecar="${archive_path}.sha256"
[[ -f "${archive_sidecar}" && ! -L "${archive_sidecar}" ]] \
  || fail "adjacent archive digest is required: ${archive_sidecar}"
expected_archive_digest="$(python3 - "${archive_sidecar}" "$(basename "${archive_path}")" <<'PY'
import pathlib
import re
import sys

lines = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
if len(lines) != 1:
    raise SystemExit("archive digest sidecar must contain exactly one line")
match = re.fullmatch(r"([0-9a-fA-F]{64})  (.+)", lines[0])
if not match or match.group(2) != sys.argv[2]:
    raise SystemExit("archive digest sidecar filename does not match the archive")
print(match.group(1))
PY
)" || fail "invalid archive digest sidecar"
actual_archive_digest="$(sha256sum "${archive_path}" | awk '{print $1}')"
[[ "${actual_archive_digest,,}" == "${expected_archive_digest,,}" ]] \
  || fail "archive SHA-256 does not match its sidecar"

extract_parent="$(mktemp -d "$(dirname "${data_dir}")/.relicscope-v2-restore.XXXXXX")"

cleanup() {
  local status=$?
  local tuple=""
  local target=""
  local rollback=""
  local stage=""
  trap - EXIT INT TERM
  if [[ "${cutover_started}" == "true" && "${cutover_complete}" != "true" ]]; then
    printf 'WARNING: restore cutover failed; attempting rollback.\n' >&2
    for tuple in \
      "${data_dir}|${data_rollback}" \
      "${caddy_data_dir}|${caddy_data_rollback}" \
      "${caddy_config_dir}|${caddy_config_rollback}"; do
      target="${tuple%%|*}"
      rollback="${tuple#*|}"
      if [[ -e "${rollback}" ]]; then
        if [[ -e "${target}" ]]; then
          mv -- "${target}" "${target}.failed-restore-${rollback_timestamp}" || true
        fi
        mv -- "${rollback}" "${target}" || true
      fi
    done
  fi
  for stage in "${data_stage}" "${caddy_data_stage}" "${caddy_config_stage}"; do
    if [[ -n "${stage}" && -d "${stage}" && "${stage}" == *.restore-stage-"${rollback_timestamp}" ]]; then
      rm -rf -- "${stage}"
    fi
  done
  if [[ -n "${extract_parent}" && "${extract_parent}" == "$(dirname "${data_dir}")/.relicscope-v2-restore."* ]]; then
    rm -rf -- "${extract_parent}"
  fi
  exit "${status}"
}
trap cleanup EXIT INT TERM

top_level="$(python3 - "${archive_path}" "${extract_parent}" <<'PY'
import pathlib
import posixpath
import re
import shutil
import sys
import tarfile

archive = pathlib.Path(sys.argv[1])
work_dir = pathlib.Path(sys.argv[2])
seen = set()
top_levels = set()
total_size = 0
with tarfile.open(archive, "r:gz") as bundle:
    for member in bundle.getmembers():
        name = member.name
        if not name or name.startswith("/") or any(character in name for character in ("\\", "\n", "\r")):
            raise SystemExit(f"unsafe archive member: {name!r}")
        normalized = posixpath.normpath(name)
        if normalized != name.rstrip("/") or normalized in {".", ".."} or normalized.startswith("../"):
            raise SystemExit(f"unsafe archive member: {name!r}")
        if normalized in seen:
            raise SystemExit(f"duplicate archive member: {normalized}")
        seen.add(normalized)
        total_size += member.size
        parts = normalized.split("/")
        top_levels.add(parts[0])
        if not re.fullmatch(r"relicscope-v2-backup-\d{8}T\d{6}Z", parts[0]):
            raise SystemExit(f"unexpected archive root: {parts[0]}")
        relative = "/".join(parts[1:])
        allowed = (
            relative in {"", "manifest.json", "SHA256SUMS", "payload"}
            or relative.startswith("payload/data/")
            or relative == "payload/data"
            or relative.startswith("payload/caddy-data/")
            or relative == "payload/caddy-data"
            or relative.startswith("payload/caddy-config/")
            or relative == "payload/caddy-config"
        )
        if not allowed:
            raise SystemExit(f"unexpected archive member: {relative}")
        if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
            raise SystemExit(f"unsupported archive member type: {normalized}")
if len(top_levels) != 1:
    raise SystemExit("archive must contain exactly one backup root")
free = shutil.disk_usage(work_dir).free
reserve = max(1 << 30, total_size // 20)
if total_size + reserve > free:
    raise SystemExit(
        f"insufficient restore workspace: need {total_size + reserve} bytes, have {free}"
    )
print(next(iter(top_levels)))
PY
)" || fail "archive member validation failed"

tar --extract --gzip --file "${archive_path}" --directory "${extract_parent}" \
  --no-same-owner --no-same-permissions
backup_root="${extract_parent}/${top_level}"
[[ -f "${backup_root}/manifest.json" && -f "${backup_root}/SHA256SUMS" ]] \
  || fail "archive manifest or checksums are missing"
for payload in data caddy-data caddy-config; do
  [[ -d "${backup_root}/payload/${payload}" ]] || fail "archive payload is missing: ${payload}"
done

python3 - "${backup_root}" "${top_level}" <<'PY'
import hashlib
import json
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
top_level = sys.argv[2]
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
if manifest.get("format") != "relicscope-scout-spark-v2-backup" or manifest.get("format_version") != 1:
    raise SystemExit("unsupported backup manifest")
if manifest.get("backup_id") != top_level:
    raise SystemExit("backup ID does not match archive root")
expected_payload = {"payload/data", "payload/caddy-data", "payload/caddy-config"}
if {entry.get("path") for entry in manifest.get("payload", [])} != expected_payload:
    raise SystemExit("manifest payload scope is invalid")
excluded = set(manifest.get("excluded", []))
required_exclusions = {".env.v2", "service API key", "Hugging Face model cache", "vLLM cache"}
if not required_exclusions.issubset(excluded):
    raise SystemExit("manifest does not declare required secret/cache exclusions")

checksum_file = root / "SHA256SUMS"
listed = set()
for line in checksum_file.read_text(encoding="utf-8").splitlines():
    match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
    if not match:
        raise SystemExit("malformed SHA256SUMS line")
    relative = match.group(2)
    path = pathlib.PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit(f"unsafe checksum path: {relative}")
    if relative != "manifest.json" and not relative.startswith("payload/"):
        raise SystemExit(f"checksum path outside payload: {relative}")
    if relative in listed:
        raise SystemExit(f"duplicate checksum path: {relative}")
    listed.add(relative)

actual = {
    path.relative_to(root).as_posix()
    for path in root.rglob("*")
    if path.is_file() and path.name != "SHA256SUMS"
}
if listed != actual:
    missing = sorted(actual - listed)
    extra = sorted(listed - actual)
    raise SystemExit(f"checksum inventory mismatch; missing={missing}, extra={extra}")
PY
(
  cd "${backup_root}"
  sha256sum --check --strict SHA256SUMS >/dev/null
) || fail "payload SHA-256 validation failed"

manifest_values="$(python3 - "${backup_root}/manifest.json" <<'PY'
import json
import pathlib
import re
import sys

source = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["source"]
keys = (
    "git_commit",
    "service_version",
    "vision_model",
    "vision_model_revision",
    "ingress_caddy_image",
    "ingress_caddy_image_id",
)
values = [source.get(key, "unknown") for key in keys]
if any(not isinstance(value, str) or "\n" in value or "\r" in value for value in values):
    raise SystemExit("manifest provenance fields are invalid")
if values[4] != "unknown" and not re.search(r"@sha256:[0-9a-fA-F]{64}$", values[4]):
    raise SystemExit("manifest ingress Caddy image is not immutable")
if values[5] != "unknown" and not re.fullmatch(r"sha256:[0-9a-f]{64}", values[5]):
    raise SystemExit("manifest ingress Caddy image ID is invalid")
print("\n".join(values))
PY
)"
backup_commit="$(sed -n '1p' <<<"${manifest_values}")"
backup_service_version="$(sed -n '2p' <<<"${manifest_values}")"
backup_model="$(sed -n '3p' <<<"${manifest_values}")"
backup_revision="$(sed -n '4p' <<<"${manifest_values}")"
backup_ingress_caddy_image="$(sed -n '5p' <<<"${manifest_values}")"
backup_ingress_caddy_image_id="$(sed -n '6p' <<<"${manifest_values}")"
current_commit="$(git -C "${project_root}" rev-parse --verify 'HEAD^{commit}' 2>/dev/null || printf unknown)"
current_service_version="$(cfg RELICSCOPE_SERVICE_VERSION 2.0.0)"
current_model="$(cfg VISION_MODEL unknown)"
current_revision="$(cfg VISION_MODEL_REVISION unknown)"
current_ingress_caddy_image="$(cfg CADDY_IMAGE unknown)"
current_ingress_caddy_image_id="$(docker image inspect \
  --format '{{.Id}}' "${current_ingress_caddy_image}" 2>/dev/null || printf unknown)"
if [[ "${allow_version_mismatch}" != "true" ]]; then
  [[ "${backup_commit}" == "${current_commit}" ]] \
    || fail "backup source commit differs; checkout ${backup_commit} or use --allow-version-mismatch after review"
  [[ "${backup_service_version}" == "${current_service_version}" ]] \
    || fail "backup service version differs; align the release or review --allow-version-mismatch"
  [[ "${backup_model}" == "${current_model}" && "${backup_revision}" == "${current_revision}" ]] \
    || fail "backup model identity differs; align .env.v2 or use --allow-version-mismatch after review"
  [[ "${backup_ingress_caddy_image}" != "unknown" \
     && "${backup_ingress_caddy_image_id}" != "unknown" \
     && "${current_ingress_caddy_image}" =~ @sha256:[0-9a-fA-F]{64}$ \
     && "${current_ingress_caddy_image_id}" =~ ^sha256:[0-9a-f]{64}$ \
     && "${backup_ingress_caddy_image}" == "${current_ingress_caddy_image}" \
     && "${backup_ingress_caddy_image_id}" == "${current_ingress_caddy_image_id}" ]] \
    || fail "backup ingress Caddy image identity differs; align CADDY_IMAGE or use --allow-version-mismatch after review"
fi
[[ -z "$(git -C "${project_root}" status --porcelain --untracked-files=all)" ]] \
  || fail "restore requires a clean checked-out source tree"

rollback_timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
data_stage="${data_dir}.restore-stage-${rollback_timestamp}"
caddy_data_stage="${caddy_data_dir}.restore-stage-${rollback_timestamp}"
caddy_config_stage="${caddy_config_dir}.restore-stage-${rollback_timestamp}"
data_rollback="${data_dir}.pre-restore-${rollback_timestamp}"
caddy_data_rollback="${caddy_data_dir}.pre-restore-${rollback_timestamp}"
caddy_config_rollback="${caddy_config_dir}.pre-restore-${rollback_timestamp}"
for path in \
  "${data_stage}" "${caddy_data_stage}" "${caddy_config_stage}" \
  "${data_rollback}" "${caddy_data_rollback}" "${caddy_config_rollback}"; do
  [[ ! -e "${path}" ]] || fail "restore staging/rollback path already exists: ${path}"
done

stage_payload() {
  local source="$1"
  local stage="$2"
  local target_parent=""
  target_parent="$(dirname "${stage}")"
  if [[ "$(stat -c '%d' "$(dirname "${source}")")" == "$(stat -c '%d' "${target_parent}")" ]]; then
    mv -- "${source}" "${stage}"
    return
  fi

  python3 - "${source}" "${target_parent}" <<'PY'
import pathlib
import shutil
import sys

source = pathlib.Path(sys.argv[1])
target_parent = pathlib.Path(sys.argv[2])
required = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
reserve = max(1 << 30, required // 20)
free = shutil.disk_usage(target_parent).free
if required + reserve > free:
    raise SystemExit(
        f"insufficient target staging space: need {required + reserve} bytes, have {free}"
    )
PY
  mkdir -m 700 -- "${stage}"
  rsync -a --safe-links --no-owner --no-group -- "${source}/" "${stage}/"
  python3 - "${source}" "${stage}" <<'PY'
import hashlib
import pathlib
import sys


def inventory(root: pathlib.Path):
    directories = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_dir()
    }
    files = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        files[path.relative_to(root).as_posix()] = digest.hexdigest()
    return directories, files


source_inventory = inventory(pathlib.Path(sys.argv[1]))
stage_inventory = inventory(pathlib.Path(sys.argv[2]))
if source_inventory != stage_inventory:
    raise SystemExit("cross-filesystem restore staging verification failed")
PY
}

find "${backup_root}/payload" -type d -exec chmod 700 -- {} +
find "${backup_root}/payload" -type f -exec chmod 600 -- {} +
stage_payload "${backup_root}/payload/data" "${data_stage}"
stage_payload "${backup_root}/payload/caddy-data" "${caddy_data_stage}"
stage_payload "${backup_root}/payload/caddy-config" "${caddy_config_stage}"
find "${data_stage}" "${caddy_data_stage}" "${caddy_config_stage}" -type d -exec chmod 700 -- {} +
find "${data_stage}" "${caddy_data_stage}" "${caddy_config_stage}" -type f -exec chmod 600 -- {} +
sync

docker compose --env-file "${env_file}" -f "${compose_file}" stop --timeout 60
cutover_started=true
mv -- "${data_dir}" "${data_rollback}"
mv -- "${data_stage}" "${data_dir}"
mv -- "${caddy_data_dir}" "${caddy_data_rollback}"
mv -- "${caddy_data_stage}" "${caddy_data_dir}"
mv -- "${caddy_config_dir}" "${caddy_config_rollback}"
mv -- "${caddy_config_stage}" "${caddy_config_dir}"
cutover_complete=true

printf '%s\n' \
  'PASS: V2 data and Caddy TLS identity restored; Compose remains stopped.' \
  "Rollback copy: ${data_rollback}" \
  "Rollback copy: ${caddy_data_rollback}" \
  "Rollback copy: ${caddy_config_rollback}" \
  'Next: run v2-preflight, start V2, run v2-health, then submit a job with an existing Scout credential.'
