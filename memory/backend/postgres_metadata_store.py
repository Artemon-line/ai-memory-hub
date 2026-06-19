from __future__ import annotations

import json
import re
import uuid
from typing import Any, Callable

from memory.backend.contracts import ProviderCapabilities
from memory.backend.errors import SchemaVersionError
from memory.backend.metadata_store import (
    LOCAL_DEFAULT_PROJECT_ID,
    PROJECT_ROLE_ADMIN,
    PROJECT_ROLE_ORDER,
    _default_project_id,
    _hash_bearer_token,
    _new_token_id,
    _normalize_token_scopes,
    _owner_id_from_payload,
    _parse_token_scopes,
    _stamp_project_id,
    _token_id_from_hash,
    _token_prefix,
    _validate_owner_id,
    _validate_project_id,
    _validate_project_role,
    _validate_token_lookup,
)

CREATE_CONVERSATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    title TEXT NULL,
    conversation_hash TEXT NULL,
    project_id TEXT NULL,
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

CREATE_GENERATED_SUMMARIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS generated_summaries (
    id TEXT PRIMARY KEY,
    summary_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    owner_id TEXT NULL,
    project_id TEXT NULL,
    text TEXT NOT NULL,
    basis TEXT NOT NULL,
    provenance_status TEXT NOT NULL,
    filters JSONB NOT NULL,
    provenance JSONB NOT NULL,
    payload JSONB NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

CREATE_GENERATED_SUMMARIES_TARGET_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_generated_summaries_target
ON generated_summaries(summary_type, target_id, project_id)
"""

CREATE_SCHEMA_VERSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    id SMALLINT PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""
CREATE_USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    display_name TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    disabled_at TIMESTAMPTZ NULL
)
"""
CREATE_AUTH_TOKENS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash TEXT PRIMARY KEY,
    token_id TEXT NULL,
    owner_id TEXT NOT NULL REFERENCES users(id),
    display_name TEXT NULL,
    token_prefix TEXT NULL,
    scopes JSONB NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ NULL,
    expires_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL
)
"""
CREATE_AUTH_TOKENS_OWNER_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_auth_tokens_owner
ON auth_tokens(owner_id)
WHERE revoked_at IS NULL
"""
CREATE_AUTH_TOKENS_TOKEN_ID_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_tokens_token_id
ON auth_tokens(token_id)
WHERE token_id IS NOT NULL
"""
CREATE_PROJECTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    owner_id TEXT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    description TEXT NULL,
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ NULL
)
"""
CREATE_PROJECT_MEMBERSHIPS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS project_memberships (
    project_id TEXT NOT NULL REFERENCES projects(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL CHECK(role IN ('admin', 'writer', 'reader')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(project_id, user_id)
)
"""
CREATE_DEFAULT_PROJECT_OWNER_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_default_owner
ON projects(owner_id)
WHERE is_default = TRUE AND owner_id IS NOT NULL
"""
CREATE_DEFAULT_PROJECT_LOCAL_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_default_local
ON projects(is_default)
WHERE is_default = TRUE AND owner_id IS NULL
"""
CREATE_CONVERSATION_PROJECT_HASH_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_project_hash
ON conversations(project_id, conversation_hash)
WHERE conversation_hash IS NOT NULL
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
    owner_id TEXT NULL,
    project_id TEXT NULL,
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
INSERT INTO conversations (id, source, timestamp, title, conversation_hash, project_id, upstream_thread_id, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (id) DO UPDATE
SET source = EXCLUDED.source,
    timestamp = EXCLUDED.timestamp,
    title = EXCLUDED.title,
    conversation_hash = EXCLUDED.conversation_hash,
    project_id = EXCLUDED.project_id,
    upstream_thread_id = EXCLUDED.upstream_thread_id,
    payload = EXCLUDED.payload
"""

INSERT_NEW_CONVERSATION_SQL = """
INSERT INTO conversations (id, source, timestamp, title, conversation_hash, project_id, upstream_thread_id, payload)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
"""

GET_CONVERSATION_SQL = "SELECT payload::text FROM conversations WHERE id = %s"
GET_MANY_CONVERSATIONS_SQL = "SELECT id, payload::text FROM conversations WHERE id = ANY(%s)"
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
                cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS conversation_hash TEXT NULL")
                cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS project_id TEXT NULL")
                cur.execute("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS upstream_thread_id TEXT NULL")
                cur.execute("ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_conversation_hash_key")
                cur.execute("DROP INDEX IF EXISTS idx_conversations_conversation_hash")
                cur.execute(CREATE_CONVERSATION_PROJECT_HASH_INDEX_SQL)
                cur.execute(CREATE_UPSTREAM_THREAD_INDEX_SQL)
                cur.execute(CREATE_GENERATED_SUMMARIES_TABLE_SQL)
                cur.execute(CREATE_GENERATED_SUMMARIES_TARGET_INDEX_SQL)
                cur.execute(CREATE_SCHEMA_VERSION_TABLE_SQL)
                cur.execute(CREATE_USERS_TABLE_SQL)
                cur.execute(CREATE_AUTH_TOKENS_TABLE_SQL)
                cur.execute("ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS token_id TEXT NULL")
                cur.execute("ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS display_name TEXT NULL")
                cur.execute("ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS token_prefix TEXT NULL")
                cur.execute("ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS scopes JSONB NULL")
                cur.execute("ALTER TABLE auth_tokens ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ NULL")
                cur.execute(CREATE_AUTH_TOKENS_OWNER_INDEX_SQL)
                cur.execute(CREATE_AUTH_TOKENS_TOKEN_ID_INDEX_SQL)
                cur.execute(CREATE_PROJECTS_TABLE_SQL)
                cur.execute(CREATE_PROJECT_MEMBERSHIPS_TABLE_SQL)
                cur.execute(CREATE_DEFAULT_PROJECT_OWNER_INDEX_SQL)
                cur.execute(CREATE_DEFAULT_PROJECT_LOCAL_INDEX_SQL)
                cur.execute(CREATE_FACTS_TABLE_SQL)
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS source_quality TEXT NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS confidence_reason TEXT NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS last_confirmed_at TIMESTAMPTZ NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS object_raw TEXT NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS object_normalized TEXT NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS owner_id TEXT NULL")
                cur.execute("ALTER TABLE facts ADD COLUMN IF NOT EXISTS project_id TEXT NULL")
                cur.execute(CREATE_FACTS_INDEX_SQL)
                cur.execute(SEED_SCHEMA_VERSION_SQL, (1, self._schema_version_seed))
                self._migrate_auth_token_ids(cur)
                self._migrate_default_projects(cur)

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
                        self._project_id(conversation_json),
                        self._upstream_thread_id(conversation_json),
                        payload,
                    ),
                )
        return memory_id

    def create_user(self, *, user_id: str, display_name: str | None = None) -> dict[str, Any]:
        owner = _validate_owner_id(user_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, display_name)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                        disabled_at = NULL
                    """,
                    (owner, display_name),
                )
                self._ensure_default_project(cur, owner)
                cur.execute(
                    """
                    SELECT id, display_name, created_at::text, disabled_at::text
                    FROM users
                    WHERE id = %s
                    """,
                    (owner,),
                )
                row = cur.fetchone()
        if row is None:
            raise RuntimeError("user could not be created")
        return self._user_from_row(row)

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, display_name, created_at::text, disabled_at::text
                    FROM users
                    ORDER BY id ASC
                    """
                )
                rows = cur.fetchall()
        return [self._user_from_row(row) for row in rows]

    def create_auth_token(
        self,
        *,
        owner_id: str,
        token: str,
        display_name: str | None = None,
        token_display_name: str | None = None,
        expires_at: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict[str, Any]:
        owner = _validate_owner_id(owner_id)
        token_hash = _hash_bearer_token(token)
        token_id = _new_token_id()
        token_name = token_display_name
        token_prefix = _token_prefix(token)
        scopes_json = json.dumps(_normalize_token_scopes(scopes), separators=(",", ":"))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, display_name)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        display_name = COALESCE(EXCLUDED.display_name, users.display_name)
                    """,
                    (owner, display_name),
                )
                self._ensure_default_project(cur, owner)
                cur.execute(
                    """
                    INSERT INTO auth_tokens
                        (token_hash, token_id, owner_id, display_name, token_prefix, scopes, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (token_hash) DO UPDATE SET
                        token_id = COALESCE(auth_tokens.token_id, EXCLUDED.token_id),
                        owner_id = EXCLUDED.owner_id,
                        display_name = EXCLUDED.display_name,
                        token_prefix = EXCLUDED.token_prefix,
                        scopes = EXCLUDED.scopes,
                        expires_at = EXCLUDED.expires_at,
                        revoked_at = NULL
                    """,
                    (token_hash, token_id, owner, token_name, token_prefix, scopes_json, expires_at),
                )
                cur.execute(
                    """
                    SELECT token_id, owner_id, display_name, token_prefix,
                           created_at::text, expires_at::text, revoked_at::text,
                           scopes::text, last_used_at::text
                    FROM auth_tokens
                    WHERE token_hash = %s
                    """,
                    (token_hash,),
                )
                row = cur.fetchone()
        if row is None:
            raise RuntimeError("auth token could not be created")
        return self._token_from_row(row)

    def owner_for_token(self, token: str) -> str | None:
        context = self.auth_context_for_token(token)
        return context["owner_id"] if context is not None else None

    def auth_context_for_token(self, token: str) -> dict[str, Any] | None:
        token_hash = _hash_bearer_token(token)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT auth_tokens.owner_id, auth_tokens.scopes::text, auth_tokens.token_id
                    FROM auth_tokens
                    JOIN users ON users.id = auth_tokens.owner_id
                    WHERE auth_tokens.token_hash = %s
                      AND auth_tokens.revoked_at IS NULL
                      AND users.disabled_at IS NULL
                      AND (auth_tokens.expires_at IS NULL OR auth_tokens.expires_at > NOW())
                    """,
                    (token_hash,),
                )
                row = cur.fetchone()
                if row is not None:
                    cur.execute(
                        "UPDATE auth_tokens SET last_used_at = NOW() WHERE token_hash = %s",
                        (token_hash,),
                    )
        if row is None:
            return None
        return {
            "owner_id": str(row[0]),
            "scopes": _parse_token_scopes(row[1]),
            "token_id": str(row[2]) if row[2] is not None else None,
        }

    def list_auth_tokens(self, *, owner_id: str) -> list[dict[str, Any]]:
        owner = _validate_owner_id(owner_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT token_id, owner_id, display_name, token_prefix,
                           created_at::text, expires_at::text, revoked_at::text,
                           scopes::text, last_used_at::text
                    FROM auth_tokens
                    WHERE owner_id = %s
                    ORDER BY created_at DESC, token_id ASC
                    """,
                    (owner,),
                )
                rows = cur.fetchall()
        return [self._token_from_row(row) for row in rows]

    def revoke_auth_token(self, token_id_or_prefix: str) -> dict[str, Any] | None:
        handle = _validate_token_lookup(token_id_or_prefix)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT token_hash, token_id, owner_id, display_name, token_prefix,
                           created_at::text, expires_at::text, revoked_at::text,
                           scopes::text, last_used_at::text
                    FROM auth_tokens
                    WHERE token_id = %s
                       OR token_id LIKE %s
                       OR token_prefix = %s
                    ORDER BY token_id ASC
                    """,
                    (handle, f"{handle}%", handle),
                )
                rows = cur.fetchall()
                if not rows:
                    return None
                token_ids = {str(row[1]) for row in rows}
                if len(token_ids) > 1:
                    raise ValueError("token identifier is ambiguous")
                token_hash = str(rows[0][0])
                cur.execute(
                    """
                    UPDATE auth_tokens
                    SET revoked_at = NOW()
                    WHERE token_hash = %s
                    """,
                    (token_hash,),
                )
                cur.execute(
                    """
                    SELECT token_id, owner_id, display_name, token_prefix,
                           created_at::text, expires_at::text, revoked_at::text,
                           scopes::text, last_used_at::text
                    FROM auth_tokens
                    WHERE token_hash = %s
                    """,
                    (token_hash,),
                )
                row = cur.fetchone()
        return self._token_from_row(row) if row is not None else None

    def ensure_default_project(self, owner_id: str | None) -> dict[str, Any]:
        owner = _validate_owner_id(owner_id) if owner_id is not None else None
        with self._connect() as conn:
            with conn.cursor() as cur:
                project_id = self._ensure_default_project(cur, owner)
                project = self._get_project_row(cur, project_id)
        if project is None:
            raise RuntimeError("default project could not be resolved")
        return project

    def create_project(
        self,
        *,
        project_id: str,
        owner_id: str,
        name: str | None = None,
        description: str | None = None,
        is_default: bool = False,
    ) -> dict[str, Any]:
        project = _validate_project_id(project_id)
        owner = _validate_owner_id(owner_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, display_name)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (owner, owner),
                )
                cur.execute(
                    """
                    INSERT INTO projects (id, owner_id, name, description, is_default)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                    SET owner_id = EXCLUDED.owner_id,
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        is_default = EXCLUDED.is_default,
                        updated_at = NOW()
                    """,
                    (project, owner, name or project, description, bool(is_default)),
                )
                self._upsert_project_membership(cur, project, owner, PROJECT_ROLE_ADMIN)
                row = self._get_project_row(cur, project)
        if row is None:
            raise RuntimeError("project could not be created")
        return row

    def add_project_member(self, *, project_id: str, user_id: str, role: str) -> None:
        project = _validate_project_id(project_id)
        user = _validate_owner_id(user_id)
        normalized_role = _validate_project_role(role)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (id, display_name)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (user, user),
                )
                self._upsert_project_membership(cur, project, user, normalized_role)

    def list_project_members(self, *, project_id: str) -> list[dict[str, Any]]:
        project = _validate_project_id(project_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT project_memberships.project_id, project_memberships.user_id,
                           project_memberships.role, project_memberships.created_at::text,
                           project_memberships.updated_at::text, users.display_name
                    FROM project_memberships
                    JOIN projects ON projects.id = project_memberships.project_id
                    JOIN users ON users.id = project_memberships.user_id
                    WHERE project_memberships.project_id = %s
                      AND projects.archived_at IS NULL
                    ORDER BY project_memberships.role ASC, project_memberships.user_id ASC
                    """,
                    (project,),
                )
                rows = cur.fetchall()
        return [self._project_member_from_row(row) for row in rows]

    def project_has_role(self, *, project_id: str, user_id: str | None, role: str) -> bool:
        project = _validate_project_id(project_id)
        required = _validate_project_role(role)
        if user_id is None:
            return project == LOCAL_DEFAULT_PROJECT_ID
        user = _validate_owner_id(user_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT project_memberships.role
                    FROM project_memberships
                    JOIN projects ON projects.id = project_memberships.project_id
                    WHERE project_memberships.project_id = %s
                      AND project_memberships.user_id = %s
                      AND projects.archived_at IS NULL
                    """,
                    (project, user),
                )
                row = cur.fetchone()
        if row is None:
            return False
        return PROJECT_ROLE_ORDER[str(row[0])] >= PROJECT_ROLE_ORDER[required]

    def list_projects(self, *, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                if user_id is None:
                    cur.execute(
                        """
                        SELECT id, owner_id, name, description, is_default,
                               created_at::text, updated_at::text, archived_at::text, NULL::text AS role
                        FROM projects
                        WHERE id = %s AND archived_at IS NULL
                        ORDER BY is_default DESC, name ASC
                        """,
                        (LOCAL_DEFAULT_PROJECT_ID,),
                    )
                else:
                    user = _validate_owner_id(user_id)
                    cur.execute(
                        """
                        SELECT projects.id, projects.owner_id, projects.name,
                               projects.description, projects.is_default,
                               projects.created_at::text, projects.updated_at::text,
                               projects.archived_at::text, project_memberships.role
                        FROM projects
                        JOIN project_memberships ON project_memberships.project_id = projects.id
                        WHERE project_memberships.user_id = %s
                          AND projects.archived_at IS NULL
                        ORDER BY projects.is_default DESC, projects.name ASC
                        """,
                        (user,),
                    )
                rows = cur.fetchall()
        return [self._project_from_row(row) for row in rows]

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
                            self._project_id(conversation_json),
                            self._upstream_thread_id(conversation_json),
                            payload,
                        ),
                    )
        except Exception as exc:
            return self._handle_insert_new_error(
                exc,
                memory_id=memory_id,
                conversation_hash=conversation_hash,
                project_id=self._project_id(conversation_json),
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

    def search_text(
        self, query: str, limit: int = 50, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        tokens = _keyword_tokens(query)
        if not tokens:
            return []
        clauses = ["payload::text ILIKE %s"] * len(tokens)
        params = [f"%{token}%" for token in tokens]
        if project_id is not None:
            clauses.append("project_id = %s")
            params.append(project_id)
        params.append(int(limit))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT payload::text FROM conversations
                    WHERE {' AND '.join(clauses)}
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
                            source_conversation_id, owner_id, project_id, source_message_indexes,
                            created_at, updated_at, superseded_by, superseded_at,
                            source_quality, confidence_reason, last_confirmed_at,
                            object_raw, object_normalized, deleted_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (id) DO UPDATE
                        SET subject = EXCLUDED.subject,
                            predicate = EXCLUDED.predicate,
                            object = EXCLUDED.object,
                            qualifiers = EXCLUDED.qualifiers,
                            confidence = EXCLUDED.confidence,
                            source_conversation_id = EXCLUDED.source_conversation_id,
                            owner_id = EXCLUDED.owner_id,
                            project_id = EXCLUDED.project_id,
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
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["deleted_at IS NULL"]
        params: list[Any] = []
        if subject is not None:
            clauses.append("subject = %s")
            params.append(subject)
        if predicate is not None:
            clauses.append("predicate = %s")
            params.append(predicate)
        if project_id is not None:
            clauses.append("project_id = %s")
            params.append(project_id)
        if not include_superseded:
            clauses.append("superseded_by IS NULL")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, subject, predicate, object, qualifiers::text, confidence,
                           source_conversation_id, owner_id, project_id, source_message_indexes::text,
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

    def profile_get(
        self, subject: str = "user", project_id: str | None = None
    ) -> dict[str, Any]:
        return {"subject": subject, "facts": self.search_facts(subject=subject, project_id=project_id)}

    def upsert_generated_summary(self, summary: dict[str, Any]) -> str:
        summary_id = str(summary["id"])
        payload = json.dumps(summary, separators=(",", ":"), ensure_ascii=False)
        filters = json.dumps(summary.get("filters", {}), separators=(",", ":"), ensure_ascii=False)
        provenance = json.dumps(summary.get("provenance", []), separators=(",", ":"), ensure_ascii=False)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO generated_summaries (
                        id, summary_type, target_id, owner_id, project_id, text, basis,
                        provenance_status, filters, provenance, payload, generated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb,
                        %s::jsonb, %s
                    )
                    ON CONFLICT(id) DO UPDATE SET
                        text = EXCLUDED.text,
                        basis = EXCLUDED.basis,
                        provenance_status = EXCLUDED.provenance_status,
                        filters = EXCLUDED.filters,
                        provenance = EXCLUDED.provenance,
                        payload = EXCLUDED.payload,
                        generated_at = EXCLUDED.generated_at,
                        updated_at = NOW()
                    """,
                    (
                        summary_id,
                        str(summary["type"]),
                        str(summary["target_id"]),
                        summary.get("owner_id"),
                        summary.get("project_id"),
                        str(summary.get("text", "")),
                        str(summary.get("basis", "")),
                        str(summary.get("provenance_status", "")),
                        filters,
                        provenance,
                        payload,
                        str(summary.get("generated_at", "")),
                    ),
                )
        return summary_id

    def get_generated_summary(self, summary_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload::text FROM generated_summaries WHERE id = %s",
                    (str(summary_id),),
                )
                row = cur.fetchone()
        if row is None:
            return None
        return json.loads(str(row[0]))

    def supersede_fact(
        self, fact_id: str, superseded_by: str, project_id: str | None = None
    ) -> bool:
        clauses = ["id = %s", "deleted_at IS NULL"]
        params: list[Any] = [superseded_by, fact_id]
        if project_id is not None:
            clauses.append("project_id = %s")
            params.append(project_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE facts
                    SET superseded_by = %s, superseded_at = NOW(), updated_at = NOW()
                    WHERE {' AND '.join(clauses)}
                    """,
                    tuple(params),
                )
                return cur.rowcount > 0

    def get_by_conversation_hash(
        self, conversation_hash: str | None, project_id: str | None = None
    ) -> dict[str, Any] | None:
        if not conversation_hash:
            return None
        with self._connect() as conn:
            with conn.cursor() as cur:
                if project_id is None:
                    cur.execute(
                        "SELECT payload::text FROM conversations WHERE conversation_hash = %s",
                        (conversation_hash,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT payload::text FROM conversations
                        WHERE conversation_hash = %s AND project_id = %s
                        """,
                        (conversation_hash, project_id),
                    )
                row = cur.fetchone()
        if row is None:
            return None
        return json.loads(str(row[0]))

    def get_by_upstream_thread(
        self, source: str, upstream_thread_id: str, project_id: str | None = None
    ) -> dict[str, Any] | None:
        clauses = ["source = %s", "upstream_thread_id = %s"]
        params: list[Any] = [source, upstream_thread_id]
        if project_id is not None:
            clauses.append("project_id = %s")
            params.append(project_id)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT payload::text FROM conversations
                    WHERE {' AND '.join(clauses)}
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    tuple(params),
                )
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

    def _project_id(self, conversation_json: dict[str, Any]) -> str | None:
        metadata = conversation_json.get("metadata", {})
        if isinstance(metadata, dict):
            value = metadata.get("project_id")
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
        self,
        exc: Exception,
        *,
        memory_id: str,
        conversation_hash: str | None,
        project_id: str | None,
    ) -> tuple[str, bool]:
        if not self._is_unique_violation(exc):
            raise exc
        return self._resolve_insert_new_conflict(
            memory_id=memory_id,
            conversation_hash=conversation_hash,
            project_id=project_id,
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
              AND owner_id IS NOT DISTINCT FROM %s
              AND project_id IS NOT DISTINCT FROM %s
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
                fact.get("owner_id"),
                fact.get("project_id"),
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
              AND owner_id IS NOT DISTINCT FROM %s
              AND project_id IS NOT DISTINCT FROM %s
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
                fact.get("project_id"),
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
            fact.get("project_id"),
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
        row = tuple(row)
        if len(row) == 18:
            row = (*row[:8], None, *row[8:], None)
        elif len(row) == 19:
            row = (*row, None)
        elif len(row) != 20:
            raise ValueError("unsupported fact row shape")
        return {
            "id": str(row[0]),
            "subject": str(row[1]),
            "predicate": str(row[2]),
            "object": str(row[3]),
            "qualifiers": json.loads(str(row[4])),
            "confidence": str(row[5]),
            "source_conversation_id": str(row[6]),
            "owner_id": row[7],
            "project_id": row[8],
            "source_message_indexes": json.loads(str(row[9])),
            "created_at": str(row[10]),
            "updated_at": str(row[11]),
            "superseded_by": row[12],
            "superseded_at": row[13],
            "source_quality": row[14],
            "confidence_reason": row[15],
            "last_confirmed_at": row[16],
            "object_raw": row[17],
            "object_normalized": row[18],
            "deleted_at": row[19],
        }

    def _resolve_insert_new_conflict(
        self,
        *,
        memory_id: str,
        conversation_hash: str | None,
        project_id: str | None,
        cause: Exception,
    ) -> tuple[str, bool]:
        existing = self._get_by_conversation_hash_for_conflict(
            conversation_hash, project_id=project_id
        )
        if existing is None:
            existing = self.get(memory_id)
        if existing is None:
            raise cause
        if self._conversation_hash(existing) != conversation_hash:
            raise ValueError("unauthorized_update: conversation id already exists") from cause
        if self._project_id(existing) != project_id:
            raise ValueError("unauthorized_update: conversation id already exists") from cause
        return str(existing["id"]), False

    def _get_by_conversation_hash_for_conflict(
        self, conversation_hash: str | None, *, project_id: str | None
    ) -> dict[str, Any] | None:
        try:
            return self.get_by_conversation_hash(conversation_hash, project_id=project_id)
        except TypeError:
            return self.get_by_conversation_hash(conversation_hash)

    def _ensure_default_project(self, cur: Any, owner_id: str | None) -> str:
        if owner_id is None:
            cur.execute(
                """
                INSERT INTO projects (id, owner_id, name, is_default)
                VALUES (%s, NULL, %s, TRUE)
                ON CONFLICT (id) DO UPDATE SET is_default = TRUE, updated_at = NOW()
                """,
                (LOCAL_DEFAULT_PROJECT_ID, "Local Default"),
            )
            return LOCAL_DEFAULT_PROJECT_ID

        cur.execute(
            """
            INSERT INTO users (id, display_name)
            VALUES (%s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (owner_id, owner_id),
        )
        cur.execute(
            """
            SELECT id FROM projects
            WHERE owner_id = %s AND is_default = TRUE AND archived_at IS NULL
            LIMIT 1
            """,
            (owner_id,),
        )
        row = cur.fetchone()
        if row is not None:
            project_id = str(row[0])
        else:
            project_id = _default_project_id(owner_id)
            cur.execute(
                """
                INSERT INTO projects (id, owner_id, name, is_default)
                VALUES (%s, %s, %s, TRUE)
                ON CONFLICT (id) DO UPDATE SET is_default = TRUE, updated_at = NOW()
                """,
                (project_id, owner_id, "Private Default"),
            )
        self._upsert_project_membership(cur, project_id, owner_id, PROJECT_ROLE_ADMIN)
        return project_id

    def _upsert_project_membership(
        self, cur: Any, project_id: str, user_id: str, role: str
    ) -> None:
        cur.execute(
            """
            INSERT INTO project_memberships (project_id, user_id, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (project_id, user_id) DO UPDATE
            SET role = EXCLUDED.role,
                updated_at = NOW()
            """,
            (project_id, user_id, role),
        )

    def _get_project_row(self, cur: Any, project_id: str) -> dict[str, Any] | None:
        cur.execute(
            """
            SELECT id, owner_id, name, description, is_default,
                   created_at::text, updated_at::text, archived_at::text, NULL::text AS role
            FROM projects
            WHERE id = %s AND archived_at IS NULL
            """,
            (project_id,),
        )
        row = cur.fetchone()
        return self._project_from_row(row) if row is not None else None

    def _project_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "owner_id": row[1],
            "name": str(row[2]),
            "description": row[3],
            "is_default": bool(row[4]),
            "created_at": str(row[5]),
            "updated_at": str(row[6]),
            "archived_at": row[7],
            "role": row[8],
        }

    def _user_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "display_name": row[1],
            "created_at": str(row[2]),
            "disabled_at": row[3],
        }

    def _token_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "token_id": str(row[0]),
            "owner_id": str(row[1]),
            "display_name": row[2],
            "token_prefix": row[3],
            "created_at": str(row[4]),
            "expires_at": row[5],
            "revoked_at": row[6],
            "scopes": _parse_token_scopes(row[7]),
            "last_used_at": row[8],
        }

    def _project_member_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "project_id": str(row[0]),
            "user_id": str(row[1]),
            "role": str(row[2]),
            "created_at": str(row[3]),
            "updated_at": str(row[4]),
            "display_name": row[5],
        }

    def _migrate_auth_token_ids(self, cur: Any) -> None:
        cur.execute(
            """
            SELECT token_hash FROM auth_tokens
            WHERE token_id IS NULL
            """
        )
        for row in cur.fetchall():
            token_hash = str(row[0])
            cur.execute(
                """
                UPDATE auth_tokens
                SET token_id = %s
                WHERE token_hash = %s
                """,
                (_token_id_from_hash(token_hash), token_hash),
            )

    def _migrate_default_projects(self, cur: Any) -> None:
        local_project = self._ensure_default_project(cur, None)
        cur.execute("SELECT id FROM users")
        for row in cur.fetchall():
            self._ensure_default_project(cur, str(row[0]))

        cur.execute("SELECT id, payload::text FROM conversations WHERE project_id IS NULL")
        for row in cur.fetchall():
            payload = json.loads(str(row[1]))
            owner_id = _owner_id_from_payload(payload)
            project_id = self._ensure_default_project(cur, owner_id) if owner_id else local_project
            _stamp_project_id(payload, project_id)
            cur.execute(
                "UPDATE conversations SET project_id = %s, payload = %s::jsonb WHERE id = %s",
                (project_id, json.dumps(payload, separators=(",", ":"), ensure_ascii=False), str(row[0])),
            )

        cur.execute("SELECT id, owner_id FROM facts WHERE project_id IS NULL")
        for row in cur.fetchall():
            owner_id = str(row[1]) if row[1] is not None else None
            project_id = self._ensure_default_project(cur, owner_id) if owner_id else local_project
            cur.execute(
                "UPDATE facts SET project_id = %s WHERE id = %s",
                (project_id, str(row[0])),
            )


def _keyword_tokens(query: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", query)
        if len(token) >= 2
    ][:8]
