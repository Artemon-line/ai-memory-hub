from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable

from memory.backend.contracts import ProviderCapabilities
from memory.backend.errors import SchemaVersionError

CREATE_CONVERSATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    title TEXT NULL,
    conversation_hash TEXT NULL UNIQUE,
    upstream_thread_id TEXT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_UPSTREAM_THREAD_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_conversations_upstream_thread
ON conversations(source, upstream_thread_id)
WHERE upstream_thread_id IS NOT NULL
"""

CREATE_SCHEMA_VERSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""
CREATE_FACTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    qualifiers JSONB NOT NULL,
    confidence TEXT NOT NULL,
    source_conversation_id TEXT NOT NULL,
    source_message_indexes JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    superseded_by TEXT NULL,
    superseded_at TIMESTAMPTZ NULL,
    source_quality TEXT NULL,
    confidence_reason TEXT NULL,
    last_confirmed_at TIMESTAMPTZ NULL,
    object_raw TEXT NULL,
    object_normalized TEXT NULL,
    deleted_at TIMESTAMPTZ NULL
)
"""
CREATE_FACTS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate
ON facts(subject, predicate)
WHERE deleted_at IS NULL
"""

SEED_SCHEMA_VERSION_SQL = """
INSERT INTO schema_version (id, version)
VALUES (%s, %s)
ON CONFLICT (id) DO NOTHING
"""

INSERT_CONVERSATION_SQL = """
INSERT INTO conversations (id, source, timestamp, title, conversation_hash, upstream_thread_id, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (id) DO UPDATE
SET source = EXCLUDED.source,
    timestamp = EXCLUDED.timestamp,
    title = EXCLUDED.title,
    conversation_hash = EXCLUDED.conversation_hash,
    upstream_thread_id = EXCLUDED.upstream_thread_id,
    payload = EXCLUDED.payload
"""

INSERT_NEW_CONVERSATION_SQL = """
INSERT INTO conversations (id, source, timestamp, title, conversation_hash, upstream_thread_id, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
"""

GET_CONVERSATION_SQL = "SELECT payload::text FROM conversations WHERE id = %s"
GET_MANY_CONVERSATIONS_SQL = "SELECT id, payload::text FROM conversations WHERE id = ANY(%s)"
GET_CONVERSATION_BY_HASH_SQL = "SELECT payload::text FROM conversations WHERE conversation_hash = %s"
GET_CONVERSATION_BY_UPSTREAM_THREAD_SQL = """
SELECT payload::text FROM conversations
WHERE source = %s AND upstream_thread_id = %s
ORDER BY created_at ASC
LIMIT 1
"""
COUNT_SCHEMA_ROWS_SQL = "SELECT COUNT(*) FROM schema_version"
READ_SCHEMA_VERSION_SQL = "SELECT version FROM schema_version WHERE id = %s"


class PostgresMetadataStore:
    _MAX_GET_MANY_IDS = 500

    def __init__(
        self,
        dsn: str,
        *,
        schema_version_seed: int = 1,
        connect_fn: Callable[[str], Any] | None = None,
    ):
        if not dsn:
            raise ValueError("Postgres metadata DSN is required")
        self._dsn = dsn
        self._connect_fn = connect_fn
        self._schema_version_seed = schema_version_seed
        self._init_db()
        self.schema_version = self._read_schema_version()

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn(self._dsn)
        try:
            import importlib
            psycopg = importlib.import_module("psycopg")
        except ImportError as exc:
            raise RuntimeError("psycopg package is required for providers.metadata_db=postgres") from exc
        return psycopg.connect(self._dsn)

    def _init_db(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(CREATE_CONVERSATIONS_TABLE_SQL)
                cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS conversation_hash TEXT UNIQUE")
                cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS upstream_thread_id TEXT NULL")
                cur.execute(CREATE_UPSTREAM_THREAD_INDEX_SQL)
                cur.execute(CREATE_SCHEMA_VERSION_TABLE_SQL)
                cur.execute(CREATE_FACTS_TABLE_SQL)
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS source_quality TEXT NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS confidence_reason TEXT NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS last_confirmed_at TIMESTAMPTZ NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS object_raw TEXT NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS object_normalized TEXT NULL")
                cur.execute(CREATE_FACTS_INDEX_SQL)
                cur.execute(SEED_SCHEMA_VERSION_SQL, (1, self._schema_version_seed))

    def _read_schema_version(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(COUNT_SCHEMA_ROWS_SQL)
                count_row = cur.fetchone()
                total = int(count_row[0]) if count_row is not None else 0
                if total != 1:
                    raise SchemaVersionError(
                        f"schema_version invariant violated: expected 1 row, found {total}"
                    )
                cur.execute(READ_SCHEMA_VERSION_SQL, (1,))
                row = cur.fetchone()
                if row is None:
                    raise SchemaVersionError("schema_version row missing for id=1")
                return int(row[0])

    def insert(self, conversation_json: dict[str, Any]) -> str:
        memory_id = self._validate_memory_id(conversation_json["id"])
        payload = json.dumps(conversation_json, separators=(",", ":"), ensure_ascii=False)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    INSERT_CONVERSATION_SQL,
                    (
                        memory_id,
                        str(conversation_json.get("source", "")),
                        str(conversation_json.get("timestamp", "")),
                        conversation_json.get("title"),
                        self._conversation_hash(conversation_json),
                        self._upstream_thread_id(conversation_json),
                        payload,
                    ),
                )
        return memory_id

    def insert_new(self, conversation_json: dict[str, Any]) -> tuple[str, bool]:
        memory_id = self._validate_memory_id(conversation_json["id"])
        payload = json.dumps(conversation_json, separators=(",", ":"), ensure_ascii=False)
        conversation_hash = self._conversation_hash(conversation_json)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        INSERT_NEW_CONVERSATION_SQL,
                        (
                            memory_id,
                            str(conversation_json.get("source", "")),
                            str(conversation_json.get("timestamp", "")),
                            conversation_json.get("title"),
                            conversation_hash,
                            self._upstream_thread_id(conversation_json),
                            payload,
                        ),
                    )
        except Exception as exc:
            return self._handle_insert_new_error(
                exc,
                memory_id=memory_id,
                conversation_hash=conversation_hash,
            )
        return memory_id, True

    def append_messages(
        self, conversation_json: dict[str, Any], new_messages: list[dict[str, Any]]
    ) -> str:
        _ = new_messages
        return self.insert(conversation_json)

    def get(self, memory_id: str) -> dict[str, Any] | None:
        validated = self._validate_memory_id(memory_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(GET_CONVERSATION_SQL, (validated,))
                row = cur.fetchone()
        if row is None:
            return None
        return json.loads(str(row[0]))

    def get_many(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        if len(ids) > self._MAX_GET_MANY_IDS:
            raise ValueError(f"Too many ids requested: {len(ids)} > {self._MAX_GET_MANY_IDS}")
        validated = [self._validate_memory_id(memory_id) for memory_id in ids]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(GET_MANY_CONVERSATIONS_SQL, (validated,))
                rows = cur.fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            result[str(row[0])] = json.loads(str(row[1]))
        return result

    def search_text(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        tokens = _keyword_tokens(query)
        if not tokens:
            return []
        clauses = " AND ".join(["payload::text ILIKE %s"] * len(tokens))
        params = [f"%{token}%" for token in tokens]
        params.append(int(limit))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT payload::text FROM conversations
                    WHERE {clauses}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def insert_facts(self, facts: list[dict[str, Any]]) -> None:
        if not facts:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                for fact in facts:
                    self._refresh_matching_fact_confirmation(cur, fact)
                    self._supersede_corrected_facts(cur, fact)
                    cur.execute(
                        """
                        INSERT INTO facts (
                            id, subject, predicate, object, qualifiers, confidence,
                            source_conversation_id, source_message_indexes,
                            created_at, updated_at, superseded_by, superseded_at,
                            source_quality, confidence_reason, last_confirmed_at,
                            object_raw, object_normalized, deleted_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (id) DO UPDATE
                        SET subject = EXCLUDED.subject,
                            predicate = EXCLUDED.predicate,
                            object = EXCLUDED.object,
                            qualifiers = EXCLUDED.qualifiers,
                            confidence = EXCLUDED.confidence,
                            source_conversation_id = EXCLUDED.source_conversation_id,
                            source_message_indexes = EXCLUDED.source_message_indexes,
                            updated_at = EXCLUDED.updated_at,
                            superseded_by = EXCLUDED.superseded_by,
                            superseded_at = EXCLUDED.superseded_at,
                            source_quality = EXCLUDED.source_quality,
                            confidence_reason = EXCLUDED.confidence_reason,
                            last_confirmed_at = EXCLUDED.last_confirmed_at,
                            object_raw = EXCLUDED.object_raw,
                            object_normalized = EXCLUDED.object_normalized,
                            deleted_at = EXCLUDED.deleted_at
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
            clauses.append("subject = %s")
            params.append(subject)
        if predicate is not None:
            clauses.append("predicate = %s")
            params.append(predicate)
        if not include_superseded:
            clauses.append("superseded_by IS NULL")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, subject, predicate, object, qualifiers::text, confidence,
                           source_conversation_id, source_message_indexes::text,
                           created_at::text, updated_at::text, superseded_by,
                           superseded_at::text, source_quality, confidence_reason,
                           last_confirmed_at::text, object_raw, object_normalized,
                           deleted_at::text
                    FROM facts
                    WHERE {' AND '.join(clauses)}
                    ORDER BY updated_at DESC, id ASC
                    """,
                    tuple(params),
                )
                rows = cur.fetchall()
        return [self._fact_from_row(row) for row in rows]

    def profile_get(self, subject: str = "user") -> dict[str, Any]:
        return {"subject": subject, "facts": self.search_facts(subject=subject)}

    def supersede_fact(self, fact_id: str, superseded_by: str) -> bool:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE facts
                    SET superseded_by = %s, superseded_at = NOW(), updated_at = NOW()
                    WHERE id = %s AND deleted_at IS NULL
                    """,
                    (superseded_by, fact_id),
                )
                return cur.rowcount > 0

    def get_by_conversation_hash(self, conversation_hash: str | None) -> dict[str, Any] | None:
        if not conversation_hash:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(GET_CONVERSATION_BY_HASH_SQL, (conversation_hash,))
                row = cur.fetchone()
        if row is None:
            return None
        return json.loads(str(row[0]))

    def get_by_upstream_thread(
        self, source: str, upstream_thread_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(GET_CONVERSATION_BY_UPSTREAM_THREAD_SQL, (source, upstream_thread_id))
                row = cur.fetchone()
        if row is None:
            return None
        return json.loads(str(row[0]))

    def mark_chunks_indexed(self, memory_id: str, chunk_ids: list[str]) -> None:
        _ = (memory_id, chunk_ids)

    def mark_chunks_indexing_failed(self, memory_id: str, chunk_ids: list[str]) -> None:
        _ = (memory_id, chunk_ids)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_batch_insert=True,
            supports_transactions=True,
            supports_tags=True,
            supports_metadata_indexing=True,
        )

    def health(self) -> dict[str, Any]:
        return {
            "provider": "postgres",
            "schema_version": self.schema_version,
        }

    def _validate_memory_id(self, memory_id: Any) -> str:
        value = str(memory_id)
        try:
            uuid.UUID(value)
        except (ValueError, TypeError) as exc:
            raise ValueError("memory_id must be a valid UUID") from exc
        return value

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

    def _is_unique_violation(self, exc: Exception) -> bool:
        if getattr(exc, "sqlstate", None) == "23505":
            return True
        if getattr(exc, "pgcode", None) == "23505":
            return True
        return exc.__class__.__name__ == "UniqueViolation"

    def _handle_insert_new_error(
        self, exc: Exception, *, memory_id: str, conversation_hash: str | None
    ) -> tuple[str, bool]:
        if not self._is_unique_violation(exc):
            raise exc
        return self._resolve_insert_new_conflict(
            memory_id=memory_id,
            conversation_hash=conversation_hash,
            cause=exc,
        )

    def _supersede_corrected_facts(self, cur: Any, fact: dict[str, Any]) -> None:
        qualifiers = fact.get("qualifiers", {})
        corrects = qualifiers.get("corrects") if isinstance(qualifiers, dict) else None
        if not corrects:
            return
        cur.execute(
            """
            UPDATE facts
            SET superseded_by = %s, superseded_at = %s, updated_at = %s
            WHERE subject = %s
              AND predicate = %s
              AND superseded_by IS NULL
              AND deleted_at IS NULL
              AND lower(object) LIKE %s
            """,
            (
                str(fact["id"]),
                str(fact["updated_at"]),
                str(fact["updated_at"]),
                str(fact["subject"]),
                str(fact["predicate"]),
                f"%{str(corrects).lower()}%",
            ),
        )

    def _refresh_matching_fact_confirmation(self, cur: Any, fact: dict[str, Any]) -> None:
        if fact.get("source_quality") != "direct_user_statement":
            return
        cur.execute(
            """
            UPDATE facts
            SET last_confirmed_at = %s, updated_at = %s
            WHERE subject = %s
              AND predicate = %s
              AND object = %s
              AND superseded_by IS NULL
              AND deleted_at IS NULL
            """,
            (
                str(fact["last_confirmed_at"]),
                str(fact["updated_at"]),
                str(fact["subject"]),
                str(fact["predicate"]),
                str(fact["object"]),
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

    def _fact_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "subject": str(row[1]),
            "predicate": str(row[2]),
            "object": str(row[3]),
            "qualifiers": json.loads(str(row[4])),
            "confidence": str(row[5]),
            "source_conversation_id": str(row[6]),
            "source_message_indexes": json.loads(str(row[7])),
            "created_at": str(row[8]),
            "updated_at": str(row[9]),
            "superseded_by": row[10],
            "superseded_at": row[11],
            "source_quality": row[12],
            "confidence_reason": row[13],
            "last_confirmed_at": row[14],
            "object_raw": row[15],
            "object_normalized": row[16],
            "deleted_at": row[17],
        }

    def _resolve_insert_new_conflict(
        self,
        *,
        memory_id: str,
        conversation_hash: str | None,
        cause: Exception,
    ) -> tuple[str, bool]:
        existing = self.get_by_conversation_hash(conversation_hash)
        if existing is None:
            existing = self.get(memory_id)
        if existing is None:
            raise cause
        if self._conversation_hash(existing) != conversation_hash:
            raise ValueError("unauthorized_update: conversation id already exists") from cause
        return str(existing["id"]), False


def _keyword_tokens(query: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", query)
        if len(token) >= 2
    ][:8]
