#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${PID_FILE:-${ROOT_DIR}/server/voice-bridge-https.pid}"

if command -v systemctl >/dev/null 2>&1 && systemctl cat call-hermes.service >/dev/null 2>&1; then
  sudo systemctl stop call-hermes.service
  echo "Stopped call-hermes.service via systemd"
  exit 0
fi

if [ ! -f "${PID_FILE}" ]; then
  echo "No PID file found: ${PID_FILE}"
  exit 0
fi

PID="$(cat "${PID_FILE}")"
if kill -0 "${PID}" 2>/dev/null; then
  kill "${PID}"
  echo "Stopped HTTPS voice bridge pid ${PID}"
elif sudo -n kill -0 "${PID}" 2>/dev/null; then
  sudo -n kill "${PID}"
  echo "Stopped HTTPS voice bridge pid ${PID}"
else
  echo "Process ${PID} is not running"
fi
rm -f "${PID_FILE}"
