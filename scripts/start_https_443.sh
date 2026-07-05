#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="${ROOT_DIR}/server"
LOG_FILE="${SERVER_DIR}/voice-bridge-https.log"
PID_FILE="${SERVER_DIR}/voice-bridge-https.pid"

cd "${SERVER_DIR}"

if [ ! -d ".venv" ]; then
  python3.11 -m venv .venv
fi

./.venv/bin/pip install -q -e ".[dev]"

# The log file remains owned by the invoking service user.
# shellcheck disable=SC2024
sudo -n setsid "${SERVER_DIR}/.venv/bin/uvicorn" app.main:app \
  --host 0.0.0.0 \
  --port 443 \
  --ssl-certfile "${CERT_FILE:-${ROOT_DIR}/ssl/fullchain.pem}" \
  --ssl-keyfile "${KEY_FILE:-${ROOT_DIR}/ssl/privkey.pem}" \
  > "${LOG_FILE}" 2>&1 < /dev/null &

sleep 1
pgrep -f "uvicorn app.main:app --host 0.0.0.0 --port 443" | head -1 > "${PID_FILE}"
echo "Started HTTPS voice bridge on port 443, pid $(cat "${PID_FILE}")"
