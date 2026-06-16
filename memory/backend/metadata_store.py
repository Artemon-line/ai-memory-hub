from __future__ import annotations

import hashlib
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
        ("facts", "source_quality"): "TEXT",
        ("facts", "confidence_reason"): "TEXT",
        ("facts", "last_confirmed_at"): "TEXT",
        ("facts", "superseded_at"): "TEXT",
        ("facts", "object_raw"): "TEXT",
        ("facts", "object_normalized"): "TEXT",
        ("facts", "owner_id"): "TEXT",
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
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    display_name TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    disabled_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_tokens (
                    token_hash TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    expires_at TEXT,
                    revoked_at TEXT,
                    FOREIGN KEY(owner_id) REFERENCES users(id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_auth_tokens_owner
                ON auth_tokens(owner_id)
                WHERE revoked_at IS NULL
                """
            )
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    qualifiers TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    source_conversation_id TEXT NOT NULL,
                    owner_id TEXT,
                    source_message_indexes TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    superseded_by TEXT,
                    superseded_at TEXT,
                    source_quality TEXT,
                    confidence_reason TEXT,
                    last_confirmed_at TEXT,
                    object_raw TEXT,
                    object_normalized TEXT,
                    deleted_at TEXT
                )
                """
            )
            for column, declaration in (
                ("source_quality", "TEXT"),
                ("confidence_reason", "TEXT"),
                ("last_confirmed_at", "TEXT"),
                ("superseded_at", "TEXT"),
                ("object_raw", "TEXT"),
                ("object_normalized", "TEXT"),
                ("owner_id", "TEXT"),
            ):
                self._ensure_column(conn, "facts", column, declaration)
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate
                ON facts(subject, predicate)
                WHERE deleted_at IS NULL
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

    def create_auth_token(
        self, *, owner_id: str, token: str, display_name: str | None = None
    ) -> None:
        owner = _validate_owner_id(owner_id)
        token_hash = _hash_bearer_token(token)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (id, display_name)
                VALUES (?, ?)
                ON CONFLICT(id) DO UPDATE SET display_name = excluded.display_name
                """,
                (owner, display_name),
            )
            conn.execute(
                """
                INSERT INTO auth_tokens (token_hash, owner_id)
                VALUES (?, ?)
                ON CONFLICT(token_hash) DO UPDATE SET owner_id = excluded.owner_id,
                    revoked_at = NULL
                """,
                (token_hash, owner),
            )

    def owner_for_token(self, token: str) -> str | None:
        token_hash = _hash_bearer_token(token)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT auth_tokens.owner_id
                FROM auth_tokens
                JOIN users ON users.id = auth_tokens.owner_id
                WHERE auth_tokens.token_hash = ?
                  AND auth_tokens.revoked_at IS NULL
                  AND users.disabled_at IS NULL
                  AND (auth_tokens.expires_at IS NULL OR auth_tokens.expires_at > CURRENT_TIMESTAMP)
                """,
                (token_hash,),
            ).fetchone()
        return str(row["owner_id"]) if row is not None else None

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
            return self._resolve_insert_new_conflict(
                memory_id=memory_id,
                conversation_hash=conversation_hash,
            )
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

    def insert_facts(self, facts: list[dict[str, Any]]) -> None:
        if not facts:
            return
        with self._connect() as conn:
            for fact in facts:
                self._refresh_matching_fact_confirmation(conn, fact)
                self._supersede_corrected_facts(conn, fact)
                conn.execute(
                    """
                    INSERT INTO facts (
                        id, subject, predicate, object, qualifiers, confidence,
                        source_conversation_id, owner_id, source_message_indexes,
                        created_at, updated_at, superseded_by, superseded_at,
                        source_quality, confidence_reason, last_confirmed_at,
                        object_raw, object_normalized, deleted_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        subject = excluded.subject,
                        predicate = excluded.predicate,
                        object = excluded.object,
                        qualifiers = excluded.qualifiers,
                        confidence = excluded.confidence,
                        source_conversation_id = excluded.source_conversation_id,
                        owner_id = excluded.owner_id,
                        source_message_indexes = excluded.source_message_indexes,
                        updated_at = excluded.updated_at,
                        superseded_by = excluded.superseded_by,
                        superseded_at = excluded.superseded_at,
                        source_quality = excluded.source_quality,
                        confidence_reason = excluded.confidence_reason,
                        last_confirmed_at = excluded.last_confirmed_at,
                        object_raw = excluded.object_raw,
                        object_normalized = excluded.object_normalized,
                        deleted_at = excluded.deleted_at
                    """,
                    self._fact_row(fact),
                )

    def search_facts(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = ["deleted_at IS NULL"]
        params: list[Any] = []
        if subject is not None:
            clauses.append("subject = ?")
            params.append(subject)
        if predicate is not None:
            clauses.append("predicate = ?")
            params.append(predicate)
        if not include_superseded:
            clauses.append("superseded_by IS NULL")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM facts
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC, id ASC
                """,
                params,
            ).fetchall()
        return [self._fact_from_row(row) for row in rows]

    def profile_get(self, subject: str = "user") -> dict[str, Any]:
        return {"subject": subject, "facts": self.search_facts(subject=subject)}

    def supersede_fact(self, fact_id: str, superseded_by: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE facts
                SET superseded_by = ?, superseded_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND deleted_at IS NULL
                """,
                (superseded_by, fact_id),
            )
            return cursor.rowcount > 0

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

    def _resolve_insert_new_conflict(
        self, *, memory_id: str, conversation_hash: str | None
    ) -> tuple[str, bool]:
        existing = self.get_by_conversation_hash(conversation_hash)
        if existing is None:
            existing = self.get(memory_id)
        if existing is None:
            raise sqlite3.IntegrityError("conversation insert conflict could not be resolved")
        if self._conversation_hash(existing) != conversation_hash:
            raise ValueError("unauthorized_update: conversation id already exists")
        return str(existing["id"]), False

    def _supersede_corrected_facts(
        self, conn: sqlite3.Connection, fact: dict[str, Any]
    ) -> None:
        qualifiers = fact.get("qualifiers", {})
        corrects = qualifiers.get("corrects") if isinstance(qualifiers, dict) else None
        if not corrects:
            return
        conn.execute(
            """
            UPDATE facts
            SET superseded_by = ?, superseded_at = ?, updated_at = ?
            WHERE subject = ?
              AND predicate = ?
              AND (owner_id IS ? OR owner_id = ?)
              AND superseded_by IS NULL
              AND deleted_at IS NULL
              AND lower(object) LIKE ?
            """,
            (
                str(fact["id"]),
                str(fact["updated_at"]),
                str(fact["updated_at"]),
                str(fact["subject"]),
                str(fact["predicate"]),
                fact.get("owner_id"),
                fact.get("owner_id"),
                f"%{str(corrects).lower()}%",
            ),
        )

    def _refresh_matching_fact_confirmation(
        self, conn: sqlite3.Connection, fact: dict[str, Any]
    ) -> None:
        if fact.get("source_quality") != "direct_user_statement":
            return
        conn.execute(
            """
            UPDATE facts
            SET last_confirmed_at = ?, updated_at = ?
            WHERE subject = ?
              AND predicate = ?
              AND object = ?
              AND (owner_id IS ? OR owner_id = ?)
              AND superseded_by IS NULL
              AND deleted_at IS NULL
            """,
            (
                str(fact["last_confirmed_at"]),
                str(fact["updated_at"]),
                str(fact["subject"]),
                str(fact["predicate"]),
                str(fact["object"]),
                fact.get("owner_id"),
                fact.get("owner_id"),
            ),
        )

    def _fact_row(self, fact: dict[str, Any]) -> tuple[Any, ...]:
        return (
            str(fact["id"]),
            str(fact["subject"]),
            str(fact["predicate"]),
            str(fact["object"]),
            json.dumps(fact.get("qualifiers", {}), separators=(",", ":"), ensure_ascii=False),
            str(fact.get("confidence", "medium")),
            str(fact["source_conversation_id"]),
            fact.get("owner_id"),
            json.dumps(fact.get("source_message_indexes", []), separators=(",", ":")),
            str(fact["created_at"]),
            str(fact["updated_at"]),
            fact.get("superseded_by"),
            fact.get("superseded_at"),
            fact.get("source_quality"),
            fact.get("confidence_reason"),
            fact.get("last_confirmed_at"),
            fact.get("object_raw", fact.get("object")),
            fact.get("object_normalized", fact.get("object")),
            fact.get("deleted_at"),
        )

    def _fact_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "subject": str(row["subject"]),
            "predicate": str(row["predicate"]),
            "object": str(row["object"]),
            "qualifiers": json.loads(str(row["qualifiers"])),
            "confidence": str(row["confidence"]),
            "source_conversation_id": str(row["source_conversation_id"]),
            "owner_id": row["owner_id"],
            "source_message_indexes": json.loads(str(row["source_message_indexes"])),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "superseded_by": row["superseded_by"],
            "superseded_at": row["superseded_at"],
            "source_quality": row["source_quality"],
            "confidence_reason": row["confidence_reason"],
            "last_confirmed_at": row["last_confirmed_at"],
            "object_raw": row["object_raw"],
            "object_normalized": row["object_normalized"],
            "deleted_at": row["deleted_at"],
        }

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


def _hash_bearer_token(token: str) -> str:
    if not isinstance(token, str) or not token:
        raise ValueError("bearer token must be a non-empty string")
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_owner_id(owner_id: str) -> str:
    value = str(owner_id).strip()
    if not value:
        raise ValueError("owner_id must be non-empty")
    if len(value) > 128:
        raise ValueError("owner_id exceeds max length 128")
    return value
