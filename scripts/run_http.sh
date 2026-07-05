#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER_DIR="${ROOT_DIR}/server"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"

cd "${SERVER_DIR}"

if [ ! -d ".venv" ]; then
  "${PYTHON_BIN}" -m venv .venv
fi

./.venv/bin/pip install -q -e ".[dev]"

exec ./.venv/bin/uvicorn app.main:app --host "${HOST}" --port "${PORT}"
