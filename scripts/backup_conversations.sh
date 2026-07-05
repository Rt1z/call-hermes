#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/server/.env}"
BACKUP_DIR="${BACKUP_DIR:-${ROOT_DIR}/server/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

database_path="$( (grep -E '^CONVERSATION_DATABASE_PATH=' "${ENV_FILE}" || true) | tail -1 | cut -d= -f2-)"
database_path="${database_path:-data/conversations.sqlite3}"
if [[ "${database_path}" != /* ]]; then
  database_path="${ROOT_DIR}/server/${database_path}"
fi
if [ ! -f "${database_path}" ]; then
  echo "Conversation database not found: ${database_path}" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="${BACKUP_DIR}/conversations-${timestamp}.sqlite3"
sqlite3 "${database_path}" ".backup '${destination}'"
chmod 600 "${destination}"
sqlite3 "${destination}" "PRAGMA quick_check" | grep -qx ok
find "${BACKUP_DIR}" -type f -name 'conversations-*.sqlite3' -mtime "+${RETENTION_DAYS}" -delete
echo "Backup created: ${destination}"
