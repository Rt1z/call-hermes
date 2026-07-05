#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/server/.env}"

read_env() {
  local key="$1"
  if [ ! -f "${ENV_FILE}" ]; then
    return 0
  fi
  grep -E "^${key}=" "${ENV_FILE}" | tail -1 | cut -d= -f2-
}

TURN_URLS="$(read_env ICE_TURN_URLS)"
TURN_USERNAME="$(read_env ICE_TURN_USERNAME)"
TURN_CREDENTIAL="$(read_env ICE_TURN_CREDENTIAL)"
MAX_CONCURRENT_SESSIONS="$(read_env MAX_CONCURRENT_SESSIONS)"
MAX_CONCURRENT_SESSIONS="${MAX_CONCURRENT_SESSIONS:-8}"
TURN_CONFIG_FILE="${TURN_CONFIG_FILE:-/etc/turnserver.conf}"
CHECK_STATUS=0

if [ -z "${TURN_URLS}" ] && [ -z "${TURN_USERNAME}" ] && [ -z "${TURN_CREDENTIAL}" ]; then
  echo "TURN is not configured in ${ENV_FILE}"
  exit 1
fi

if [ -z "${TURN_URLS}" ] || [ -z "${TURN_USERNAME}" ] || [ -z "${TURN_CREDENTIAL}" ]; then
  echo "TURN config is incomplete in ${ENV_FILE}"
  echo "Required: ICE_TURN_URLS, ICE_TURN_USERNAME, ICE_TURN_CREDENTIAL"
  exit 1
fi

FIRST_URL="${TURN_URLS%%,*}"
URL_NO_SCHEME="${FIRST_URL#turn:}"
URL_NO_SCHEME="${URL_NO_SCHEME#turns:}"
HOST_PORT="${URL_NO_SCHEME%%\?*}"
HOST="${HOST_PORT%%:*}"
PORT="${HOST_PORT##*:}"

echo "TURN config present"
echo "First TURN server: ${HOST}:${PORT}"
echo "Username: ${TURN_USERNAME}"
echo "Credential: configured"

if [ -r "${TURN_CONFIG_FILE}" ]; then
  EXTERNAL_MAPPING="$(awk -F= '/^[[:space:]]*external-ip=/{print $2; exit}' "${TURN_CONFIG_FILE}")"
  EXTERNAL_IP="${EXTERNAL_MAPPING%%/*}"
  DNS_IP="$(getent ahostsv4 "${HOST}" 2>/dev/null | awk '$2 == "STREAM" {print $1; exit}')"
  if [ -n "${EXTERNAL_IP}" ] && [ -n "${DNS_IP}" ]; then
    if [ "${EXTERNAL_IP}" = "${DNS_IP}" ]; then
      echo "TURN external IP: ok (${EXTERNAL_IP})"
    else
      echo "TURN external IP: mismatch"
      echo "  coturn external-ip: ${EXTERNAL_IP}"
      echo "  ${HOST} resolves to: ${DNS_IP}"
      echo "Update external-ip in ${TURN_CONFIG_FILE}, then restart coturn."
      CHECK_STATUS=1
    fi
  else
    echo "TURN external IP: skipped, external-ip or IPv4 DNS record unavailable"
  fi
  MIN_RELAY_PORT="$(awk -F= '/^[[:space:]]*min-port=/{print $2; exit}' "${TURN_CONFIG_FILE}")"
  MAX_RELAY_PORT="$(awk -F= '/^[[:space:]]*max-port=/{print $2; exit}' "${TURN_CONFIG_FILE}")"
  if [[ "${MIN_RELAY_PORT}" =~ ^[0-9]+$ && "${MAX_RELAY_PORT}" =~ ^[0-9]+$ ]]; then
    RELAY_PORT_COUNT=$((MAX_RELAY_PORT - MIN_RELAY_PORT + 1))
    REQUIRED_RELAY_PORTS=$((MAX_CONCURRENT_SESSIONS * 2))
    if [ "${RELAY_PORT_COUNT}" -ge "${REQUIRED_RELAY_PORTS}" ]; then
      echo "TURN relay capacity: ok (${RELAY_PORT_COUNT} ports for ${MAX_CONCURRENT_SESSIONS} sessions)"
    else
      echo "TURN relay capacity: insufficient"
      echo "  relay ports: ${MIN_RELAY_PORT}-${MAX_RELAY_PORT} (${RELAY_PORT_COUNT})"
      echo "  required estimate: ${REQUIRED_RELAY_PORTS} for ${MAX_CONCURRENT_SESSIONS} sessions"
      CHECK_STATUS=1
    fi
  else
    echo "TURN relay capacity: skipped, min-port/max-port unavailable"
  fi
else
  echo "TURN external IP: skipped, cannot read ${TURN_CONFIG_FILE}"
fi

if command -v nc >/dev/null 2>&1; then
  if nc -vz -w 3 "${HOST}" "${PORT}" >/dev/null 2>&1; then
    echo "TCP reachability: ok"
  else
    echo "TCP reachability: failed or UDP-only TURN server"
  fi
else
  echo "TCP reachability: skipped, nc not installed"
fi

exit "${CHECK_STATUS}"
