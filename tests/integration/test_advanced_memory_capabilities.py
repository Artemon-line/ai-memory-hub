from __future__ import annotations

from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.mongodb_metadata_store import MongoDBMetadataStore
from memory.backend.postgres_metadata_store import PostgresMetadataStore


def test_sqlite_reports_advanced_memory_capabilities(tmp_path) -> None:
    capabilities = SQLiteMetadataStore(tmp_path / "metadata.sqlite3").capabilities()

    assert capabilities.supports_graph_records is True
    assert capabilities.supports_decay_fields is True
    assert capabilities.supports_shared_scopes is True
    assert capabilities.supports_plugin_metadata is True


def test_postgres_capabilities_include_advanced_memory_flags() -> None:
    capabilities = PostgresMetadataStore.__new__(PostgresMetadataStore).capabilities()

    assert capabilities.supports_graph_records is True
    assert capabilities.supports_decay_fields is True
    assert capabilities.supports_shared_scopes is True
    assert capabilities.supports_plugin_metadata is True


def test_mongodb_capabilities_stay_conservative_for_unimplemented_advanced_storage() -> None:
    capabilities = MongoDBMetadataStore.__new__(MongoDBMetadataStore).capabilities()

    assert capabilities.supports_graph_records is False
    assert capabilities.supports_decay_fields is False
    assert capabilities.supports_shared_scopes is True
    assert capabilities.supports_plugin_metadata is False
