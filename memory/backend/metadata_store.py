from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from memory.backend.contracts import ProviderCapabilities
from memory.backend.errors import NotSupportedError


class SQLiteMetadataStore:
    schema_version: int = 1
    _MAX_GET_MANY_IDS = 500
    _MAX_SOURCE_LEN = 128
    _MAX_TIMESTAMP_LEN = 128
    _MAX_TITLE_LEN = 1024

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
        memory_id = self._validate_memory_id(conversation_json["id"])
        source = str(conversation_json.get("source", ""))
        timestamp = str(conversation_json.get("timestamp", ""))
        title = conversation_json.get("title")
        self._validate_field_lengths(source=source, timestamp=timestamp, title=title)
        payload = json.dumps(conversation_json, separators=(",", ":"), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO conversations (id, source, timestamp, title, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    source,
                    timestamp,
                    title,
                    payload,
                ),
            )
        return memory_id

    def get(self, memory_id: str) -> dict[str, Any] | None:
        validated_id = self._validate_memory_id(memory_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM conversations WHERE id = ?",
                (validated_id,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(str(row["payload"]))

    def get_many(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        if len(ids) > self._MAX_GET_MANY_IDS:
            raise ValueError(f"Too many ids requested: {len(ids)} > {self._MAX_GET_MANY_IDS}")
        validated_ids = [self._validate_memory_id(memory_id) for memory_id in ids]
        placeholders = ",".join(["?"] * len(ids))
        query = f"SELECT id, payload FROM conversations WHERE id IN ({placeholders})"
        with self._connect() as conn:
            rows = conn.execute(query, validated_ids).fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            result[str(row["id"])] = json.loads(str(row["payload"]))
        return result

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_transactions=True,
            supports_tags=True,
            supports_metadata_indexing=True,
        )

    def health(self) -> dict[str, Any]:
        return {
            "provider": "sqlite",
            "schema_version": self.schema_version,
        }

    def batch_insert(self, conversations: list[dict[str, Any]]) -> list[str]:
        # SQLite adapter supports transactional batch writes.
        ids: list[str] = []
        with self._connect() as conn:
            for conversation_json in conversations:
                memory_id = self._validate_memory_id(conversation_json["id"])
                source = str(conversation_json.get("source", ""))
                timestamp = str(conversation_json.get("timestamp", ""))
                title = conversation_json.get("title")
                self._validate_field_lengths(source=source, timestamp=timestamp, title=title)
                payload = json.dumps(conversation_json, separators=(",", ":"), ensure_ascii=False)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO conversations (id, source, timestamp, title, payload)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        source,
                        timestamp,
                        title,
                        payload,
                    ),
                )
                ids.append(memory_id)
        return ids

    def begin_transaction(self) -> None:
        raise NotSupportedError("Metadata transactions are not exposed by this adapter API")

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = (memory_id, ttl_seconds)
        raise NotSupportedError("TTL is not supported by this metadata adapter")

    def _validate_memory_id(self, memory_id: Any) -> str:
        value = str(memory_id)
        try:
            uuid.UUID(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("memory_id must be a valid UUID") from exc
        return value

    def _validate_field_lengths(self, *, source: str, timestamp: str, title: Any) -> None:
        if len(source) > self._MAX_SOURCE_LEN:
            raise ValueError(f"source exceeds max length {self._MAX_SOURCE_LEN}")
        if len(timestamp) > self._MAX_TIMESTAMP_LEN:
            raise ValueError(f"timestamp exceeds max length {self._MAX_TIMESTAMP_LEN}")
        if title is not None and len(str(title)) > self._MAX_TITLE_LEN:
            raise ValueError(f"title exceeds max length {self._MAX_TITLE_LEN}")
