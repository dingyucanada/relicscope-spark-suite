#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
ROLE="spark-b"
OUTPUT_DIR=""

usage() {
  printf '%s\n' \
    "Usage: $0 [--role spark-b|single] [--output-dir DIR]" \
    "Creates a consistent data backup. A running application is briefly stopped" \
    "and restarted; secrets and model caches are never included."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    --output-dir)
      (($# >= 2)) || die "--output-dir requires a value"
      OUTPUT_DIR="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
case "$ROLE" in spark-b|single) ;; *) die "backup is valid only for spark-b or single" ;; esac
[[ -f "$ENV_FILE" ]] || die "environment file is missing: ${ENV_FILE}"
for command_name in awk docker tar sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command not found: ${command_name}"
done

cfg() {
  local key="$1"
  local fallback="${2-}"
  local direct="${!key-}"
  local value=""
  if [[ -n "$direct" ]]; then printf '%s' "$direct"; return; fi
  value="$(awk -v wanted="$key" '$0 ~ "^[[:space:]]*" wanted "=" {sub("^[[:space:]]*" wanted "=", "", $0); found=$0} END {print found}' "$ENV_FILE")"
  value="${value%$'\r'}"
  [[ "$value" == \"*\" && "$value" == *\" ]] && value="${value:1:${#value}-2}"
  [[ "$value" == \'*\' && "$value" == *\' ]] && value="${value:1:${#value}-2}"
  printf '%s' "${value:-$fallback}"
}
absolute_path() { [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "$PROJECT_DIR" "$1"; }

data_dir="$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/data)")"
backup_dir="$(absolute_path "${OUTPUT_DIR:-$(cfg BACKUP_DIR ./runtime/backups)}")"
[[ "$data_dir" != "/" && "$data_dir" != "$PROJECT_DIR" ]] || die "unsafe data directory: ${data_dir}"
[[ "$backup_dir" != "/" && "$backup_dir" != "$PROJECT_DIR" && "$backup_dir" != "$data_dir" ]] \
  || die "unsafe backup directory: ${backup_dir}"
[[ -d "$data_dir" ]] || die "persistent data directory is missing: ${data_dir}"
install -d -m 700 -- "$backup_dir"

if command -v flock >/dev/null 2>&1; then
  exec 9>"${backup_dir}/.backup.lock"
  flock -n 9 || die "another backup is already running"
fi

if [[ "$ROLE" == "single" ]]; then
  compose_file="compose.single.yml"
else
  compose_file="compose.yml"
fi
compose=(docker compose --env-file "$ENV_FILE" -f "$compose_file")
container_id="$({ cd "$PROJECT_DIR"; "${compose[@]}" ps -q app; })"
restart_needed=0
if [[ -n "$container_id" && "$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || true)" == "true" ]]; then
  restart_needed=1
fi

restart_app() {
  if [[ "$restart_needed" == "1" ]]; then
    (
      cd "$PROJECT_DIR"
      "${compose[@]}" up --detach --no-build --pull never app >/dev/null
    ) || printf 'WARN: application restart after backup needs operator attention\n' >&2
    restart_needed=0
  fi
}
trap restart_app EXIT

if [[ "$restart_needed" == "1" ]]; then
  (cd "$PROJECT_DIR" && "${compose[@]}" stop --timeout 30 app >/dev/null)
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="${backup_dir}/relicscope-data-${timestamp}.tar.gz"
manifest="${archive}.manifest"
checksum="${archive}.sha256"

tar --numeric-owner --xattrs --acls -C "$data_dir" -czf "$archive" .
chmod 600 "$archive"
(cd "$backup_dir" && sha256sum "$(basename -- "$archive")") >"$checksum"
chmod 600 "$checksum"
archive_hash="$(sha256sum "$archive" | awk '{print $1}')"
knowledge_hash="unavailable"
if [[ -f "${PROJECT_DIR}/data/knowledge_manifest.json" ]]; then
  knowledge_hash="$(sha256sum "${PROJECT_DIR}/data/knowledge_manifest.json" | awk '{print $1}')"
fi
{
  printf 'created_at_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'role=%s\n' "$ROLE"
  printf 'archive=%s\n' "$(basename -- "$archive")"
  printf 'archive_sha256=%s\n' "$archive_hash"
  printf 'service_version=%s\n' "$(cfg RELICSCOPE_SERVICE_VERSION 1.2.0)"
  printf 'app_image=%s\n' "$(cfg APP_IMAGE relicscope-ai-demo:1.2.0-arm64)"
  printf 'knowledge_manifest_sha256=%s\n' "$knowledge_hash"
  printf 'contains_secrets=false\n'
  printf 'contains_model_cache=false\n'
} >"$manifest"
chmod 600 "$manifest"

restart_app
trap - EXIT
printf 'Backup complete: %s\n' "$archive"
printf 'Checksum: %s\n' "$checksum"
printf 'Manifest: %s\n' "$manifest"
