#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
ROLE="spark-b"
ARCHIVE=""
ALLOW_MISSING_CHECKSUM=0

usage() {
  printf '%s\n' \
    "Usage: $0 [--role spark-b|single] --archive /absolute/backup.tar.gz [--allow-missing-checksum]" \
    "Verifies the archive, preserves current data as a timestamped sibling," \
    "restores with no owner escalation, and restarts the app if it was running."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    --archive)
      (($# >= 2)) || die "--archive requires a path"
      ARCHIVE="$2"
      shift 2
      ;;
    --allow-missing-checksum) ALLOW_MISSING_CHECKSUM=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
case "$ROLE" in spark-b|single) ;; *) die "restore is valid only for spark-b or single" ;; esac
[[ -n "$ARCHIVE" && "$ARCHIVE" == /* && -f "$ARCHIVE" ]] \
  || die "--archive must name an existing absolute path"
[[ -f "$ENV_FILE" ]] || die "environment file is missing: ${ENV_FILE}"
for command_name in awk docker tar sha256sum python3; do
  command -v "$command_name" >/dev/null 2>&1 || die "required command not found: ${command_name}"
done

cfg() {
  local key="$1"
  local fallback="${2-}"
  local value=""
  value="$(awk -v wanted="$key" '$0 ~ "^[[:space:]]*" wanted "=" {sub("^[[:space:]]*" wanted "=", "", $0); found=$0} END {print found}' "$ENV_FILE")"
  value="${value%$'\r'}"
  [[ "$value" == \"*\" && "$value" == *\" ]] && value="${value:1:${#value}-2}"
  [[ "$value" == \'*\' && "$value" == *\' ]] && value="${value:1:${#value}-2}"
  printf '%s' "${value:-$fallback}"
}
absolute_path() { [[ "$1" == /* ]] && printf '%s' "$1" || printf '%s/%s' "$PROJECT_DIR" "$1"; }

data_dir="$(absolute_path "$(cfg RELICSCOPE_DATA_HOST_DIR ./runtime/data)")"
[[ "$data_dir" != "/" && "$data_dir" != "$PROJECT_DIR" && ! -L "$data_dir" ]] \
  || die "unsafe or symbolic-link data directory: ${data_dir}"
data_parent="$(dirname -- "$data_dir")"
install -d -m 700 -- "$data_parent"

if [[ -f "${ARCHIVE}.sha256" ]]; then
  (cd "$(dirname -- "$ARCHIVE")" && sha256sum -c "$(basename -- "${ARCHIVE}.sha256")")
else
  [[ "$ALLOW_MISSING_CHECKSUM" == "1" ]] \
    || die "checksum sidecar is absent: ${ARCHIVE}.sha256; use --allow-missing-checksum only after independent verification"
  printf 'WARN: missing checksum explicitly accepted; computed SHA-256 is %s\n' "$(sha256sum "$ARCHIVE" | awk '{print $1}')" >&2
fi

python3 - "$ARCHIVE" <<'PY'
import pathlib
import sys
import tarfile

archive = sys.argv[1]
with tarfile.open(archive, "r:gz") as handle:
    for member in handle.getmembers():
        path = pathlib.PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {member.name}")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit(f"unsupported archive member type: {member.name}")
PY

if [[ "$ROLE" == "single" ]]; then compose_file="compose.single.yml"; else compose_file="compose.yml"; fi
compose=(docker compose --env-file "$ENV_FILE" -f "$compose_file")
container_id="$({ cd "$PROJECT_DIR"; "${compose[@]}" ps -q app; })"
restart_needed=0
if [[ -n "$container_id" && "$(docker inspect --format '{{.State.Running}}' "$container_id" 2>/dev/null || true)" == "true" ]]; then
  restart_needed=1
  (cd "$PROJECT_DIR" && "${compose[@]}" stop --timeout 30 app >/dev/null)
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
previous_dir="${data_dir}.pre-restore-${timestamp}"
failed_dir="${data_dir}.failed-restore-${timestamp}"
had_previous=0
restore_complete=0

recover_on_exit() {
  if [[ "$restore_complete" != "1" ]]; then
    if [[ -e "$data_dir" ]]; then mv -- "$data_dir" "$failed_dir" || true; fi
    if [[ "$had_previous" == "1" && -e "$previous_dir" ]]; then mv -- "$previous_dir" "$data_dir" || true; fi
    printf 'Restore failed; prior data was returned when possible. Failed extraction: %s\n' "$failed_dir" >&2
  fi
  if [[ "$restart_needed" == "1" ]]; then
    (cd "$PROJECT_DIR" && "${compose[@]}" up --detach --no-build --pull never app >/dev/null) \
      || printf 'WARN: application restart needs operator attention\n' >&2
  fi
}
trap recover_on_exit EXIT

if [[ -e "$data_dir" ]]; then
  mv -- "$data_dir" "$previous_dir"
  had_previous=1
fi
install -d -m 700 -- "$data_dir"
tar --no-same-owner --no-same-permissions -C "$data_dir" -xzf "$ARCHIVE"
chmod 700 "$data_dir"
restore_complete=1

if [[ "$restart_needed" == "1" ]]; then
  (cd "$PROJECT_DIR" && "${compose[@]}" up --detach --no-build --pull never app >/dev/null)
  restart_needed=0
fi
trap - EXIT

printf 'Restore complete: %s\n' "$data_dir"
if [[ "$had_previous" == "1" ]]; then
  printf 'Previous data retained for manual rollback: %s\n' "$previous_dir"
fi
printf '%s\n' 'Run deploy/healthcheck.sh before admitting users.'
