from __future__ import annotations

import json
import re
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
    _MIGRATION_COLUMNS = {
        ("conversations", "conversation_hash"): "TEXT",
        ("conversations", "upstream_thread_id"): "TEXT",
    }

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
                    conversation_hash TEXT,
                    upstream_thread_id TEXT,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._ensure_column(conn, "conversations", "conversation_hash", "TEXT")
            self._ensure_column(conn, "conversations", "upstream_thread_id", "TEXT")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_conversation_hash
                ON conversations(conversation_hash)
                WHERE conversation_hash IS NOT NULL
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversations_upstream_thread
                ON conversations(source, upstream_thread_id)
                WHERE upstream_thread_id IS NOT NULL
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    conversation_id TEXT NOT NULL,
                    message_index INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    message_hash TEXT NOT NULL,
                    PRIMARY KEY (conversation_id, message_index),
                    UNIQUE (conversation_id, message_hash)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    message_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    index_state TEXT NOT NULL CHECK(index_state IN ('pending_index', 'indexed', 'indexing_failed')),
                    UNIQUE (conversation_id, chunk_index)
                )
                """
            )

    def _ensure_column(
        self, conn: sqlite3.Connection, table: str, column: str, declaration: str
    ) -> None:
        expected = self._MIGRATION_COLUMNS.get((table, column))
        if expected != declaration:
            raise ValueError("unsupported schema migration column")
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column not in {str(row["name"]) for row in rows}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def insert(self, conversation_json: dict[str, Any]) -> str:
        memory_id = self._validate_memory_id(conversation_json["id"])
        source = str(conversation_json.get("source", ""))
        timestamp = str(conversation_json.get("timestamp", ""))
        title = conversation_json.get("title")
        self._validate_field_lengths(source=source, timestamp=timestamp, title=title)
        conversation_hash = self._conversation_hash(conversation_json)
        upstream_thread_id = self._upstream_thread_id(conversation_json)
        payload = json.dumps(conversation_json, separators=(",", ":"), ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (id, source, timestamp, title, conversation_hash, upstream_thread_id, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    timestamp = excluded.timestamp,
                    title = excluded.title,
                    conversation_hash = excluded.conversation_hash,
                    upstream_thread_id = excluded.upstream_thread_id,
                    payload = excluded.payload
                """,
                (
                    memory_id,
                    source,
                    timestamp,
                    title,
                    conversation_hash,
                    upstream_thread_id,
                    payload,
                ),
            )
            self._replace_child_rows(conn, conversation_json)
        return memory_id

    def insert_new(self, conversation_json: dict[str, Any]) -> tuple[str, bool]:
        memory_id = self._validate_memory_id(conversation_json["id"])
        source = str(conversation_json.get("source", ""))
        timestamp = str(conversation_json.get("timestamp", ""))
        title = conversation_json.get("title")
        self._validate_field_lengths(source=source, timestamp=timestamp, title=title)
        conversation_hash = self._conversation_hash(conversation_json)
        upstream_thread_id = self._upstream_thread_id(conversation_json)
        payload = json.dumps(conversation_json, separators=(",", ":"), ensure_ascii=False)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO conversations (id, source, timestamp, title, conversation_hash, upstream_thread_id, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        source,
                        timestamp,
                        title,
                        conversation_hash,
                        upstream_thread_id,
                        payload,
                    ),
                )
                self._replace_child_rows(conn, conversation_json)
        except sqlite3.IntegrityError:
            existing = self.get_by_conversation_hash(conversation_hash)
            if existing is None:
                raise
            return str(existing["id"]), False
        return memory_id, True

    def append_messages(
        self, conversation_json: dict[str, Any], new_messages: list[dict[str, Any]]
    ) -> str:
        _ = new_messages
        return self.insert(conversation_json)

    def get_by_conversation_hash(self, conversation_hash: str | None) -> dict[str, Any] | None:
        if not conversation_hash:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM conversations WHERE conversation_hash = ?",
                (conversation_hash,),
            ).fetchone()
        if row is None:
            return None
        return json.loads(str(row["payload"]))

    def get_by_upstream_thread(
        self, source: str, upstream_thread_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT payload FROM conversations
                WHERE source = ? AND upstream_thread_id = ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (source, upstream_thread_id),
            ).fetchone()
        if row is None:
            return None
        return json.loads(str(row["payload"]))

    def is_fully_indexed(self, conversation_id: str) -> bool:
        validated_id = self._validate_memory_id(conversation_id)
        with self._connect() as conn:
            # Check if there are any chunks at all, and if any are NOT indexed
            row = conn.execute(
                """
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN index_state = 'indexed' THEN 1 ELSE 0 END) as indexed
                FROM chunks 
                WHERE conversation_id = ?
                """,
                (validated_id,),
            ).fetchone()
        if not row or row["total"] == 0:
            return False
        return row["total"] == row["indexed"]

    def mark_chunks_indexed(self, memory_id: str, chunk_ids: list[str]) -> None:
        self._mark_chunks_state(memory_id, chunk_ids, "indexed")

    def mark_chunks_indexing_failed(self, memory_id: str, chunk_ids: list[str]) -> None:
        self._mark_chunks_state(memory_id, chunk_ids, "indexing_failed")

    def _mark_chunks_state(
        self, memory_id: str, chunk_ids: list[str], state: str
    ) -> None:
        validated_id = self._validate_memory_id(memory_id)
        with self._connect() as conn:
            for chunk_id in chunk_ids:
                conn.execute(
                    """
                    UPDATE chunks
                    SET index_state = ?
                    WHERE conversation_id = ? AND chunk_id = ?
                    """,
                    (state, validated_id, chunk_id),
                )

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

    def search_text(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        tokens = _keyword_tokens(query)
        if not tokens:
            return []
        clauses = " AND ".join(["lower(payload) LIKE ?"] * len(tokens))
        params = [f"%{token}%" for token in tokens]
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT payload FROM conversations
                WHERE {clauses}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [json.loads(str(row["payload"])) for row in rows]

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
                    INSERT INTO conversations (id, source, timestamp, title, conversation_hash, upstream_thread_id, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source = excluded.source,
                        timestamp = excluded.timestamp,
                        title = excluded.title,
                        conversation_hash = excluded.conversation_hash,
                        upstream_thread_id = excluded.upstream_thread_id,
                        payload = excluded.payload
                    """,
                    (
                        memory_id,
                        source,
                        timestamp,
                        title,
                        self._conversation_hash(conversation_json),
                        self._upstream_thread_id(conversation_json),
                        payload,
                    ),
                )
                self._replace_child_rows(conn, conversation_json)
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

    def _conversation_hash(self, conversation_json: dict[str, Any]) -> str | None:
        metadata = conversation_json.get("metadata", {})
        if isinstance(metadata, dict):
            value = metadata.get("conversation_hash")
            return str(value) if value is not None else None
        return None

    def _upstream_thread_id(self, conversation_json: dict[str, Any]) -> str | None:
        metadata = conversation_json.get("metadata", {})
        if isinstance(metadata, dict):
            value = metadata.get("upstream_thread_id")
            return str(value) if value is not None else None
        return None

    def _replace_child_rows(
        self, conn: sqlite3.Connection, conversation_json: dict[str, Any]
    ) -> None:
        conversation_id = self._validate_memory_id(conversation_json["id"])
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM chunks WHERE conversation_id = ?", (conversation_id,))
        messages = conversation_json.get("messages", [])
        for index, message in enumerate(messages if isinstance(messages, list) else []):
            message_hash = str(message["hash"])
            role = str(message["role"])
            text = str(message["text"])
            conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (conversation_id, message_index, role, text, message_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, index, role, text, message_hash),
            )
        for chunk in self._index_chunks(conversation_json):
            conn.execute(
                """
                INSERT OR REPLACE INTO chunks
                    (chunk_id, conversation_id, chunk_index, message_hash, role, text, index_state)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(chunk["chunk_id"]),
                    conversation_id,
                    int(chunk["chunk_index"]),
                    str(chunk["message_hash"]),
                    str(chunk["role"]),
                    str(chunk["text"]),
                    str(chunk.get("index_state", "pending_index")),
                ),
            )

    def _index_chunks(self, conversation_json: dict[str, Any]) -> list[dict[str, Any]]:
        metadata = conversation_json.get("metadata", {})
        chunks = metadata.get("index_chunks") if isinstance(metadata, dict) else None
        if isinstance(chunks, list) and all(isinstance(chunk, dict) for chunk in chunks):
            return chunks
        fallback_chunks: list[dict[str, Any]] = []
        messages = conversation_json.get("messages", [])
        for index, message in enumerate(messages if isinstance(messages, list) else []):
            message_hash = str(message["hash"])
            fallback_chunks.append(
                {
                    "chunk_id": f"{conversation_json['id']}:{index}:{message_hash}",
                    "chunk_index": index,
                    "message_hash": message_hash,
                    "role": str(message["role"]),
                    "text": str(message["text"]),
                    "index_state": "pending_index",
                }
            )
        return fallback_chunks


def _keyword_tokens(query: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", query)
        if len(token) >= 2
    ][:8]
