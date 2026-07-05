#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="${ROOT_DIR}/server"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-10005}"
CERT_FILE="${CERT_FILE:-${ROOT_DIR}/ssl/fullchain.pem}"
KEY_FILE="${KEY_FILE:-${ROOT_DIR}/ssl/privkey.pem}"
LOG_FILE="${LOG_FILE:-${SERVER_DIR}/voice-bridge-https.log}"
PID_FILE="${PID_FILE:-${SERVER_DIR}/voice-bridge-https.pid}"

if command -v systemctl >/dev/null 2>&1 && systemctl cat call-hermes.service >/dev/null 2>&1; then
  sudo systemctl restart call-hermes.service
  echo "Started call-hermes.service via systemd"
  exit 0
fi

cd "${SERVER_DIR}"

if [ ! -d ".venv" ]; then
  "${PYTHON_BIN}" -m venv .venv
fi

./.venv/bin/pip install -q -e ".[dev]"

setsid .venv/bin/uvicorn app.main:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --ssl-certfile "${CERT_FILE}" \
  --ssl-keyfile "${KEY_FILE}" \
  > "${LOG_FILE}" 2>&1 < /dev/null &

sleep 1
pgrep -f "uvicorn app.main:app --host ${HOST} --port ${PORT}" | head -1 > "${PID_FILE}"
echo "Started HTTPS voice bridge on port ${PORT}, pid $(cat "${PID_FILE}")"
