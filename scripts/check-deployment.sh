#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

failures=0
checked=0
while IFS= read -r -d '' script; do
  checked=$((checked + 1))
  if ! bash -n "$script"; then failures=$((failures + 1)); fi
  if ! grep -Eq '^set -[^#]*E[^#]*e[^#]*u[^#]*o[[:space:]]+pipefail|^set -Eeuo pipefail' "$script"; then
    printf 'ERROR: strict shell mode is missing: %s\n' "$script" >&2
    failures=$((failures + 1))
  fi
done < <(find "${PROJECT_DIR}/deploy" "${PROJECT_DIR}/scripts" -type f -name '*.sh' -print0 | sort -z)

if command -v shellcheck >/dev/null 2>&1; then
  while IFS= read -r -d '' script; do
    shellcheck -x "$script" || failures=$((failures + 1))
  done < <(find "${PROJECT_DIR}/deploy" "${PROJECT_DIR}/scripts" -type f -name '*.sh' -print0 | sort -z)
else
  printf '%s\n' 'INFO: shellcheck is not installed; bash -n and policy checks were run.'
fi

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  (
    cd "$PROJECT_DIR"
    docker compose --env-file .env.example -f compose.yml config --quiet
    docker compose --env-file .env.example -f compose.single.yml config --quiet
  ) || failures=$((failures + 1))
else
  printf '%s\n' 'INFO: Docker Compose is unavailable; compose static parsing was skipped.'
fi

if ((failures)); then
  printf 'Deployment checks failed: %s issue(s), %s shell script(s) inspected.\n' "$failures" "$checked" >&2
  exit 1
fi
printf 'Deployment checks passed: %s shell script(s); Compose checked when available.\n' "$checked"
