#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${ROOT_DIR}/deploy/call-hermes.service.in"
SERVICE_NAME="${SERVICE_NAME:-call-hermes.service}"
ENV_FILE="${ROOT_DIR}/server/.env"

read_env() {
  local key="$1"
  (grep -E "^${key}=" "${ENV_FILE}" || true) | tail -1 | cut -d= -f2-
}

resolve_path() {
  local value="$1"
  if [[ "${value}" = /* ]]; then
    printf '%s\n' "${value}"
  else
    realpath -m "${ROOT_DIR}/server/${value}"
  fi
}

user_name="${SERVICE_USER:-$(id -un)}"
group_name="${SERVICE_GROUP:-$(id -gn)}"
cert_value="$(read_env SSL_CERT_FILE)"
key_value="$(read_env SSL_KEY_FILE)"
cert_file="$(resolve_path "${cert_value:-../ssl/fullchain.pem}")"
key_file="$(resolve_path "${key_value:-../ssl/privkey.pem}")"
if [ ! -f "${cert_file}" ] || [ ! -r "${cert_file}" ] || [ ! -f "${key_file}" ] || [ ! -r "${key_file}" ]; then
  echo "TLS certificate or key is not readable" >&2
  exit 1
fi

rendered="$(mktemp)"
trap 'rm -f "${rendered}"' EXIT
sed \
  -e "s|@USER@|${user_name}|g" \
  -e "s|@GROUP@|${group_name}|g" \
  -e "s|@ROOT@|${ROOT_DIR}|g" \
  -e "s|@CERT@|${cert_file}|g" \
  -e "s|@KEY@|${key_file}|g" \
  "${TEMPLATE}" > "${rendered}"

sudo install -m 0644 "${rendered}" "/etc/systemd/system/${SERVICE_NAME}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
for unit in call-hermes-ops call-hermes-backup; do
  unit_rendered="$(mktemp)"
  sed -e "s|@ROOT@|${ROOT_DIR}|g" -e "s|@USER@|${user_name}|g" \
    "${ROOT_DIR}/deploy/${unit}.service.in" > "${unit_rendered}"
  sudo install -m 0644 "${unit_rendered}" "/etc/systemd/system/${unit}.service"
  rm -f "${unit_rendered}"
  sudo install -m 0644 "${ROOT_DIR}/deploy/${unit}.timer" "/etc/systemd/system/${unit}.timer"
done
sudo systemctl daemon-reload
sudo systemctl enable --now call-hermes-ops.timer call-hermes-backup.timer
sudo systemctl --no-pager --full status "${SERVICE_NAME}"
