import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


class AccountStore:
    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path)
        self._lock = Lock()

    def initialize(self, bootstrap_username: str, bootstrap_password: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
                    created_at TEXT NOT NULL, disabled_at TEXT
                );
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
                    created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, revoked_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id)
                );
                CREATE INDEX IF NOT EXISTS devices_user_idx ON devices(user_id);
                CREATE TABLE IF NOT EXISTS refresh_tokens (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, device_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL, created_at TEXT NOT NULL, revoked_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(device_id) REFERENCES devices(id)
                );
                CREATE INDEX IF NOT EXISTS refresh_device_idx ON refresh_tokens(device_id);
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, action TEXT NOT NULL,
                    device_id TEXT, ip_address TEXT, details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            existing = connection.execute("SELECT id FROM users WHERE username = ?", (bootstrap_username,)).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO users(id, username, password_hash, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
                    (uuid4().hex, bootstrap_username, _password_hash(bootstrap_password), _now()),
                )

    def authenticate(self, username: str, password: str) -> dict[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, username, password_hash, role FROM users WHERE username = ? AND disabled_at IS NULL",
                (username,),
            ).fetchone()
        if row is None or not _verify_password(password, row["password_hash"]):
            return None
        return {"id": row["id"], "username": row["username"], "role": row["role"]}

    def get_user(self, username: str) -> dict[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, username, role FROM users WHERE username = ? AND disabled_at IS NULL",
                (username,),
            ).fetchone()
        return dict(row) if row else None

    def create_user(self, username: str, password: str, role: str = "user") -> dict[str, str]:
        user_id = uuid4().hex
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO users(id, username, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, _password_hash(password), role, _now()),
            )
        return {"id": user_id, "username": username, "role": role}

    def list_users(self) -> list[dict[str, str | None]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, username, role, created_at, disabled_at FROM users ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def change_password(self, user_id: str, current_password: str, new_password: str) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None or not _verify_password(current_password, row["password_hash"]):
                return False
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?", (_password_hash(new_password), user_id)
            )
        return True

    def register_device(self, user_id: str, name: str, device_id: str | None = None) -> str:
        now = _now()
        with self._connect() as connection:
            if device_id:
                row = connection.execute(
                    "SELECT id FROM devices WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                    (device_id, user_id),
                ).fetchone()
                if row:
                    connection.execute(
                        "UPDATE devices SET name = ?, last_seen_at = ? WHERE id = ?", (name[:200], now, device_id)
                    )
                    return device_id
            device_id = uuid4().hex
            connection.execute(
                "INSERT INTO devices(id, user_id, name, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                (device_id, user_id, name[:200] or "Unknown device", now, now),
            )
        return device_id

    def issue_refresh_token(self, user_id: str, device_id: str, ttl_days: int) -> tuple[str, str]:
        token = secrets.token_urlsafe(48)
        expires = datetime.now(UTC) + timedelta(days=ttl_days)
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO refresh_tokens(token_hash, user_id, device_id, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                (self._token_hash(token), user_id, device_id, expires.isoformat(), _now()),
            )
        return token, expires.isoformat()

    def revoke_device_tokens(self, device_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE refresh_tokens SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
                (_now(), device_id),
            )

    def consume_refresh_token(self, token: str) -> dict[str, str] | None:
        token_hash = self._token_hash(token)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT r.user_id, r.device_id, r.expires_at, u.username, u.role, d.name
                   FROM refresh_tokens r JOIN users u ON u.id = r.user_id
                   JOIN devices d ON d.id = r.device_id
                   WHERE r.token_hash = ? AND r.revoked_at IS NULL AND d.revoked_at IS NULL
                   AND u.disabled_at IS NULL""",
                (token_hash,),
            ).fetchone()
            if row is None or datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
                return None
            connection.execute("UPDATE refresh_tokens SET revoked_at = ? WHERE token_hash = ?", (_now(), token_hash))
        return dict(row)

    def device_active(self, user_id: str, device_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM devices WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                (device_id, user_id),
            ).fetchone()
        return row is not None

    def list_devices(self, user_id: str, current_device_id: str) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, name, created_at, last_seen_at, revoked_at FROM devices WHERE user_id = ? ORDER BY last_seen_at DESC",
                (user_id,),
            ).fetchall()
        return [{**dict(row), "current": row["id"] == current_device_id} for row in rows]

    def revoke_device(self, user_id: str, device_id: str) -> bool:
        now = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE devices SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                (now, device_id, user_id),
            )
            connection.execute(
                "UPDATE refresh_tokens SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
                (now, device_id),
            )
        return cursor.rowcount > 0

    def revoke_refresh_token(self, token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE refresh_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (_now(), self._token_hash(token)),
            )

    def audit(
        self,
        action: str,
        user_id: str | None = None,
        device_id: str | None = None,
        ip_address: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO audit_log(user_id, action, device_id, ip_address, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, action, device_id, ip_address, json.dumps(details or {}, ensure_ascii=False), _now()),
            )

    def list_audit(self, user_id: str, limit: int = 100) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT action, device_id, ip_address, details_json, created_at FROM audit_log WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, min(max(limit, 1), 500)),
            ).fetchall()
        return [{**dict(row), "details": json.loads(row["details_json"])} for row in rows]

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
