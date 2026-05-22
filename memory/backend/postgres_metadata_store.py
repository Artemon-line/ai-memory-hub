from __future__ import annotations

import json
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
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_SCHEMA_VERSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

SEED_SCHEMA_VERSION_SQL = """
INSERT INTO schema_version (id, version)
VALUES (%s, %s)
ON CONFLICT (id) DO NOTHING
"""

INSERT_CONVERSATION_SQL = """
INSERT INTO conversations (id, source, timestamp, title, payload)
VALUES (%s, %s, %s, %s, %s::jsonb)
ON CONFLICT (id) DO UPDATE
SET source = EXCLUDED.source,
    timestamp = EXCLUDED.timestamp,
    title = EXCLUDED.title,
    payload = EXCLUDED.payload
"""

GET_CONVERSATION_SQL = "SELECT payload::text FROM conversations WHERE id = %s"
GET_MANY_CONVERSATIONS_SQL = "SELECT id, payload::text FROM conversations WHERE id = ANY(%s)"
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
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg package is required for providers.metadata_db=postgres") from exc
        return psycopg.connect(self._dsn)

    def _init_db(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(CREATE_CONVERSATIONS_TABLE_SQL)
                cur.execute(CREATE_SCHEMA_VERSION_TABLE_SQL)
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
                        payload,
                    ),
                )
        return memory_id

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

