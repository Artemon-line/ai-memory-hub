from __future__ import annotations

from pathlib import Path

import pytest

from memory.backend.contracts import ProviderCapabilities
from memory.backend.errors import NotSupportedError, SchemaVersionError, VectorDimensionError
from memory.backend.log_safety import redact_secrets
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.postgres_metadata_store import PostgresMetadataStore
from memory.backend.vector_store import InMemoryVectorStore
from memory.ingestion import mvp_ingestion


def test_sqlite_capabilities_and_health(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    capabilities = store.capabilities()
    health = store.health()

    assert isinstance(capabilities, ProviderCapabilities)
    assert capabilities.supports_transactions is True
    assert health["schema_version"] == 1


def test_inmemory_vector_dimension_validation_insert_and_search() -> None:
    store = InMemoryVectorStore(dimension=3)

    with pytest.raises(VectorDimensionError):
        store.insert(
            "id-1",
            [{"chunk_index": 0, "role": "user", "text": "x", "vector": [1.0, 2.0]}],
        )

    with pytest.raises(VectorDimensionError):
        store.search([1.0, 2.0], top_k=5)


def test_build_runtime_fails_fast_on_schema_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(SQLiteMetadataStore, "schema_version", 99)
    config = {
        "providers": {"embeddings": "local", "vector_db": "pgvector"},
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
            return {"provider": "memory", "expected_dimensionality": self.expected_dimensionality}

    monkeypatch.setattr(mvp_ingestion, "InMemoryVectorStore", MismatchVectorStore)
    with pytest.raises(VectorDimensionError, match="dimensionality mismatch at startup"):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "pgvector"},
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

    assert runtime.vector_store.expected_dimensionality == runtime.embedding_provider.dimension


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
            raise RuntimeError("connect postgres://u:pw123@db/app password=letmein token=tok123")

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


def test_dry_run_write_is_noop(tmp_path: Path) -> None:
    runtime = mvp_ingestion.build_runtime(
        {
            "providers": {"embeddings": "local", "vector_db": "pgvector"},
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

    with pytest.raises(NotSupportedError, match="Metadata transactions are not exposed by this adapter API"):
        metadata.begin_transaction()
    with pytest.raises(NotSupportedError, match="TTL is not supported by this metadata adapter"):
        metadata.set_ttl("id-1", 60)
    with pytest.raises(NotSupportedError, match="TTL is not supported by this vector adapter"):
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
            "providers": {"embeddings": "local", "vector_db": "pgvector"},
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
                "providers": {"embeddings": "local", "vector_db": "pgvector"},
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


def _fake_connect_with_responses(responses: dict[str, list[tuple[object, ...]]]):
    def _connect(_dsn: str):
        return _FakeConnection(responses)

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


def test_postgres_schema_version_missing_row_raises() -> None:
    responses = {
        "SELECT COUNT(*) FROM schema_version": [(1,)],
        "SELECT version FROM schema_version WHERE id = %s": [],
    }
    with pytest.raises(SchemaVersionError, match="schema_version row missing"):
        PostgresMetadataStore("postgres://example", connect_fn=_fake_connect_with_responses(responses))


def test_postgres_schema_version_invariant_violation_raises() -> None:
    responses = {
        "SELECT COUNT(*) FROM schema_version": [(2,)],
    }
    with pytest.raises(SchemaVersionError, match="invariant violated"):
        PostgresMetadataStore("postgres://example", connect_fn=_fake_connect_with_responses(responses))


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
    with pytest.raises(SchemaVersionError, match="Incompatible metadata schema version"):
        mvp_ingestion.build_runtime(
            {
                "providers": {
                    "embeddings": "local",
                    "vector_db": "pgvector",
                    "metadata_db": "postgres",
                    "metadata_dsn": "postgres://example",
                },
                "paths": {"data_dir": str(tmp_path)},
                "storage": {"metadata_schema_versions": [1]},
            }
        )
