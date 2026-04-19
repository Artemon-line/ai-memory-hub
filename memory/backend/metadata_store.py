from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class SQLiteMetadataStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    title TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def insert(self, conversation_json: dict[str, Any]) -> str:
        memory_id = str(conversation_json["id"])
        payload = json.dumps(conversation_json, separators=(",", ":"), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO conversations (id, source, timestamp, title, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    str(conversation_json.get("source", "")),
                    str(conversation_json.get("timestamp", "")),
                    conversation_json.get("title"),
                    payload,
                ),
            )
        return memory_id

    def get(self, memory_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM conversations WHERE id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(str(row["payload"]))

    def get_many(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        placeholders = ",".join(["?"] * len(ids))
        query = f"SELECT id, payload FROM conversations WHERE id IN ({placeholders})"
        with self._connect() as conn:
            rows = conn.execute(query, ids).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            result[str(row["id"])] = json.loads(str(row["payload"]))
        return result
