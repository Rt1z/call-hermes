#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-https://127.0.0.1:10005}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required"
  exit 1
fi

python3 - "$BASE_URL" <<'PY'
import json
import ssl
import sys
import urllib.error
import urllib.request

base_url = sys.argv[1].rstrip("/")
ctx = ssl._create_unverified_context()
try:
    data = urllib.request.urlopen(f"{base_url}/health/details", timeout=10, context=ctx).read()
    health = json.loads(data)
except urllib.error.HTTPError as exc:
    if exc.code != 403:
        raise
    data = urllib.request.urlopen(f"{base_url}/health", timeout=10, context=ctx).read()
    health = json.loads(data)

print(f"health ok: {health.get('ok')}")
components = health.get("components", {})
for name, ok in components.items():
    print(f"{'ok' if ok else 'FAIL':4} {name}")

config = health.get("config")
if config is not None:
    print(f"config ok: {config.get('ok')}")
    for name, check in config.get("checks", {}).items():
        status = "ok" if check.get("ok") else "WARN"
        print(f"{status:4} {name}: {check.get('detail')}")
    errors = config.get("errors") or []
    if errors:
        print("config errors:", ", ".join(errors))
        raise SystemExit(1)

if not health.get("ok") or any(not ok for ok in components.values()):
    raise SystemExit(1)
PY
