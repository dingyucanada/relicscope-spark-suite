#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROLE=""
WAIT_SECONDS=0
INTERVAL_SECONDS=5

usage() {
  printf '%s\n' \
    "Usage: $0 --role spark-a|spark-b|single|all [--wait SECONDS]" \
    "Runs authenticated model checks plus application readiness and topology checks."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    --wait)
      (($# >= 2)) || die "--wait requires seconds"
      WAIT_SECONDS="$2"
      shift 2
      ;;
    --interval)
      (($# >= 2)) || die "--interval requires seconds"
      INTERVAL_SECONDS="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$ROLE" in
  spark-a|spark-b|single|all) ;;
  *) die "invalid role: ${ROLE}" ;;
esac
[[ "$WAIT_SECONDS" =~ ^[0-9]+$ ]] || die "--wait must be a non-negative integer"
[[ "$INTERVAL_SECONDS" =~ ^[1-9][0-9]*$ ]] || die "--interval must be a positive integer"

probe() {
  "${SCRIPT_DIR}/preflight.sh" --role "$ROLE" --check-running
}

deadline=$((SECONDS + WAIT_SECONDS))
last_output=""
while true; do
  if last_output="$(probe 2>&1)"; then
    printf '%s\n' "$last_output"
    printf 'Health check passed: role=%s\n' "$ROLE"
    exit 0
  fi
  if ((SECONDS >= deadline)); then
    printf '%s\n' "$last_output" >&2
    die "health check timed out for role=${ROLE} after ${WAIT_SECONDS}s"
  fi
  sleep "$INTERVAL_SECONDS"
done
