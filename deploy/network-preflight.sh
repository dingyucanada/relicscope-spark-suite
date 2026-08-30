#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
ROLE=""

usage() {
  printf '%s\n' \
    "Usage: $0 --role spark-a|spark-b" \
    "Checks the dedicated Spark-to-Spark route, local address, link state," \
    "negotiated speed and MTU. It does not change network configuration."
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf 'WARN: %s\n' "$*" >&2; }

while (($#)); do
  case "$1" in
    --role)
      (($# >= 2)) || die "--role requires a value"
      ROLE="$2"
      shift 2
      ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

case "$ROLE" in
  spark-a|spark-b) ;;
  *) die "--role must be spark-a or spark-b" ;;
esac

cfg() {
  local key="$1"
  local fallback="${2-}"
  local direct="${!key-}"
  local value=""
  if [[ -n "$direct" ]]; then printf '%s' "$direct"; return; fi
  if [[ -f "$ENV_FILE" ]]; then
    value="$(awk -v wanted="$key" '$0 ~ "^[[:space:]]*" wanted "=" {sub("^[[:space:]]*" wanted "=", "", $0); found=$0} END {print found}' "$ENV_FILE")"
    value="${value%$'\r'}"
    [[ "$value" == \"*\" && "$value" == *\" ]] && value="${value:1:${#value}-2}"
    [[ "$value" == \'*\' && "$value" == *\' ]] && value="${value:1:${#value}-2}"
  fi
  printf '%s' "${value:-$fallback}"
}

command -v awk >/dev/null 2>&1 || die "awk is required"
command -v ip >/dev/null 2>&1 || die "iproute2 is required"
[[ -f "$ENV_FILE" ]] || die "environment file is missing: ${ENV_FILE}"

if [[ "$ROLE" == "spark-a" ]]; then
  local_ip="$(cfg SPARK_A_IP '')"
  peer_ip="$(cfg SPARK_B_IP '')"
else
  local_ip="$(cfg SPARK_B_IP '')"
  peer_ip="$(cfg SPARK_A_IP '')"
fi
[[ -n "$local_ip" && -n "$peer_ip" ]] || die "SPARK_A_IP and SPARK_B_IP must be configured"
[[ "$local_ip" != "$peer_ip" ]] || die "Spark A and Spark B cannot use the same IP"

route_line="$(ip route get "$peer_ip" 2>/dev/null | head -n 1)" \
  || die "no route to peer ${peer_ip}"
[[ -n "$route_line" ]] || die "no route to peer ${peer_ip}"
route_iface="$(awk '{for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}' <<<"$route_line")"
configured_iface="$(cfg INTERCONNECT_INTERFACE '')"
iface="${configured_iface:-$route_iface}"
[[ -n "$iface" ]] || die "could not determine the interconnect interface"
[[ -d "/sys/class/net/${iface}" ]] || die "interconnect interface does not exist: ${iface}"
if [[ -n "$configured_iface" && "$configured_iface" != "$route_iface" ]]; then
  die "route to ${peer_ip} uses ${route_iface}, not configured interface ${configured_iface}"
fi

ip -o -4 addr show dev "$iface" | awk '{print $4}' | cut -d/ -f1 | grep -Fxq "$local_ip" \
  || die "${local_ip} is not assigned to ${iface}"

operstate="$(<"/sys/class/net/${iface}/operstate")"
[[ "$operstate" == "up" || "$operstate" == "unknown" ]] \
  || die "interface ${iface} is not up: ${operstate}"

min_mbps="$(cfg INTERCONNECT_MIN_MBPS 25000)"
[[ "$min_mbps" =~ ^[0-9]+$ && "$min_mbps" -ge 25000 ]] \
  || die "INTERCONNECT_MIN_MBPS must be an integer of at least 25000"
speed="unknown"
if [[ -r "/sys/class/net/${iface}/speed" ]]; then
  speed="$(<"/sys/class/net/${iface}/speed")"
fi
if [[ "$speed" =~ ^[0-9]+$ && "$speed" -gt 0 ]]; then
  ((speed >= min_mbps)) \
    || die "${iface} negotiated ${speed} Mb/s; require at least ${min_mbps} Mb/s"
elif [[ "$(cfg ALLOW_UNKNOWN_LINK_SPEED NO)" == "YES" ]]; then
  warn "link speed is unavailable for ${iface}; explicit ALLOW_UNKNOWN_LINK_SPEED=YES accepted"
else
  die "link speed is unavailable for ${iface}; verify the physical 25/100 GbE link or explicitly set ALLOW_UNKNOWN_LINK_SPEED=YES"
fi

mtu="$(<"/sys/class/net/${iface}/mtu")"
min_mtu="$(cfg INTERCONNECT_MTU_MIN 1500)"
[[ "$min_mtu" =~ ^[0-9]+$ ]] || die "INTERCONNECT_MTU_MIN must be numeric"
((mtu >= min_mtu)) || die "${iface} MTU is ${mtu}; require at least ${min_mtu}"

if [[ "$(cfg REQUIRE_PEER_PING 0)" == "1" ]]; then
  command -v ping >/dev/null 2>&1 || die "ping is required when REQUIRE_PEER_PING=1"
  ping -I "$iface" -c 2 -W 2 "$peer_ip" >/dev/null \
    || die "peer ${peer_ip} did not answer on ${iface}"
fi

printf 'Network preflight passed: role=%s interface=%s local=%s peer=%s speed_mbps=%s mtu=%s route="%s"\n' \
  "$ROLE" "$iface" "$local_ip" "$peer_ip" "$speed" "$mtu" "$route_line"
