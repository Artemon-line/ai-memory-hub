from __future__ import annotations

import os
import sqlite3
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from memory.backend.contracts import ProviderCapabilities
from memory.backend.errors import (
    NotSupportedError,
    SchemaVersionError,
    VectorDimensionError,
)
from memory.backend.log_safety import redact_secrets
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.postgres_metadata_store import PostgresMetadataStore
from memory.backend.vector_store import InMemoryVectorStore, LanceDBVectorStore, PGVectorStore
from memory.ingestion import mvp_ingestion

VectorStoreFactory = Callable[[Path], Any]


def test_sqlite_capabilities_and_health(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    capabilities = store.capabilities()
    health = store.health()

    assert isinstance(capabilities, ProviderCapabilities)
    assert capabilities.supports_transactions is True
    assert health["schema_version"] == 1


def test_sqlite_metadata_store_tracks_index_chunk_manifest(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    conversation = {
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [
            {"role": "user", "text": "alpha beta gamma", "hash": "sha256:" + "a" * 64}
        ],
        "metadata": {
            "imported_at": "2026-01-01T00:00:00Z",
            "index_chunks": [
                {
                    "chunk_id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210:0:sha256:" + "a" * 64,
                    "chunk_index": 0,
                    "message_hash": "sha256:" + "a" * 64,
                    "role": "user",
                    "text": "alpha beta",
                    "index_state": "pending_index",
                },
                {
                    "chunk_id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210:1:sha256:" + "a" * 64,
                    "chunk_index": 1,
                    "message_hash": "sha256:" + "a" * 64,
                    "role": "user",
                    "text": "beta gamma",
                    "index_state": "pending_index",
                },
            ],
        },
    }

    memory_id = store.insert(conversation)
    assert store.is_fully_indexed(memory_id) is False

    store.mark_chunks_indexed(
        memory_id,
        [chunk["chunk_id"] for chunk in conversation["metadata"]["index_chunks"]],
    )

    assert store.is_fully_indexed(memory_id) is True


def test_inmemory_vector_dimension_validation_insert_and_search() -> None:
    store = InMemoryVectorStore(dimension=3)

    with pytest.raises(VectorDimensionError):
        store.insert(
            "id-1",
            [{"chunk_index": 0, "role": "user", "text": "x", "vector": [1.0, 2.0]}],
        )

    with pytest.raises(VectorDimensionError):
        store.search([1.0, 2.0], top_k=5)


def _assert_vector_store_contract(store: Any) -> None:
    first_id = str(uuid4())
    second_id = str(uuid4())
    replacement_id = str(uuid4())

    store.insert(
        first_id,
        [
            {
                "chunk_id": f"{first_id}:0",
                "chunk_index": 0,
                "message_hash": "sha256:" + ("a" * 64),
                "role": "user",
                "text": "alpha memory",
                "vector": [1.0, 0.0, 0.0],
            }
        ],
    )
    store.insert(
        second_id,
        [
            {
                "chunk_id": f"{second_id}:0",
                "chunk_index": 0,
                "message_hash": "sha256:" + ("b" * 64),
                "role": "assistant",
                "text": "beta memory",
                "vector": [0.0, 1.0, 0.0],
            }
        ],
    )

    matches = store.search([1.0, 0.0, 0.0], top_k=2)
    assert matches[0]["memory_id"] == first_id
    assert matches[0]["chunk_index"] == 0
    assert matches[0]["role"] == "user"
    assert matches[0]["text"] == "alpha memory"
    assert len(matches) == 2

    store.insert(
        replacement_id,
        [
            {
                "chunk_id": f"{replacement_id}:0",
                "chunk_index": 0,
                "message_hash": "sha256:" + ("c" * 64),
                "role": "user",
                "text": "old replacement",
                "vector": [0.0, 0.0, 1.0],
            }
        ],
    )
    store.insert(
        replacement_id,
        [
            {
                "chunk_id": f"{replacement_id}:1",
                "chunk_index": 1,
                "message_hash": "sha256:" + ("d" * 64),
                "role": "assistant",
                "text": "new replacement",
                "vector": [1.0, 0.0, 0.0],
            }
        ],
        replace=True,
    )
    replacement_matches = [
        row
        for row in store.search([1.0, 0.0, 0.0], top_k=10)
        if row["memory_id"] == replacement_id
    ]
    assert len(replacement_matches) == 1
    assert replacement_matches[0]["chunk_index"] == 1
    assert replacement_matches[0]["text"] == "new replacement"

    stats = store.get_stats()
    assert stats["expected_dimensionality"] == 3
    assert stats["provider"] in {"memory", "lancedb", "pgvector"}
    assert stats["rows"] is None or stats["rows"] >= 3

    store.delete([first_id])
    deleted_matches = [
        row for row in store.search([1.0, 0.0, 0.0], top_k=10) if row["memory_id"] == first_id
    ]
    assert deleted_matches == []


@pytest.mark.parametrize(
    ("provider", "factory"),
    [
        ("memory", lambda tmp_path: InMemoryVectorStore(dimension=3)),
        ("lancedb", lambda tmp_path: LanceDBVectorStore(tmp_path / "lancedb", dimension=3)),
    ],
)
def test_vector_store_contract_for_local_backends(
    provider: str, factory: VectorStoreFactory, tmp_path: Path
) -> None:
    _ = provider
    _assert_vector_store_contract(factory(tmp_path))


def test_build_runtime_fails_fast_on_schema_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(SQLiteMetadataStore, "schema_version", 99)
    config = {
        "providers": {"embeddings": "local", "vector_db": "in_memory"},
        "paths": {"data_dir": str(tmp_path)},
        "storage": {"metadata_schema_versions": [1]},
    }

    with pytest.raises(SchemaVersionError):
        mvp_ingestion.build_runtime(config)


def test_build_runtime_fails_on_startup_dimensionality_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class MismatchVectorStore:
        expected_dimensionality = 99

        def __init__(self, *_args, **_kwargs):
            pass

        def capabilities(self):
            return ProviderCapabilities()

        def health(self):
            return {
                "provider": "memory",
                "expected_dimensionality": self.expected_dimensionality,
            }

    monkeypatch.setattr(mvp_ingestion, "InMemoryVectorStore", MismatchVectorStore)
    with pytest.raises(
        VectorDimensionError, match="dimensionality mismatch at startup"
    ):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "in_memory"},
                "paths": {"data_dir": str(tmp_path)},
            }
        )


def test_build_runtime_vector_fallback_uses_same_dimension(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenLanceDB:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(mvp_ingestion, "LanceDBVectorStore", BrokenLanceDB)
    runtime = mvp_ingestion.build_runtime(
        {
            "providers": {"embeddings": "local", "vector_db": "lancedb"},
            "paths": {"data_dir": str(tmp_path)},
            "storage": {"vector": {"allow_fallback": True}},
        }
    )

    assert (
        runtime.vector_store.expected_dimensionality
        == runtime.embedding_provider.dimension
    )


def test_log_redaction_for_dsn_and_keyvalue_secrets() -> None:
    message = (
        "connect failed postgres://user:supersecret@db.local/app "
        "password=hunter2 token=abc123 api_key=xyz"
    )
    redacted = redact_secrets(message)
    assert "supersecret" not in redacted
    assert "hunter2" not in redacted
    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert "***" in redacted


def test_fallback_logging_redacts_exception_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class BrokenLanceDB:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError(
                "connect postgres://u:pw123@db/app password=letmein token=tok123"
            )

    monkeypatch.setattr(mvp_ingestion, "LanceDBVectorStore", BrokenLanceDB)
    with caplog.at_level("WARNING"):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "lancedb"},
                "paths": {"data_dir": str(tmp_path)},
                "storage": {"vector": {"allow_fallback": True}},
            }
        )
    log_text = "\n".join(caplog.messages)
    assert "pw123" not in log_text
    assert "letmein" not in log_text
    assert "tok123" not in log_text
    assert "***" in log_text


def test_build_runtime_vector_fallback_disabled_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenLanceDB:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(mvp_ingestion, "LanceDBVectorStore", BrokenLanceDB)

    with pytest.raises(RuntimeError):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "lancedb"},
                "paths": {"data_dir": str(tmp_path)},
                "storage": {"vector": {"allow_fallback": False}},
            }
        )


def test_build_runtime_pgvector_fallback_uses_memory_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenPGVector:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("pgvector unavailable")

    monkeypatch.setattr(mvp_ingestion, "PGVectorStore", BrokenPGVector)
    runtime = mvp_ingestion.build_runtime(
        {
            "providers": {
                "embeddings": "local",
                "vector_db": "pgvector",
                "metadata_dsn": "postgres://example",
            },
            "paths": {"data_dir": str(tmp_path)},
            "storage": {"vector": {"allow_fallback": True}},
        }
    )

    assert runtime.health_state["mode"] == "degraded"
    assert runtime.health_state["vector_provider"] == "memory"
    assert runtime.health_state["requested_vector_provider"] == "pgvector"
    assert runtime.vector_store.expected_dimensionality == runtime.embedding_provider.dimension


def test_build_runtime_pgvector_fallback_disabled_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenPGVector:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("pgvector unavailable")

    monkeypatch.setattr(mvp_ingestion, "PGVectorStore", BrokenPGVector)

    with pytest.raises(RuntimeError, match="pgvector unavailable"):
        mvp_ingestion.build_runtime(
            {
                "providers": {
                    "embeddings": "local",
                    "vector_db": "pgvector",
                    "metadata_dsn": "postgres://example",
                },
                "paths": {"data_dir": str(tmp_path)},
                "storage": {"vector": {"allow_fallback": False}},
            }
        )


def test_dry_run_write_is_noop(tmp_path: Path) -> None:
    runtime = mvp_ingestion.build_runtime(
        {
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
            "paths": {"data_dir": str(tmp_path)},
            "storage": {"dry_run": True},
        }
    )
    conversation = {
        "id": "11111111-1111-1111-1111-111111111111",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "hello"}],
        "metadata": {"imported_at": "2026-01-01T00:00:00Z"},
    }
    memory_id = runtime.metadata_store.insert(conversation)
    runtime.vector_store.insert(
        memory_id,
        [{"chunk_index": 0, "role": "user", "text": "hello", "vector": [0.1] * 32}],
    )

    assert memory_id == conversation["id"]
    assert runtime.metadata_store.get(memory_id) is None


def test_unsupported_operations_raise_deterministic_errors(tmp_path: Path) -> None:
    metadata = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    vectors = InMemoryVectorStore(dimension=3)

    with pytest.raises(
        NotSupportedError,
        match="Metadata transactions are not exposed by this adapter API",
    ):
        metadata.begin_transaction()
    with pytest.raises(
        NotSupportedError, match="TTL is not supported by this metadata adapter"
    ):
        metadata.set_ttl("id-1", 60)
    with pytest.raises(
        NotSupportedError, match="TTL is not supported by this vector adapter"
    ):
        vectors.set_ttl("id-1", 60)


def test_metadata_store_rejects_non_uuid_and_injection_like_ids(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")

    with pytest.raises(ValueError, match="memory_id must be a valid UUID"):
        store.get("x' OR 1=1 --")

    with pytest.raises(ValueError, match="memory_id must be a valid UUID"):
        store.insert(
            {
                "id": "x' OR 1=1 --",
                "source": "manual",
                "timestamp": "2026-01-01T00:00:00Z",
                "messages": [],
                "metadata": {},
            }
        )


def test_metadata_store_get_many_caps_batch_size(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(501)]
    with pytest.raises(ValueError, match="Too many ids requested"):
        store.get_many(ids)


def test_sqlite_insert_does_not_replace_on_conversation_hash_conflict(
    tmp_path: Path,
) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    first = {
        "id": "11111111-1111-4111-8111-111111111111",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "first", "hash": "sha256:" + ("1" * 64)}],
        "metadata": {
            "imported_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "conversation_hash": "sha256:" + ("a" * 64),
        },
    }
    second = {
        "id": "22222222-2222-4222-8222-222222222222",
        "source": "manual",
        "timestamp": "2026-01-02T00:00:00Z",
        "messages": [{"role": "user", "text": "second", "hash": "sha256:" + ("2" * 64)}],
        "metadata": {
            "imported_at": "2026-01-02T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "conversation_hash": "sha256:" + ("a" * 64),
        },
    }

    store.insert(first)
    with pytest.raises(sqlite3.IntegrityError):
        store.insert(second)

    assert store.get(first["id"]) == first


def test_sqlite_insert_new_deduplicates_same_id_same_hash(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    payload = {
        "id": "11111111-1111-4111-8111-111111111111",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "first", "hash": "sha256:" + ("1" * 64)}],
        "metadata": {
            "imported_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "conversation_hash": "sha256:" + ("a" * 64),
        },
    }

    assert store.insert_new(payload) == (payload["id"], True)
    assert store.insert_new(payload) == (payload["id"], False)


def test_sqlite_insert_new_rejects_same_id_different_hash(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    first = {
        "id": "11111111-1111-4111-8111-111111111111",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "first", "hash": "sha256:" + ("1" * 64)}],
        "metadata": {
            "imported_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "conversation_hash": "sha256:" + ("a" * 64),
        },
    }
    second = {
        **first,
        "messages": [{"role": "user", "text": "second", "hash": "sha256:" + ("2" * 64)}],
        "metadata": {
            **first["metadata"],
            "conversation_hash": "sha256:" + ("b" * 64),
        },
    }

    assert store.insert_new(first) == (first["id"], True)
    with pytest.raises(ValueError, match="unauthorized_update"):
        store.insert_new(second)
    assert store.get(first["id"]) == first


def test_runtime_health_reports_degraded_on_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenLanceDB:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(mvp_ingestion, "LanceDBVectorStore", BrokenLanceDB)
    mvp_ingestion.configure_runtime(
        config={
            "providers": {"embeddings": "local", "vector_db": "lancedb"},
            "paths": {"data_dir": str(tmp_path)},
            "storage": {"vector": {"allow_fallback": True}},
        }
    )
    health = mvp_ingestion.runtime_health()
    assert health["mode"] == "degraded"
    assert health["vector_fallback_active"] is True
    assert health["vector_provider"] == "memory"


def test_runtime_health_reports_dry_run(tmp_path: Path) -> None:
    mvp_ingestion.configure_runtime(
        config={
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
            "paths": {"data_dir": str(tmp_path)},
            "storage": {"dry_run": True},
        }
    )
    health = mvp_ingestion.runtime_health()
    assert health["mode"] == "dry_run"
    assert health["vector_fallback_active"] is False


def test_runtime_dimensionality_mismatch_after_startup_raises() -> None:
    store = InMemoryVectorStore(dimension=32)
    store.insert(
        "11111111-1111-1111-1111-111111111111",
        [{"chunk_index": 0, "role": "user", "text": "hello", "vector": [0.1] * 32}],
    )
    # Simulate runtime drift after successful startup.
    store.dimension = 16
    with pytest.raises(VectorDimensionError, match="expected 16, got 32"):
        store.search([0.2] * 32, top_k=5)


def test_metadata_init_failure_fails_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenMetadataStore:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("metadata init failed")

    monkeypatch.setattr(mvp_ingestion, "SQLiteMetadataStore", BrokenMetadataStore)

    with pytest.raises(RuntimeError, match="metadata init failed"):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "in_memory"},
                "paths": {"data_dir": str(tmp_path)},
            }
        )


class _FakeCursor:
    def __init__(self, responses: dict[str, list[tuple[object, ...]]]):
        self._responses = responses
        self._current: list[tuple[object, ...]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params: object | None = None) -> None:
        _ = params
        self._current = list(self._responses.get(query.strip(), []))

    def fetchone(self):
        if not self._current:
            return None
        return self._current.pop(0)

    def fetchall(self):
        rows = list(self._current)
        self._current = []
        return rows


class _FakeConnection:
    def __init__(self, responses: dict[str, list[tuple[object, ...]]]):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _FakeCursor(self._responses)


def _fake_connect_with_responses(responses: dict[str, list[tuple[Any, ...]]]):
    def _connect(_dsn: str):
        return _FakeConnection(responses)

    return _connect


class _RecordingCursor:
    def __init__(self, state: dict[str, Any]):
        self.state = state
        self._current: list[tuple[Any, ...]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params: object | None = None) -> None:
        normalized = " ".join(query.split())
        self.state["queries"].append(normalized)
        self.state["params"].append(params)
        if normalized.startswith("SELECT version FROM memory_vector_schema_version"):
            self._current = [(1,)]
        elif normalized.startswith("SELECT COUNT(*) FROM memory_vectors"):
            self._current = [(len(self.state["rows"]),)]
        elif normalized.startswith("SELECT memory_id, chunk_id"):
            self._current = [
                (
                    "11111111-1111-1111-1111-111111111111",
                    "chunk-1",
                    0,
                    "sha256:abc",
                    "user",
                    "hello",
                    0.1,
                )
            ]
        elif normalized.startswith("DELETE FROM memory_vectors WHERE memory_id = %s"):
            memory_id = params[0] if isinstance(params, tuple) else None
            self.state["rows"] = [
                row for row in self.state["rows"] if row[0] != memory_id
            ]
            self._current = []
        elif normalized.startswith("INSERT INTO memory_vectors"):
            if isinstance(params, tuple):
                self.state["rows"].append(params)
            self._current = []
        else:
            self._current = []

    def fetchone(self):
        if not self._current:
            return None
        return self._current.pop(0)

    def fetchall(self):
        rows = list(self._current)
        self._current = []
        return rows


class _RecordingConnection:
    def __init__(self, state: dict[str, Any]):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _RecordingCursor(self.state)


def _recording_connect(state: dict[str, Any]):
    def _connect(_dsn: str):
        return _RecordingConnection(state)

    return _connect


def test_postgres_schema_version_invariant_and_health() -> None:
    responses = {
        "SELECT COUNT(*) FROM schema_version": [(1,)],
        "SELECT version FROM schema_version WHERE id = %s": [(1,)],
    }
    store = PostgresMetadataStore(
        "postgres://example",
        connect_fn=_fake_connect_with_responses(responses),
    )
    assert store.schema_version == 1
    assert store.health()["schema_version"] == 1


class _UniqueViolation(Exception):
    sqlstate = "23505"


class _UniqueViolationCursor:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query: str, params: object | None = None) -> None:
        _ = (query, params)
        raise _UniqueViolation("duplicate key value violates unique constraint")


class _UniqueViolationConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return _UniqueViolationCursor()


def test_postgres_insert_new_deduplicates_same_id_same_hash() -> None:
    payload = {
        "id": "11111111-1111-4111-8111-111111111111",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "first", "hash": "sha256:" + ("1" * 64)}],
        "metadata": {
            "imported_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "conversation_hash": "sha256:" + ("a" * 64),
        },
    }
    store = PostgresMetadataStore.__new__(PostgresMetadataStore)
    store._connect = lambda: _UniqueViolationConnection()
    store.get_by_conversation_hash = lambda conversation_hash: None
    store.get = lambda memory_id: payload

    assert store.insert_new(payload) == (payload["id"], False)


def test_postgres_insert_new_rejects_same_id_different_hash() -> None:
    first = {
        "id": "11111111-1111-4111-8111-111111111111",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "first", "hash": "sha256:" + ("1" * 64)}],
        "metadata": {
            "imported_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "conversation_hash": "sha256:" + ("a" * 64),
        },
    }
    second = {
        **first,
        "messages": [{"role": "user", "text": "second", "hash": "sha256:" + ("2" * 64)}],
        "metadata": {
            **first["metadata"],
            "conversation_hash": "sha256:" + ("b" * 64),
        },
    }
    store = PostgresMetadataStore.__new__(PostgresMetadataStore)
    store._connect = lambda: _UniqueViolationConnection()
    store.get_by_conversation_hash = lambda conversation_hash: None
    store.get = lambda memory_id: first

    with pytest.raises(ValueError, match="unauthorized_update"):
        store.insert_new(second)


def test_postgres_schema_version_missing_row_raises() -> None:
    responses = {
        "SELECT COUNT(*) FROM schema_version": [(1,)],
        "SELECT version FROM schema_version WHERE id = %s": [],
    }
    with pytest.raises(SchemaVersionError, match="schema_version row missing"):
        PostgresMetadataStore(
            "postgres://example", connect_fn=_fake_connect_with_responses(responses)
        )


def test_postgres_schema_version_invariant_violation_raises() -> None:
    responses = {
        "SELECT COUNT(*) FROM schema_version": [(2,)],
    }
    with pytest.raises(SchemaVersionError, match="invariant violated"):
        PostgresMetadataStore(
            "postgres://example", connect_fn=_fake_connect_with_responses(responses)
        )


def test_build_runtime_fails_on_incompatible_postgres_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class StubPostgresStore:
        schema_version = 99

        def __init__(self, _dsn: str):
            pass

        def health(self):
            return {"provider": "postgres", "schema_version": self.schema_version}

    monkeypatch.setattr(mvp_ingestion, "PostgresMetadataStore", StubPostgresStore)
    with pytest.raises(
        SchemaVersionError, match="Incompatible metadata schema version"
    ):
        mvp_ingestion.build_runtime(
            {
                "providers": {
                    "embeddings": "local",
                    "vector_db": "in_memory",
                    "metadata_db": "postgres",
                    "metadata_dsn": "postgres://example",
                },
                "paths": {"data_dir": str(tmp_path)},
                "storage": {"metadata_schema_versions": [1]},
            }
        )


def test_postgres_live_integration_when_dsn_provided() -> None:
    dsn = os.getenv("AMH_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("AMH_TEST_POSTGRES_DSN not set")

    store = PostgresMetadataStore(dsn)
    payload = {
        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "source": "ci",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "postgres smoke"}],
        "metadata": {"imported_at": "2026-01-01T00:00:00Z"},
    }
    memory_id = store.insert(payload)
    loaded = store.get(memory_id)
    assert loaded is not None
    assert loaded["id"] == payload["id"]
    assert store.health()["schema_version"] == 1


def test_pgvector_startup_insert_search_and_health() -> None:
    state: dict[str, Any] = {"queries": [], "params": [], "rows": []}
    store = PGVectorStore(
        "postgres://example",
        dimension=3,
        distance="l2",
        connect_fn=_recording_connect(state),
    )

    assert any("CREATE EXTENSION IF NOT EXISTS vector" in query for query in state["queries"])
    assert any("USING hnsw (vector vector_l2_ops)" in query for query in state["queries"])

    store.insert(
        "11111111-1111-1111-1111-111111111111",
        [
            {
                "chunk_id": "chunk-1",
                "chunk_index": 0,
                "message_hash": "sha256:abc",
                "role": "user",
                "text": "hello",
                "vector": [0.1, 0.2, 0.3],
            }
        ],
        replace=True,
    )
    matches = store.search([0.1, 0.2, 0.3], top_k=1)
    health = store.health()

    assert matches == [
        {
            "memory_id": "11111111-1111-1111-1111-111111111111",
            "chunk_id": "chunk-1",
            "chunk_index": 0,
            "message_hash": "sha256:abc",
            "role": "user",
            "text": "hello",
            "score": 0.1,
        }
    ]
    assert health["provider"] == "pgvector"
    assert health["distance"] == "l2"
    assert health["stats"]["rows"] == 1


def test_pgvector_live_integration_when_dsn_provided() -> None:
    dsn = os.getenv("AMH_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("AMH_TEST_POSTGRES_DSN not set")

    table_name = f"memory_vectors_contract_{uuid4().hex}"
    store = PGVectorStore(
        dsn,
        table_name=table_name,
        dimension=3,
        distance="cosine",
    )
    _assert_vector_store_contract(store)


def test_runtime_postgres_pgvector_live_integration_when_dsn_provided(
    tmp_path: Path,
) -> None:
    dsn = os.getenv("AMH_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("AMH_TEST_POSTGRES_DSN not set")

    runtime = mvp_ingestion.configure_runtime(
        config={
            "providers": {
                "embeddings": "local",
                "metadata_db": "postgres",
                "metadata_dsn": dsn,
                "vector_db": "pgvector",
            },
            "paths": {"data_dir": str(tmp_path)},
            "storage": {"vector": {"allow_fallback": False}},
        }
    )
    unique_text = f"postgres pgvector contract {uuid4().hex}"
    memory_id = str(uuid4())
    result = mvp_ingestion.ingest_messages(
        {
            "id": memory_id,
            "source": "ci",
            "timestamp": "2026-01-01T00:00:00Z",
            "messages": [{"role": "user", "text": unique_text}],
            "metadata": {"imported_at": "2026-01-01T00:00:00Z"},
        }
    )
    search_result = mvp_ingestion.search(unique_text, top_k=5)

    assert result["id"] == memory_id
    assert runtime.health_state["metadata_provider"] == "postgres"
    assert runtime.health_state["vector_provider"] == "pgvector"
    assert any(row["id"] == memory_id for row in search_result["results"])


def test_lancedb_index_is_created_lazily(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeTable:
        def __init__(self):
            self.add_calls = 0
            self.create_index_calls = 0

        def add(self, rows):
            _ = rows
            self.add_calls += 1

        def create_index(self, metric: str = "cosine", index_type: str = "IVF_HNSW_SQ"):
            _ = metric
            self.create_index_calls += 1

    class FakeLanceDBModule:
        @staticmethod
        def connect(_path: str):
            return object()

    fake_table = FakeTable()
    monkeypatch.setitem(
        sys.modules, "lancedb", types.SimpleNamespace(connect=FakeLanceDBModule.connect)
    )
    monkeypatch.setattr(
        LanceDBVectorStore, "_open_or_create_table", lambda self: fake_table
    )

    store = LanceDBVectorStore(tmp_path / "lancedb", dimension=3)
    assert fake_table.create_index_calls == 0

    store.insert(
        "11111111-1111-1111-1111-111111111111",
        [
            {
                "chunk_index": 0,
                "role": "user",
                "text": "hello",
                "vector": [0.1, 0.2, 0.3],
            }
        ],
    )
    assert fake_table.add_calls == 1
    assert fake_table.create_index_calls == 1

    store.insert(
        "11111111-1111-1111-1111-111111111111",
        [
            {
                "chunk_index": 1,
                "role": "assistant",
                "text": "world",
                "vector": [0.4, 0.5, 0.6],
            }
        ],
    )
    assert fake_table.create_index_calls == 1
