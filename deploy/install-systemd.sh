#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ROLE=""
REMOVE=0
START_NOW=0
RUN_USER="${SUDO_USER:-${USER:-}}"
RUN_GROUP=""

usage() {
  printf '%s\n' \
    "Usage: sudo $0 --role spark-a|spark-b [--user USER] [--now] [--remove]" \
    "Renders and installs a role-specific systemd unit. It never edits firewall," \
    "network or Docker daemon configuration."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    --user)
      (($# >= 2)) || die "--user requires a value"
      RUN_USER="$2"
      shift 2
      ;;
    --now) START_NOW=1; shift ;;
    --remove) REMOVE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
case "$ROLE" in spark-a|spark-b) ;; *) die "--role must be spark-a or spark-b" ;; esac
((EUID == 0)) || die "run this installer as root"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
[[ -n "$RUN_USER" ]] || die "could not determine the non-root service user; pass --user"
id "$RUN_USER" >/dev/null 2>&1 || die "service user does not exist: ${RUN_USER}"
[[ "$RUN_USER" != "root" ]] || die "RelicScope systemd service must not run as root"
RUN_GROUP="$(id -gn "$RUN_USER")"

unit_name="relicscope-${ROLE}.service"
unit_path="/etc/systemd/system/${unit_name}"
template="${SCRIPT_DIR}/systemd/${unit_name}.in"

if [[ "$REMOVE" == "1" ]]; then
  systemctl disable --now "$unit_name" >/dev/null 2>&1 || true
  if [[ -f "$unit_path" ]]; then
    rm -f -- "$unit_path"
    printf 'Removed unit: %s\n' "$unit_path"
  else
    printf 'Unit already absent: %s\n' "$unit_path"
  fi
  systemctl daemon-reload
  exit 0
fi

[[ -f "$template" ]] || die "unit template is missing: ${template}"
[[ -f "${PROJECT_DIR}/.env" ]] || die "configure ${PROJECT_DIR}/.env before installing autostart"
[[ ! "$PROJECT_DIR" =~ [[:space:]\&\|%@] ]] \
  || die "systemd installer requires a project path without whitespace or sed/systemd metacharacters"
[[ "$RUN_USER" =~ ^[A-Za-z_][A-Za-z0-9_-]*[$]?$ ]] || die "unsafe user name"
[[ "$RUN_GROUP" =~ ^[A-Za-z_][A-Za-z0-9_-]*[$]?$ ]] || die "unsafe group name"

tmp_unit="$(mktemp "/etc/systemd/system/.${unit_name}.XXXXXX")"
trap 'rm -f -- "$tmp_unit"' EXIT
sed \
  -e "s|@PROJECT_DIR@|${PROJECT_DIR}|g" \
  -e "s|@RUN_USER@|${RUN_USER}|g" \
  -e "s|@RUN_GROUP@|${RUN_GROUP}|g" \
  "$template" >"$tmp_unit"
chmod 644 "$tmp_unit"
mv -f -- "$tmp_unit" "$unit_path"
trap - EXIT

systemctl daemon-reload
systemctl enable "$unit_name" >/dev/null
printf 'Installed and enabled: %s\n' "$unit_path"
if [[ "$START_NOW" == "1" ]]; then
  systemctl start "$unit_name"
  printf 'Started: %s\n' "$unit_name"
else
  printf 'Autostart is enabled. Start now with: systemctl start %s\n' "$unit_name"
fi
printf 'After both nodes boot, verify explicitly: %s/deploy/healthcheck.sh --role %s --wait 900\n' \
  "$PROJECT_DIR" "$ROLE"
