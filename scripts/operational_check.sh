#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/server/.env}"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi
BASE_URL="${HEALTHCHECK_BASE_URL:-https://127.0.0.1:10005}"
CERT_FILE="${SSL_CERT_FILE:-${ROOT_DIR}/ssl/fullchain.pem}"
[[ "${CERT_FILE}" = /* ]] || CERT_FILE="${ROOT_DIR}/server/${CERT_FILE}"
MIN_CERT_DAYS="${MIN_CERT_DAYS:-21}"
MAX_DISK_PERCENT="${MAX_DISK_PERCENT:-85}"

curl --fail --silent --show-error --insecure --max-time 15 \
  --retry 10 --retry-delay 1 --retry-connrefused "${BASE_URL%/}/ready" >/dev/null
openssl x509 -checkend "$((MIN_CERT_DAYS * 86400))" -noout -in "${CERT_FILE}" >/dev/null || {
  echo "CRITICAL: TLS certificate expires in less than ${MIN_CERT_DAYS} days" >&2
  exit 1
}
disk_percent="$(df -P "${ROOT_DIR}" | awk 'NR==2 {gsub("%", "", $5); print $5}')"
if (( disk_percent >= MAX_DISK_PERCENT )); then
  echo "CRITICAL: disk usage is ${disk_percent}%" >&2
  exit 1
fi
echo "OK: ready, certificate >${MIN_CERT_DAYS}d, disk ${disk_percent}%"
