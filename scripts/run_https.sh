#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="${ROOT_DIR}/server"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-10005}"
CERT_FILE="${CERT_FILE:-${ROOT_DIR}/ssl/fullchain.pem}"
KEY_FILE="${KEY_FILE:-${ROOT_DIR}/ssl/privkey.pem}"

if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet call-hermes.service; then
  echo "call-hermes.service is already running; stop it before foreground startup" >&2
  exit 1
fi

cd "${SERVER_DIR}"

if [ ! -d ".venv" ]; then
  "${PYTHON_BIN}" -m venv .venv
fi

./.venv/bin/pip install -q -e ".[dev]"

exec ./.venv/bin/uvicorn app.main:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --ssl-certfile "${CERT_FILE}" \
  --ssl-keyfile "${KEY_FILE}"
