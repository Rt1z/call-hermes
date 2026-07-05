#!/usr/bin/env python3
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: scripts/smoke_pwa_turn.py /path/to/audio")
        return 2

    audio_path = Path(sys.argv[1]).expanduser()
    if not audio_path.exists():
        print(f"audio file not found: {audio_path}")
        return 2

    root = Path(__file__).resolve().parents[1]
    env = read_env(root / "server/.env")
    base_url = os.environ.get(
        "BASE_URL", env.get("PUBLIC_BASE_URL", "https://127.0.0.1:10005")
    ).rstrip("/")
    ctx = ssl._create_unverified_context()

    auth_body = json.dumps(
        {
            "username": env.get("BOOTSTRAP_ADMIN_USERNAME", "admin"),
            "password": env["APP_SHARED_SECRET"],
            "device_name": "smoke",
        }
    ).encode()
    auth_req = urllib.request.Request(
        f"{base_url}/auth/login",
        data=auth_body,
        headers={"Content-Type": "application/json"},
    )
    token = json.loads(urllib.request.urlopen(auth_req, timeout=10, context=ctx).read())["token"]

    boundary = "----callhermes"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="audio"; '
                f'filename="{audio_path.name}"\r\n'
            ).encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            audio_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    turn_req = urllib.request.Request(
        f"{base_url}/pwa/turn",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        result = json.loads(urllib.request.urlopen(turn_req, timeout=150, context=ctx).read())
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode(errors='ignore')}")
        return 1

    print("OK")
    print(f"turn_id: {result.get('turn_id', '')}")
    print(f"transcript: {result.get('transcript', '')[:160]}")
    print(f"answer_len: {len(result.get('answer', ''))}")
    print(f"audio_base64_len: {len(result.get('audio_base64', ''))}")
    print(f"timings: {result.get('timings', {})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
