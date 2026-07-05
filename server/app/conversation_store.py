import json
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Any


class ConversationStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._lock = Lock()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    messages_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(conversations)")}
            for name, definition in (
                ("owner_id", "TEXT NOT NULL DEFAULT 'legacy'"),
                ("title", "TEXT"),
                ("favorite", "INTEGER NOT NULL DEFAULT 0"),
                ("archived", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE conversations ADD COLUMN {name} {definition}")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS conversations_owner_updated_idx ON conversations(owner_id, updated_at DESC)"
            )

    def load(self, conversation_id: str, owner_id: str = "legacy") -> list[dict[str, str]]:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT messages_json FROM conversations WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            ).fetchone()
        if not row:
            return []
        try:
            return _valid_messages(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError):
            return []

    def save(
        self, conversation_id: str, messages: list[dict[str, str]], owner_id: str = "legacy"
    ) -> None:
        payload = json.dumps(_valid_messages(messages), ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversations (conversation_id, messages_json, owner_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    messages_json = excluded.messages_json,
                    owner_id = excluded.owner_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (conversation_id, payload, owner_id),
            )

    def clear(self, conversation_id: str, owner_id: str = "legacy") -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ? AND owner_id = ?",
                (conversation_id, owner_id),
            )

    def list(
        self, query: str = "", limit: int = 50, offset: int = 0, owner_id: str = "legacy"
    ) -> list[dict[str, object]]:
        sql = (
            "SELECT conversation_id, messages_json, updated_at, title, favorite, archived "
            "FROM conversations WHERE owner_id = ?"
        )
        parameters: list[object] = [owner_id]
        if query:
            sql += " AND (messages_json LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\')"
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.extend([f"%{escaped}%", f"%{escaped}%"])
        sql += " ORDER BY favorite DESC, updated_at DESC, conversation_id DESC LIMIT ? OFFSET ?"
        parameters.extend([max(1, min(100, limit)), max(0, offset)])
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        summaries: list[dict[str, object]] = []
        for conversation_id, payload, updated_at, title, favorite, archived in rows:
            try:
                messages = _valid_messages(json.loads(payload))
            except (json.JSONDecodeError, TypeError):
                messages = []
            first_user = next(
                (message["content"] for message in messages if message["role"] == "user"),
                "Untitled conversation",
            )
            summaries.append(
                {
                    "conversation_id": conversation_id,
                    "title": title or first_user[:80],
                    "turn_count": sum(message["role"] == "user" for message in messages),
                    "updated_at": updated_at,
                    "favorite": bool(favorite),
                    "archived": bool(archived),
                }
            )
        return summaries

    def update_metadata(
        self,
        conversation_id: str,
        owner_id: str,
        *,
        title: str | None = None,
        favorite: bool | None = None,
        archived: bool | None = None,
    ) -> bool:
        if title is None and favorite is None and archived is None:
            return False
        with self._lock, self._connect() as connection:
            updated = 0
            if title is not None:
                updated += connection.execute(
                    "UPDATE conversations SET title = ? WHERE conversation_id = ? AND owner_id = ?",
                    (title[:120], conversation_id, owner_id),
                ).rowcount
            if favorite is not None:
                updated += connection.execute(
                    "UPDATE conversations SET favorite = ? WHERE conversation_id = ? AND owner_id = ?",
                    (int(favorite), conversation_id, owner_id),
                ).rowcount
            if archived is not None:
                updated += connection.execute(
                    "UPDATE conversations SET archived = ? WHERE conversation_id = ? AND owner_id = ?",
                    (int(archived), conversation_id, owner_id),
                ).rowcount
        return updated > 0

    def health(self) -> tuple[bool, str]:
        try:
            with self._lock, self._connect() as connection:
                result = connection.execute("PRAGMA quick_check").fetchone()
            detail = str(result[0]) if result else "no result"
            return detail == "ok", detail
        except sqlite3.Error as exc:
            return False, str(exc)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5)


def _valid_messages(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    messages: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content:
            messages.append({"role": role, "content": content})
    return messages
