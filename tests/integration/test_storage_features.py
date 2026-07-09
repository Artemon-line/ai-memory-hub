from __future__ import annotations

import importlib.util
import json
import logging
import os
import sqlite3
import struct
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
from memory.backend.mongodb_metadata_store import MongoDBMetadataStore
from memory.backend.postgres_metadata_store import PostgresMetadataStore
from memory.backend.vector_store import (
    ChromaDBVectorStore,
    ChromaMetadata,
    ElasticsearchVectorStore,
    InMemoryVectorStore,
    LanceDBVectorStore,
    MilvusVectorStore,
    MongoDBAtlasVectorStore,
    OpenSearchVectorStore,
    PGVectorStore,
    PineconeVectorStore,
    QdrantVectorStore,
    RedisVectorStore,
    TurbopufferVectorStore,
    TypesenseVectorStore,
    VespaVectorStore,
    WeaviateVectorStore,
)
from memory.ingestion import mvp_ingestion

VectorStoreFactory = Callable[[Path], Any]


def test_sqlite_capabilities_and_health(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    capabilities = store.capabilities()
    health = store.health()

    assert isinstance(capabilities, ProviderCapabilities)
    assert capabilities.supports_transactions is True
    assert health["schema_version"] == 1


def _assert_metadata_store_contract(store: Any) -> None:
    memory_id = str(uuid4())
    thread_id = f"thread-{uuid4().hex}"
    conversation_hash = "sha256:" + ("1" * 64)
    conversation = {
        "id": memory_id,
        "source": "contract",
        "timestamp": "2026-01-01T00:00:00Z",
        "title": "metadata contract",
        "messages": [
            {
                "role": "user",
                "text": "contract memory",
                "hash": "sha256:" + ("3" * 64),
            }
        ],
        "metadata": {
            "conversation_hash": conversation_hash,
            "upstream_thread_id": thread_id,
            "index_chunks": [
                {
                    "chunk_id": f"{memory_id}:0",
                    "chunk_index": 0,
                    "message_hash": "sha256:" + ("2" * 64),
                    "role": "user",
                    "text": "contract memory",
                }
            ],
        },
    }

    inserted_id, inserted = store.insert_new(conversation)

    assert (inserted_id, inserted) == (memory_id, True)
    assert store.insert_new(conversation) == (memory_id, False)
    assert store.get(memory_id) == conversation
    assert store.get_many([memory_id]) == {memory_id: conversation}
    assert store.get_by_conversation_hash(conversation_hash) == conversation
    assert store.get_by_upstream_thread("contract", thread_id) == conversation
    assert store.is_fully_indexed(memory_id) is False

    store.mark_chunks_indexed(memory_id, [f"{memory_id}:0"])

    assert store.is_fully_indexed(memory_id) is True


def test_metadata_store_contract_for_sqlite(tmp_path: Path) -> None:
    _assert_metadata_store_contract(SQLiteMetadataStore(tmp_path / "metadata.sqlite3"))


def test_metadata_store_contract_for_mongodb_fake_client() -> None:
    _assert_metadata_store_contract(
        MongoDBMetadataStore(uri="mongodb://example", client=FakeMongoClient())
    )


def test_mongodb_insert_new_rejects_same_id_different_hash() -> None:
    store = MongoDBMetadataStore(uri="mongodb://example", client=FakeMongoClient())
    memory_id = str(uuid4())
    first = {
        "id": memory_id,
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "first"}],
        "metadata": {"conversation_hash": "sha256:" + ("a" * 64)},
    }
    second = {
        "id": memory_id,
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "second"}],
        "metadata": {"conversation_hash": "sha256:" + ("b" * 64)},
    }

    assert store.insert_new(first) == (memory_id, True)
    with pytest.raises(ValueError, match="unauthorized_update"):
        store.insert_new(second)


def test_mongodb_schema_version_document_and_indexes() -> None:
    client = FakeMongoClient()
    store = MongoDBMetadataStore(uri="mongodb://example", client=client)
    db = client["ai_memory_hub"]

    assert db["schema_versions"].find_one({"id": 1}) == {"id": 1, "version": 1}
    assert store.health()["schema_collection"] == "schema_versions"

    conversation_indexes = db["conversations"].indexes
    assert {"args": ("id",), "kwargs": {"unique": True}} in conversation_indexes
    assert {"args": ("conversation_hash",), "kwargs": {}} in conversation_indexes
    assert {
        "args": ([("project_id", 1), ("conversation_hash", 1)],),
        "kwargs": {
            "unique": True,
            "partialFilterExpression": {"conversation_hash": {"$type": "string"}},
        },
    } in conversation_indexes
    assert {
        "args": ([("project_id", 1), ("source", 1), ("upstream_thread_id", 1)],),
        "kwargs": {"partialFilterExpression": {"upstream_thread_id": {"$type": "string"}}},
    } in conversation_indexes


def test_mongodb_schema_version_invariant_failures() -> None:
    missing_id_client = FakeMongoClient()
    missing_id_client["ai_memory_hub"]["schema_versions"].insert_one({"id": 2, "version": 1})
    with pytest.raises(RuntimeError, match="schema version row missing"):
        MongoDBMetadataStore(uri="mongodb://example", client=missing_id_client)

    too_many_client = FakeMongoClient()
    too_many_client["ai_memory_hub"]["schema_versions"].insert_many(
        [{"id": 1, "version": 1}, {"id": 2, "version": 1}]
    )
    with pytest.raises(RuntimeError, match="schema version invariant violation"):
        MongoDBMetadataStore(uri="mongodb://example", client=too_many_client)

    incompatible_client = FakeMongoClient()
    incompatible_client["ai_memory_hub"]["schema_versions"].insert_one({"id": 1, "version": 99})
    with pytest.raises(RuntimeError, match="Incompatible MongoDB schema version"):
        MongoDBMetadataStore(uri="mongodb://example", client=incompatible_client)


def test_sqlite_metadata_store_persists_generated_summaries_separately(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    summary = {
        "id": "summary:abc123",
        "type": "profile",
        "target_id": "user",
        "owner_id": "owner-a",
        "project_id": "project-a",
        "text": "profile_name: Tyran",
        "basis": "active_facts",
        "provenance_status": "fact_ids",
        "filters": {"status": "active"},
        "provenance": [{"fact_id": "fact-1"}],
        "generated_at": "2026-01-01T00:00:00Z",
    }

    stored_id = store.upsert_generated_summary(summary)
    loaded = store.get_generated_summary(stored_id)

    assert stored_id == "summary:abc123"
    assert loaded == summary
    assert store.get("d9fd4c95-9cb3-4fd5-b967-3027f8863210") is None


def test_sqlite_metadata_store_tracks_index_chunk_manifest(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    conversation = {
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "alpha beta gamma", "hash": "sha256:" + "a" * 64}],
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


def test_sqlite_fact_storage_persists_freshness_and_source_quality(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    fact = {
        "id": "fact-1",
        "subject": "user",
        "predicate": "favorite_holiday",
        "object": "aniversary",
        "qualifiers": {"source_role": "user"},
        "confidence": "high",
        "source_conversation_id": "11111111-1111-4111-8111-111111111111",
        "source_message_indexes": [0],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "last_confirmed_at": "2026-01-01T00:00:00Z",
        "superseded_by": None,
        "superseded_at": None,
        "deleted_at": None,
        "source_quality": "direct_user_statement",
        "confidence_reason": "Extracted from a direct user statement.",
        "object_raw": "aniversary",
        "object_normalized": "anniversary",
    }

    store.insert_facts([fact])
    loaded = store.search_facts(subject="user", predicate="favorite_holiday")[0]

    assert loaded["source_quality"] == "direct_user_statement"
    assert loaded["confidence_reason"] == "Extracted from a direct user statement."
    assert loaded["last_confirmed_at"] == "2026-01-01T00:00:00Z"
    assert loaded["object_raw"] == "aniversary"
    assert loaded["object_normalized"] == "anniversary"

    replacement = dict(fact)
    replacement["id"] = "fact-2"
    replacement["object"] = "anniversary"
    replacement["object_raw"] = "anniversary"
    replacement["qualifiers"] = {"source_role": "user", "corrects": "aniversary"}
    replacement["source_quality"] = "corrected_by_user"
    replacement["confidence_reason"] = "Extracted from a direct user correction."
    replacement["created_at"] = "2026-01-02T00:00:00Z"
    replacement["updated_at"] = "2026-01-02T00:00:00Z"
    replacement["last_confirmed_at"] = "2026-01-02T00:00:00Z"
    store.insert_facts([replacement])
    audit = store.search_facts(
        subject="user", predicate="favorite_holiday", include_superseded=True
    )
    superseded = next(row for row in audit if row["id"] == "fact-1")

    assert superseded["superseded_by"] == "fact-2"
    assert superseded["superseded_at"] == "2026-01-02T00:00:00Z"


def test_sqlite_graph_records_persist_and_supersede_conflicts(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    entity = {
        "id": "entity:velvet",
        "record_type": "entity",
        "entity_type": "project",
        "name": "Velvet Lantern",
        "normalized_name": "velvet lantern",
        "extractor": {"name": "deterministic-graph", "version": "1", "kind": "deterministic"},
        "confidence": 0.9,
        "source_scope": "private",
        "review_status": "active",
        "provenance": [{"conversation_id": "conv-1", "message_index": 0}],
        "owner_id": "owner-a",
        "project_id": "project-a",
        "created_at": "2026-07-08T00:00:00Z",
        "updated_at": "2026-07-08T00:00:00Z",
    }
    first = {
        "id": "rel:first",
        "record_type": "relationship",
        "subject": "Velvet Lantern",
        "predicate": "changed_to",
        "object": "blue",
        "extractor": {"name": "deterministic-graph", "version": "1", "kind": "deterministic"},
        "confidence": 0.85,
        "source_scope": "private",
        "review_status": "active",
        "provenance": [{"conversation_id": "conv-1", "message_index": 0}],
        "owner_id": "owner-a",
        "project_id": "project-a",
        "conflict_group": "velvet lantern:changed_to",
        "created_at": "2026-07-08T00:00:00Z",
        "updated_at": "2026-07-08T00:00:00Z",
    }
    second = {**first, "id": "rel:second", "object": "green", "updated_at": "2026-07-09T00:00:00Z"}

    store.upsert_graph_records(entities=[entity], relationships=[first])
    store.upsert_graph_records(entities=[], relationships=[second])

    entities = store.search_graph_entities(name="Velvet Lantern", project_id="project-a")
    active = store.search_graph_relationships(project_id="project-a")
    audit = store.search_graph_relationships(project_id="project-a", include_superseded=True)

    assert entities[0]["id"] == "entity:velvet"
    assert [row["object"] for row in active] == ["green"]
    assert next(row for row in audit if row["id"] == "rel:first")["superseded_by"] == "rel:second"


def test_sqlite_admin_user_token_and_project_management(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")

    user = store.create_user(user_id="jane", display_name="Jane")
    token = store.create_auth_token(
        owner_id="jane",
        token="amh_secret_value",
        token_display_name="laptop",
    )
    project = store.create_project(project_id="shared-321", owner_id="jane", name="Shared 321")
    store.create_user(user_id="carl", display_name="Carl")
    store.add_project_member(project_id="shared-321", user_id="carl", role="writer")

    assert user["id"] == "jane"
    assert any(row["id"] == "jane" and row["display_name"] == "Jane" for row in store.list_users())
    assert token["token_id"].startswith("tok_")
    assert token["token_prefix"] == "amh_secret_v"
    assert token["scopes"] == ["memory:read", "memory:write"]
    assert token["last_used_at"] is None
    assert "token_hash" not in token
    assert store.owner_for_token("amh_secret_value") == "jane"
    listed_tokens = store.list_auth_tokens(owner_id="jane")
    assert listed_tokens[0]["last_used_at"] is not None
    listed_without_last_used = {
        key: value for key, value in listed_tokens[0].items() if key != "last_used_at"
    }
    token_without_last_used = {key: value for key, value in token.items() if key != "last_used_at"}
    assert listed_without_last_used == token_without_last_used
    assert "token_hash" not in listed_tokens[0]
    assert project["id"] == "shared-321"
    assert "shared-321" in {row["id"] for row in store.list_projects(user_id="carl")}
    members = store.list_project_members(project_id="shared-321")
    assert {member["user_id"]: member["role"] for member in members} == {
        "carl": "writer",
        "jane": "admin",
    }

    revoked = store.revoke_auth_token(token["token_id"][:12])

    assert revoked is not None
    assert revoked["revoked_at"] is not None
    assert store.owner_for_token("amh_secret_value") is None


def test_sqlite_auth_token_id_migration_backfills_existing_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE users (
                id TEXT PRIMARY KEY,
                display_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                disabled_at TEXT
            )
            """
        )
        conn.execute("INSERT INTO users (id, display_name) VALUES (?, ?)", ("jane", "Jane"))
        conn.execute(
            """
            CREATE TABLE auth_tokens (
                token_hash TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT,
                revoked_at TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO auth_tokens (token_hash, owner_id) VALUES (?, ?)",
            ("sha256:" + "a" * 64, "jane"),
        )

    store = SQLiteMetadataStore(db_path)
    tokens = store.list_auth_tokens(owner_id="jane")

    assert len(tokens) == 1
    assert tokens[0]["token_id"].startswith("tok_")
    assert "token_hash" not in tokens[0]


def test_inmemory_vector_dimension_validation_insert_and_search() -> None:
    store = InMemoryVectorStore(dimension=3)

    with pytest.raises(VectorDimensionError):
        store.insert(
            "id-1",
            [{"chunk_index": 0, "role": "user", "text": "x", "vector": [1.0, 2.0]}],
        )

    with pytest.raises(VectorDimensionError):
        store.search([1.0, 2.0], top_k=5)


def test_chroma_metadata_model_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError):
        ChromaMetadata.from_query(
            {
                "memory_id": str(uuid4()),
                "chunk_id": "chunk-1",
                "chunk_index": 0,
                "unexpected": "value",
            },
            fallback_chunk_id="chunk-1",
        )


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
        row for row in store.search([1.0, 0.0, 0.0], top_k=10) if row["memory_id"] == replacement_id
    ]
    assert len(replacement_matches) == 1
    assert replacement_matches[0]["chunk_index"] == 1
    assert replacement_matches[0]["text"] == "new replacement"

    stats = store.get_stats()
    assert stats["expected_dimensionality"] == 3
    assert stats["provider"] in {
        "memory",
        "lancedb",
        "pgvector",
        "chromadb",
        "elasticsearch",
        "milvus",
        "qdrant",
        "mongodb_atlas",
        "opensearch",
        "redis",
        "pinecone",
        "turbopuffer",
        "vespa",
        "typesense",
        "weaviate",
        "fake",
    }
    assert stats["rows"] is None or stats["rows"] >= 3

    store.delete([first_id])
    deleted_matches = [
        row for row in store.search([1.0, 0.0, 0.0], top_k=10) if row["memory_id"] == first_id
    ]
    assert deleted_matches == []


class FakeVectorClient:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def upsert(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.rows[row["chunk_id"]] = dict(row)

    def delete_by_memory_id(self, memory_id: str) -> None:
        self.rows = {
            chunk_id: row for chunk_id, row in self.rows.items() if row["memory_id"] != memory_id
        }

    def search(self, query_vector: list[float], top_k: int) -> list[dict[str, Any]]:
        rows = sorted(
            self.rows.values(),
            key=lambda row: _test_cosine_distance(query_vector, row["vector"]),
        )
        return rows[:top_k]


class FakeSDKVectorStore:
    def __init__(self, *, client: FakeVectorClient, dimension: int) -> None:
        self.client = client
        self.dimension = dimension

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def insert(
        self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False
    ) -> None:
        if replace:
            self.client.delete_by_memory_id(metadata_id)
        rows = []
        for item in embeddings:
            vector = item["vector"]
            self._validate_dimension(vector)
            rows.append(
                {
                    **item,
                    "memory_id": metadata_id,
                    "score": 1.0,
                }
            )
        self.client.upsert(rows)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        self._validate_dimension(query_vector)
        return [
            {**row, "score": 1.0 - _test_cosine_distance(query_vector, row["vector"])}
            for row in self.client.search(query_vector, top_k)
        ]

    def delete(self, ids: list[str]) -> None:
        for memory_id in ids:
            self.client.delete_by_memory_id(memory_id)

    def get_stats(self) -> dict[str, Any]:
        return {
            "provider": "fake",
            "rows": len(self.client.rows),
            "expected_dimensionality": self.dimension,
        }

    def health(self) -> dict[str, Any]:
        return {"provider": "fake", "status": "ok", "dimension": self.dimension}

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_batch_insert=False,
            supports_transactions=False,
            supports_ttl=False,
            supports_tags=False,
            supports_metadata_indexing=False,
        )

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = memory_id, ttl_seconds
        raise NotSupportedError("TTL is not supported by this vector adapter")

    def _validate_dimension(self, vector: list[float]) -> None:
        if len(vector) != self.dimension:
            raise VectorDimensionError(
                f"Vector dimensionality mismatch: expected {self.dimension}, got {len(vector)}"
            )


def _test_cosine_distance(a: list[float], b: list[float]) -> float:
    dot = sum(left * right for left, right in zip(a, b))
    norm_a = sum(value * value for value in a) ** 0.5
    norm_b = sum(value * value for value in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - (dot / (norm_a * norm_b))


class FakeChromaCollection:
    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.metadata = dict(metadata or {})

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        for index, chunk_id in enumerate(ids):
            self.rows[chunk_id] = {
                "id": chunk_id,
                "embedding": embeddings[index],
                "document": documents[index],
                "metadata": dict(metadatas[index]),
            }

    def query(
        self, *, query_embeddings: list[list[float]], n_results: int, include: list[str]
    ) -> dict[str, Any]:
        _ = include
        query_vector = query_embeddings[0]
        rows = sorted(
            self.rows.values(),
            key=lambda row: _test_cosine_distance(query_vector, row["embedding"]),
        )[:n_results]
        return {
            "ids": [[row["id"] for row in rows]],
            "metadatas": [[row["metadata"] for row in rows]],
            "documents": [[row["document"] for row in rows]],
            "distances": [[_test_cosine_distance(query_vector, row["embedding"]) for row in rows]],
        }

    def delete(self, *, where: dict[str, str]) -> None:
        memory_id = where["memory_id"]
        self.rows = {
            chunk_id: row
            for chunk_id, row in self.rows.items()
            if row["metadata"]["memory_id"] != memory_id
        }

    def count(self) -> int:
        return len(self.rows)


class FakeChromaClient:
    def __init__(self, collection_metadata: dict[str, Any] | None = None) -> None:
        self.collection = FakeChromaCollection(collection_metadata)

    def get_or_create_collection(self, *, name: str, metadata: dict[str, Any]) -> Any:
        assert name == "memory_vectors"
        assert metadata == {
            "hnsw:space": "cosine",
            "schema_version": 1,
            "expected_dimensionality": 3,
            "distance": "cosine",
        }
        if not self.collection.metadata:
            self.collection.metadata = dict(metadata)
        return self.collection


def test_chromadb_collection_metadata_mismatch_fails_startup() -> None:
    with pytest.raises(RuntimeError, match="ChromaDB collection metadata mismatch"):
        ChromaDBVectorStore(
            client=FakeChromaClient(
                collection_metadata={
                    "hnsw:space": "cosine",
                    "schema_version": 1,
                    "expected_dimensionality": 99,
                    "distance": "cosine",
                }
            ),
            dimension=3,
        )


def test_qdrant_existing_collection_dimension_mismatch_fails_startup() -> None:
    with pytest.raises(VectorDimensionError, match="Qdrant collection dimensionality mismatch"):
        QdrantVectorStore(
            client=FakeQdrantClient(
                existing_config=FakeQdrantModels.VectorParams(size=99, distance="Cosine")
            ),
            models=FakeQdrantModels,
            dimension=3,
        )


def test_qdrant_existing_collection_distance_mismatch_fails_startup() -> None:
    with pytest.raises(RuntimeError, match="Qdrant collection distance mismatch"):
        QdrantVectorStore(
            client=FakeQdrantClient(
                existing_config=FakeQdrantModels.VectorParams(size=3, distance="Dot")
            ),
            models=FakeQdrantModels,
            dimension=3,
            distance="cosine",
        )


class FakeQdrantPoint:
    def __init__(self, *, payload: dict[str, Any], score: float) -> None:
        self.payload = payload
        self.score = score


class FakeQdrantModels:
    class PointStruct:
        def __init__(self, *, id: str, vector: list[float], payload: dict[str, Any]) -> None:
            self.id = id
            self.vector = vector
            self.payload = payload

    class VectorParams:
        def __init__(self, *, size: int, distance: str) -> None:
            self.size = size
            self.distance = distance

    class MatchAny:
        def __init__(self, *, any: list[str]) -> None:
            self.any = any

    class FieldCondition:
        def __init__(self, *, key: str, match: Any) -> None:
            self.key = key
            self.match = match

    class Filter:
        def __init__(self, *, must: list[Any]) -> None:
            self.must = must

    class FilterSelector:
        def __init__(self, *, filter: Any) -> None:
            self.filter = filter


class FakeQdrantClient:
    def __init__(self, existing_config: Any | None = None) -> None:
        self.rows: dict[str, Any] = {}
        self.created: dict[str, Any] = {}
        if existing_config is not None:
            self.created["memory_vectors"] = existing_config

    def collection_exists(self, collection_name: str) -> bool:
        return collection_name in self.created

    def create_collection(self, *, collection_name: str, vectors_config: Any) -> None:
        self.created[collection_name] = vectors_config

    def upsert(self, *, collection_name: str, points: list[Any]) -> None:
        assert collection_name == "memory_vectors"
        for point in points:
            self.rows[str(point.id)] = point

    def query_points(
        self, *, collection_name: str, query: list[float], limit: int, with_payload: bool
    ) -> Any:
        _ = (collection_name, with_payload)
        points = sorted(
            self.rows.values(),
            key=lambda point: _test_cosine_distance(query, point.vector),
        )
        return types.SimpleNamespace(
            points=[
                FakeQdrantPoint(
                    payload=point.payload,
                    score=1.0 - _test_cosine_distance(query, point.vector),
                )
                for point in points[:limit]
            ]
        )

    def delete(self, *, collection_name: str, points_selector: Any) -> None:
        _ = collection_name
        ids = set(points_selector.filter.must[0].match.any)
        self.rows = {
            point_id: point
            for point_id, point in self.rows.items()
            if point.payload["memory_id"] not in ids
        }

    def get_collection(self, *, collection_name: str) -> Any:
        return types.SimpleNamespace(
            points_count=len(self.rows),
            config=types.SimpleNamespace(
                params=types.SimpleNamespace(vectors=self.created.get(collection_name))
            ),
        )


class FakeMilvusClient:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.collections: dict[str, dict[str, Any]] = {}
        self.flushes: list[str] = []
        self.indexes: list[dict[str, Any]] = []
        self.loaded: list[str] = []
        self.load_state = "Loaded"

    def has_collection(self, *, collection_name: str) -> bool:
        return collection_name in self.collections

    def create_collection(
        self,
        *,
        collection_name: str,
        dimension: int,
        metric_type: str,
        auto_id: bool,
        id_type: str = "int",
        max_length: int | None = None,
    ) -> None:
        self.collections[collection_name] = {
            "dimension": dimension,
            "metric_type": metric_type,
            "id_type": id_type,
            "max_length": max_length,
            "auto_id": auto_id,
        }

    def upsert(self, *, collection_name: str, data: list[dict[str, Any]]) -> None:
        assert collection_name == "memory_vectors"
        for row in data:
            self.rows[str(row["id"])] = dict(row)

    def search(
        self,
        *,
        collection_name: str,
        data: list[list[float]],
        anns_field: str,
        limit: int,
        output_fields: list[str],
    ) -> list[list[dict[str, Any]]]:
        _ = (collection_name, anns_field, output_fields)
        query_vector = data[0]
        rows = sorted(
            self.rows.values(),
            key=lambda row: _test_cosine_distance(query_vector, row["vector"]),
        )
        return [
            [
                {
                    "entity": {key: row[key] for key in output_fields if key in row},
                    "distance": 1.0 - _test_cosine_distance(query_vector, row["vector"]),
                }
                for row in rows[:limit]
            ]
        ]

    def delete(self, *, collection_name: str, filter: str) -> None:
        _ = collection_name
        ids = {
            item.strip().strip('"') for item in filter.split("[", 1)[1].split("]", 1)[0].split(",")
        }
        self.rows = {
            row_id: row for row_id, row in self.rows.items() if row["memory_id"] not in ids
        }

    def flush(self, *, collection_name: str) -> None:
        self.flushes.append(collection_name)

    def get_collection_stats(self, *, collection_name: str) -> dict[str, int]:
        _ = collection_name
        return {"row_count": len(self.rows)}

    def describe_collection(self, *, collection_name: str) -> dict[str, Any]:
        return self.collections[collection_name]

    def create_index(
        self, *, collection_name: str, field_name: str, index_params: dict[str, Any]
    ) -> None:
        self.indexes.append(
            {
                "collection_name": collection_name,
                "field_name": field_name,
                "index_params": index_params,
            }
        )

    def load_collection(self, *, collection_name: str) -> None:
        self.loaded.append(collection_name)

    def get_load_state(self, *, collection_name: str) -> dict[str, str]:
        _ = collection_name
        return {"state": self.load_state}


class FakeSearchIndices:
    def __init__(self, *, fail_exists_status: int | None = None) -> None:
        self.created: dict[str, dict[str, Any]] = {}
        self.fail_exists_status = fail_exists_status

    def exists(self, *, index: str) -> bool:
        if self.fail_exists_status is not None:
            raise FakeSearchStatusError(self.fail_exists_status)
        return index in self.created

    def create(self, *, index: str, **kwargs: Any) -> None:
        self.created[index] = dict(kwargs)

    def get_mapping(self, *, index: str) -> dict[str, Any]:
        config = self.created[index]
        mappings = config.get("mappings") or config.get("body", {}).get("mappings", {})
        return {index: {"mappings": mappings}}


class FakeSearchStatusError(Exception):
    def __init__(self, status: int) -> None:
        self.meta = types.SimpleNamespace(status=status)
        super().__init__(f"search status {status}")


class FakeSearchClient:
    def __init__(self, *, opensearch: bool = False, fail_exists_status: int | None = None) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.indices = FakeSearchIndices(fail_exists_status=fail_exists_status)
        self.opensearch = opensearch

    def index(
        self,
        *,
        index: str,
        id: str,
        refresh: bool,
        document: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> None:
        _ = (index, refresh)
        self.rows[id] = dict(document or body or {})

    def search(
        self,
        *,
        index: str,
        size: int | None = None,
        knn: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = index
        if knn is not None:
            query_vector = knn["query_vector"]
            limit = int(size or knn["k"])
        else:
            assert body is not None
            query = body["query"]["knn"]["vector"]
            query_vector = query["vector"]
            limit = int(body["size"])
        rows = sorted(
            self.rows.values(),
            key=lambda row: _test_cosine_distance(query_vector, row["vector"]),
        )
        return {
            "hits": {
                "hits": [
                    {
                        "_source": row,
                        "_score": 1.0 - _test_cosine_distance(query_vector, row["vector"]),
                    }
                    for row in rows[:limit]
                ]
            }
        }

    def delete_by_query(self, *, index: str, refresh: bool, conflicts: str, **kwargs: Any) -> None:
        _ = (index, refresh, conflicts)
        query = kwargs.get("query") or kwargs.get("body", {}).get("query", {})
        ids = set(query["terms"]["memory_id"])
        self.rows = {
            row_id: row for row_id, row in self.rows.items() if row["memory_id"] not in ids
        }

    def count(self, *, index: str) -> dict[str, int]:
        _ = index
        return {"count": len(self.rows)}


class FakeRedisSearch:
    def __init__(self, client: "FakeRedisClient", index_name: str) -> None:
        self.client = client
        self.index_name = index_name

    def info(self) -> dict[str, int]:
        if self.index_name not in self.client.indices:
            raise RuntimeError("Unknown index name")
        return {"num_docs": len(self.client.rows)}

    def search(self, query: Any, *, query_params: dict[str, bytes] | None = None) -> Any:
        query_text = _fake_redis_query_text(query)
        if query_text.startswith("@memory_id:"):
            memory_id = query_text.split("{", 1)[1].split("}", 1)[0]
            docs = [
                self._document_for(key, row, distance=0.0)
                for key, row in self.client.rows.items()
                if row["memory_id"] == memory_id
            ]
            return types.SimpleNamespace(docs=docs)
        query_vector = _fake_redis_vector_from_bytes((query_params or {})["vector"])
        docs = [
            self._document_for(key, row, distance=distance)
            for key, row, distance in sorted(
                (
                    (
                        key,
                        row,
                        _test_cosine_distance(
                            query_vector, _fake_redis_vector_from_bytes(row["vector"])
                        ),
                    )
                    for key, row in self.client.rows.items()
                ),
                key=lambda item: item[2],
            )
        ]
        return types.SimpleNamespace(docs=docs)

    def _document_for(self, key: str, row: dict[str, Any], *, distance: float) -> Any:
        return types.SimpleNamespace(
            id=key,
            vector_distance=str(distance),
            **{
                item_key: item_value for item_key, item_value in row.items() if item_key != "vector"
            },
        )


class FakeRedisClient:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.indices: set[str] = set()

    def ft(self, index_name: str) -> FakeRedisSearch:
        return FakeRedisSearch(self, index_name)

    def execute_command(self, *args: Any) -> None:
        if args[:1] == ("FT.CREATE",):
            self.indices.add(str(args[1]))

    def hset(self, key: str, *, mapping: dict[str, Any]) -> None:
        self.rows[key] = dict(mapping)

    def delete(self, key: str) -> None:
        self.rows.pop(key, None)


def _fake_redis_vector_from_bytes(value: bytes) -> list[float]:
    return list(struct.unpack(f"{len(value) // 4}f", value))


def _fake_redis_query_text(value: Any) -> str:
    return str(getattr(value, "_query_string", value))


class FakePineconeIndex:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def upsert(self, *, vectors: list[dict[str, Any]], namespace: str) -> None:
        _ = namespace
        for vector in vectors:
            self.rows[str(vector["id"])] = dict(vector)

    def query(
        self,
        *,
        vector: list[float],
        top_k: int,
        namespace: str,
        include_metadata: bool,
    ) -> Any:
        _ = (namespace, include_metadata)
        rows = sorted(
            self.rows.values(),
            key=lambda row: _test_cosine_distance(vector, row["values"]),
        )
        return {
            "matches": [
                {
                    "metadata": row["metadata"],
                    "score": 1.0 - _test_cosine_distance(vector, row["values"]),
                }
                for row in rows[:top_k]
            ]
        }

    def delete(self, *, filter: dict[str, Any], namespace: str) -> None:
        _ = namespace
        ids = set(filter["memory_id"]["$in"])
        self.rows = {
            key: row for key, row in self.rows.items() if row["metadata"]["memory_id"] not in ids
        }

    def describe_index_stats(self) -> dict[str, Any]:
        return {"namespaces": {"default": {"vector_count": len(self.rows)}}}


class FakePineconeIndexes:
    def __init__(self, names: list[str]) -> None:
        self._names = names

    def names(self) -> list[str]:
        return self._names


class FakePineconeClient:
    def __init__(self) -> None:
        self.index = FakePineconeIndex()
        self.indexes = FakePineconeIndexes([])
        self.created: list[dict[str, Any]] = []

    def list_indexes(self) -> FakePineconeIndexes:
        return self.indexes

    def create_index(self, **kwargs: Any) -> None:
        self.created.append(dict(kwargs))
        self.indexes = FakePineconeIndexes([str(kwargs["name"])])

    def Index(self, name: str) -> FakePineconeIndex:
        _ = name
        return self.index


class FakeTurbopufferNamespace:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def write(self, **kwargs: Any) -> None:
        upsert_rows = kwargs.get("upsert_rows") or []
        for row in upsert_rows:
            self.rows[str(row["id"])] = dict(row)
        delete_filter = kwargs.get("delete_by_filter")
        if delete_filter:
            _field, _operator, values = delete_filter
            ids = {str(value) for value in values}
            self.rows = {
                row_id: row for row_id, row in self.rows.items() if row["memory_id"] not in ids
            }

    def query(self, **kwargs: Any) -> Any:
        _field, _operator, query_vector = kwargs["rank_by"]
        limit = int(kwargs["limit"])
        rows = sorted(
            self.rows.values(),
            key=lambda row: _test_cosine_distance(query_vector, row["vector"]),
        )
        return types.SimpleNamespace(
            rows=[
                types.SimpleNamespace(
                    **{key: value for key, value in row.items() if key != "vector"},
                    **{"$dist": _test_cosine_distance(query_vector, row["vector"])},
                )
                for row in rows[:limit]
            ]
        )

    def metadata(self) -> Any:
        return types.SimpleNamespace(approx_row_count=len(self.rows))


class FakeTurbopufferClient:
    def __init__(self) -> None:
        self.namespaces: dict[str, FakeTurbopufferNamespace] = {}

    def namespace(self, name: str) -> FakeTurbopufferNamespace:
        if name not in self.namespaces:
            self.namespaces[name] = FakeTurbopufferNamespace()
        return self.namespaces[name]


class FakeTypesenseNotFound(Exception):
    status_code = 404


class FakeTypesenseDocuments:
    def __init__(self, collection: "FakeTypesenseCollection") -> None:
        self.collection = collection

    def import_(self, documents: list[dict[str, Any]], options: dict[str, Any]) -> None:
        assert options["action"] == "upsert"
        for document in documents:
            self.collection.rows[str(document["id"])] = dict(document)

    def upsert(self, document: dict[str, Any]) -> None:
        self.collection.rows[str(document["id"])] = dict(document)

    def search(self, params: dict[str, Any]) -> dict[str, Any]:
        vector_query = str(params["vector_query"])
        query_text = vector_query.split("[", 1)[1].split("]", 1)[0]
        query_vector = [float(value) for value in query_text.split(",") if value]
        rows = sorted(
            self.collection.rows.values(),
            key=lambda row: _test_cosine_distance(query_vector, row["vector"]),
        )
        return {
            "hits": [
                {
                    "document": {key: value for key, value in row.items() if key != "vector"},
                    "vector_distance": _test_cosine_distance(query_vector, row["vector"]),
                }
                for row in rows[: int(params["per_page"])]
            ]
        }

    def delete(self, params: dict[str, Any]) -> None:
        filter_by = str(params["filter_by"])
        raw_ids = filter_by.split("[", 1)[1].split("]", 1)[0]
        ids = {item.strip().strip("`") for item in raw_ids.split(",") if item.strip()}
        self.collection.rows = {
            row_id: row
            for row_id, row in self.collection.rows.items()
            if row["memory_id"] not in ids
        }


class FakeTypesenseCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.rows: dict[str, dict[str, Any]] = {}
        self.documents = FakeTypesenseDocuments(self)

    def retrieve(self) -> dict[str, Any]:
        return {"name": self.name, "num_documents": len(self.rows)}


class FakeTypesenseCollections:
    def __init__(self) -> None:
        self.items: dict[str, FakeTypesenseCollection] = {}
        self.created: list[dict[str, Any]] = []

    def __getitem__(self, name: str) -> FakeTypesenseCollection:
        if name not in self.items:
            raise FakeTypesenseNotFound(name)
        return self.items[name]

    def create(self, schema: dict[str, Any]) -> FakeTypesenseCollection:
        self.created.append(dict(schema))
        collection = FakeTypesenseCollection(str(schema["name"]))
        self.items[collection.name] = collection
        return collection


class FakeTypesenseClient:
    def __init__(self) -> None:
        self.collections = FakeTypesenseCollections()


class FakeVespaResponse:
    def __init__(
        self,
        *,
        hits: list[dict[str, Any]] | None = None,
        total_count: int | None = None,
        successful: bool = True,
    ) -> None:
        self.hits = hits or []
        self.status_code = 200 if successful else 500
        self._successful = successful
        self._total_count = total_count if total_count is not None else len(self.hits)

    def is_successful(self) -> bool:
        return self._successful

    def get_json(self) -> dict[str, Any]:
        return {
            "root": {
                "fields": {"totalCount": self._total_count},
                "children": self.hits,
            }
        }


class FakeVespaClient:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.deletes: list[str] = []

    def feed_data_point(
        self,
        *,
        schema: str,
        namespace: str,
        data_id: str,
        fields: dict[str, Any],
    ) -> FakeVespaResponse:
        _ = (schema, namespace)
        self.rows[data_id] = dict(fields)
        return FakeVespaResponse()

    def query(self, *, body: dict[str, Any]) -> FakeVespaResponse:
        yql = str(body.get("yql", ""))
        if "nearestNeighbor" in yql:
            vector = list(body["input.query(q)"]["values"])
            rows = sorted(
                self.rows.values(),
                key=lambda row: _test_cosine_distance(vector, row["vector"]),
            )
            hits = [
                {
                    "id": f"id:memory:memory_vector::{row['id']}",
                    "relevance": 1.0 - _test_cosine_distance(vector, row["vector"]),
                    "fields": dict(row),
                }
                for row in rows[: int(body.get("hits", 5))]
            ]
            return FakeVespaResponse(hits=hits, total_count=len(rows))
        if "where true" in yql:
            return FakeVespaResponse(total_count=len(self.rows))
        memory_id = yql.split('contains "', 1)[1].split('"', 1)[0]
        hits = [
            {"id": f"id:memory:memory_vector::{row['id']}", "fields": {"id": row["id"]}}
            for row in self.rows.values()
            if row["memory_id"] == memory_id
        ]
        return FakeVespaResponse(hits=hits, total_count=len(hits))

    def delete_data(self, *, schema: str, namespace: str, data_id: str) -> FakeVespaResponse:
        _ = (schema, namespace)
        self.deletes.append(data_id)
        self.rows.pop(data_id, None)
        return FakeVespaResponse()


class FakeWeaviateMetadata:
    def __init__(self, *, distance: float) -> None:
        self.distance = distance
        self.certainty = 1.0 - distance


class FakeWeaviateObject:
    def __init__(self, *, properties: dict[str, Any], distance: float) -> None:
        self.properties = properties
        self.metadata = FakeWeaviateMetadata(distance=distance)


class FakeWeaviateData:
    def __init__(self, collection: "FakeWeaviateCollection") -> None:
        self.collection = collection

    def insert(self, *, uuid: str, properties: dict[str, Any], vector: list[float]) -> None:
        self.collection.rows[uuid] = {**properties, "vector": vector}

    def delete_many(self, *, where: dict[str, list[str]]) -> None:
        ids = set(where["memory_id"])
        self.collection.rows = {
            row_id: row
            for row_id, row in self.collection.rows.items()
            if row["memory_id"] not in ids
        }


class FakeWeaviateQuery:
    def __init__(self, collection: "FakeWeaviateCollection") -> None:
        self.collection = collection

    def near_vector(self, *, near_vector: list[float], limit: int, **_kwargs: Any) -> Any:
        rows = sorted(
            self.collection.rows.values(),
            key=lambda row: _test_cosine_distance(near_vector, row["vector"]),
        )
        return types.SimpleNamespace(
            objects=[
                FakeWeaviateObject(
                    properties=row,
                    distance=_test_cosine_distance(near_vector, row["vector"]),
                )
                for row in rows[:limit]
            ]
        )


class FakeWeaviateAggregate:
    def __init__(self, collection: "FakeWeaviateCollection") -> None:
        self.collection = collection

    def over_all(self, *, total_count: bool) -> Any:
        _ = total_count
        return types.SimpleNamespace(total_count=len(self.collection.rows))


class FakeWeaviateConfig:
    def __init__(self) -> None:
        self.properties = [
            types.SimpleNamespace(name=name)
            for name in [
                "memory_id",
                "project_id",
                "owner_id",
                "chunk_id",
                "chunk_index",
                "message_hash",
                "role",
                "text",
                "schema_version",
            ]
        ]
        self.vector_config = {
            "vector_index_config": {
                "dimensions": 3,
                "distance_metric": "cosine",
            }
        }

    def get(self) -> "FakeWeaviateConfig":
        return self


class FakeWeaviateCollection:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.data = FakeWeaviateData(self)
        self.query = FakeWeaviateQuery(self)
        self.aggregate = FakeWeaviateAggregate(self)
        self.config = FakeWeaviateConfig()


class FakeWeaviateCollections:
    def __init__(self) -> None:
        self.collection = FakeWeaviateCollection()
        self.exists_called = False

    def exists(self, name: str) -> bool:
        _ = name
        if self.exists_called:
            return True
        self.exists_called = True
        return False

    def create(self, *args: Any, **kwargs: Any) -> FakeWeaviateCollection:
        _ = (args, kwargs)
        return self.collection

    def get(self, name: str) -> FakeWeaviateCollection:
        _ = name
        return self.collection


class FakeWeaviateClient:
    def __init__(self) -> None:
        self.collections = FakeWeaviateCollections()


class FakeMongoCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.indexes: list[dict[str, Any]] = []
        self.search_indexes: list[dict[str, Any]] = [
            {
                "name": "memory_vector_index",
                "status": "READY",
                "definition": {
                    "fields": [
                        {
                            "type": "vector",
                            "path": "vector",
                            "numDimensions": 3,
                            "similarity": "cosine",
                        }
                    ]
                },
            }
        ]

    def create_index(self, *args: Any, **kwargs: Any) -> None:
        self.indexes.append({"args": args, "kwargs": dict(kwargs)})

    def replace_one(self, query: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> Any:
        for index, existing in enumerate(self.docs):
            if _matches_query(existing, query):
                self.docs[index] = dict(doc)
                return types.SimpleNamespace(modified_count=1)
        if upsert:
            self.docs.append(dict(doc))
        return types.SimpleNamespace(modified_count=0)

    def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs.append(dict(doc))

    def insert_many(self, docs: list[dict[str, Any]]) -> None:
        self.docs.extend(dict(doc) for doc in docs)

    def find_one(
        self, query: dict[str, Any], sort: list[tuple[str, int]] | None = None
    ) -> dict[str, Any] | None:
        rows = list(self.find(query))
        if sort:
            key, direction = sort[0]
            rows.sort(key=lambda row: str(row.get(key, "")), reverse=direction < 0)
        return rows[0] if rows else None

    def find(self, query: dict[str, Any] | None = None) -> Any:
        rows = [dict(doc) for doc in self.docs if _matches_query(doc, query or {})]
        return FakeMongoCursor(rows)

    def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> Any:
        return self.update_many(query, update, limit=1)

    def update_many(
        self, query: dict[str, Any], update: dict[str, Any], limit: int | None = None
    ) -> Any:
        modified = 0
        for doc in self.docs:
            if _matches_query(doc, query):
                _apply_update(doc, update)
                modified += 1
                if limit is not None and modified >= limit:
                    break
        return types.SimpleNamespace(modified_count=modified)

    def delete_many(self, query: dict[str, Any]) -> None:
        self.docs = [doc for doc in self.docs if not _matches_query(doc, query)]

    def count_documents(self, query: dict[str, Any]) -> int:
        return len([doc for doc in self.docs if _matches_query(doc, query)])

    def aggregate(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        vector_search = pipeline[0]["$vectorSearch"]
        query_vector = vector_search["queryVector"]
        limit = int(vector_search["limit"])
        rows = sorted(
            self.docs,
            key=lambda doc: _test_cosine_distance(query_vector, doc["vector"]),
        )
        output = []
        for doc in rows[:limit]:
            row = dict(doc)
            row["score"] = 1.0 - _test_cosine_distance(query_vector, doc["vector"])
            output.append(row)
        return output

    def list_search_indexes(self, *, name: str) -> list[dict[str, Any]]:
        return [index for index in self.search_indexes if index.get("name") == name]


class FakeMongoCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def sort(self, key: Any, direction: int | None = None) -> "FakeMongoCursor":
        if isinstance(key, list):
            key, direction = key[0]
        self.rows.sort(key=lambda row: str(row.get(str(key), "")), reverse=(direction or 1) < 0)
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeMongoDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeMongoCollection] = {}

    def __getitem__(self, name: str) -> FakeMongoCollection:
        if name not in self.collections:
            self.collections[name] = FakeMongoCollection()
        return self.collections[name]


class FakeMongoClient:
    def __init__(self) -> None:
        self.databases: dict[str, FakeMongoDatabase] = {}

    def __getitem__(self, name: str) -> FakeMongoDatabase:
        if name not in self.databases:
            self.databases[name] = FakeMongoDatabase()
        return self.databases[name]


def _matches_query(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in set(expected["$in"]):
                return False
        elif actual != expected:
            return False
    return True


def _apply_update(doc: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.get("$set", {}).items():
        doc[key] = value
    for key, value in update.get("$addToSet", {}).items():
        current = doc.setdefault(key, [])
        if isinstance(value, dict) and "$each" in value:
            for item in value["$each"]:
                if item not in current:
                    current.append(item)
        elif value not in current:
            current.append(value)


@pytest.mark.parametrize(
    ("provider", "factory"),
    [
        ("memory", lambda tmp_path: InMemoryVectorStore(dimension=3)),
        ("lancedb", lambda tmp_path: LanceDBVectorStore(tmp_path / "lancedb", dimension=3)),
        (
            "chromadb",
            lambda tmp_path: ChromaDBVectorStore(client=FakeChromaClient(), dimension=3),
        ),
        (
            "qdrant",
            lambda tmp_path: QdrantVectorStore(
                client=FakeQdrantClient(), models=FakeQdrantModels, dimension=3
            ),
        ),
        (
            "milvus",
            lambda tmp_path: MilvusVectorStore(client=FakeMilvusClient(), dimension=3),
        ),
        (
            "weaviate",
            lambda tmp_path: WeaviateVectorStore(client=FakeWeaviateClient(), dimension=3),
        ),
        (
            "mongodb_atlas",
            lambda tmp_path: MongoDBAtlasVectorStore(
                uri="mongodb://example",
                client=FakeMongoClient(),
                dimension=3,
            ),
        ),
        (
            "elasticsearch",
            lambda tmp_path: ElasticsearchVectorStore(client=FakeSearchClient(), dimension=3),
        ),
        (
            "opensearch",
            lambda tmp_path: OpenSearchVectorStore(
                client=FakeSearchClient(opensearch=True), dimension=3
            ),
        ),
        (
            "redis",
            lambda tmp_path: RedisVectorStore(client=FakeRedisClient(), dimension=3),
        ),
        (
            "pinecone",
            lambda tmp_path: PineconeVectorStore(client=FakePineconeClient(), dimension=3),
        ),
        (
            "turbopuffer",
            lambda tmp_path: TurbopufferVectorStore(client=FakeTurbopufferClient(), dimension=3),
        ),
        (
            "vespa",
            lambda tmp_path: VespaVectorStore(client=FakeVespaClient(), dimension=3),
        ),
        (
            "typesense",
            lambda tmp_path: TypesenseVectorStore(client=FakeTypesenseClient(), dimension=3),
        ),
        (
            "fake-sdk",
            lambda tmp_path: FakeSDKVectorStore(client=FakeVectorClient(), dimension=3),
        ),
    ],
)
def test_vector_store_contract_for_local_backends(
    provider: str, factory: VectorStoreFactory, tmp_path: Path
) -> None:
    _ = provider
    store = factory(tmp_path)
    _assert_vector_store_contract(store)
    with pytest.raises(NotSupportedError, match="TTL is not supported by this vector adapter"):
        store.set_ttl(str(uuid4()), 60)


def test_milvus_uses_string_primary_key_for_stable_point_ids() -> None:
    client = FakeMilvusClient()

    store = MilvusVectorStore(client=client, dimension=3)

    assert client.collections["memory_vectors"]["id_type"] == "string"
    assert client.collections["memory_vectors"]["max_length"] == 64
    store.insert(
        "memory-a",
        [
            {
                "chunk_id": "memory-a:0",
                "chunk_index": 0,
                "message_hash": "sha256:" + ("a" * 64),
                "role": "user",
                "text": "alpha memory",
                "vector": [1.0, 0.0, 0.0],
            }
        ],
    )
    store.delete(["memory-a"])
    assert client.flushes == ["memory_vectors", "memory_vectors"]
    assert client.indexes[0]["field_name"] == "vector"
    assert client.indexes[0]["index_params"]["metric_type"] == "COSINE"
    assert client.loaded == ["memory_vectors"]


def test_milvus_existing_collection_dimension_mismatch_fails_startup() -> None:
    client = FakeMilvusClient()
    client.create_collection(
        collection_name="memory_vectors",
        dimension=99,
        metric_type="COSINE",
        auto_id=False,
        id_type="string",
        max_length=64,
    )

    with pytest.raises(VectorDimensionError, match="Milvus collection dimensionality mismatch"):
        MilvusVectorStore(client=client, dimension=3)


def test_milvus_existing_collection_metric_mismatch_fails_startup() -> None:
    client = FakeMilvusClient()
    client.create_collection(
        collection_name="memory_vectors",
        dimension=3,
        metric_type="L2",
        auto_id=False,
        id_type="string",
        max_length=64,
    )

    with pytest.raises(RuntimeError, match="Milvus collection metric mismatch"):
        MilvusVectorStore(client=client, dimension=3, distance="cosine")


def test_milvus_collection_not_loaded_fails_startup() -> None:
    client = FakeMilvusClient()
    client.load_state = "NotLoad"

    with pytest.raises(RuntimeError, match="is not loaded"):
        MilvusVectorStore(client=client, dimension=3)


def test_mongodb_atlas_search_index_must_exist_and_match_dimension() -> None:
    missing_client = FakeMongoClient()
    missing_client["ai_memory_hub"]["memory_vectors"].search_indexes = []
    with pytest.raises(RuntimeError, match="vector search index .* was not found"):
        MongoDBAtlasVectorStore(uri="mongodb://example", client=missing_client, dimension=3)

    building_client = FakeMongoClient()
    building_client["ai_memory_hub"]["memory_vectors"].search_indexes[0]["status"] = "BUILDING"
    with pytest.raises(RuntimeError, match="is not ready"):
        MongoDBAtlasVectorStore(uri="mongodb://example", client=building_client, dimension=3)

    mismatch_client = FakeMongoClient()
    fields = mismatch_client["ai_memory_hub"]["memory_vectors"].search_indexes[0]["definition"][
        "fields"
    ]
    fields[0]["numDimensions"] = 99
    with pytest.raises(
        VectorDimensionError,
        match="MongoDB Atlas vector search index dimensionality mismatch",
    ):
        MongoDBAtlasVectorStore(uri="mongodb://example", client=mismatch_client, dimension=3)


def test_weaviate_existing_collection_schema_mismatch_fails_startup() -> None:
    client = FakeWeaviateClient()
    client.collections.exists_called = True
    client.collections.collection.config.properties = [
        prop
        for prop in client.collections.collection.config.properties
        if prop.name != "message_hash"
    ]

    with pytest.raises(RuntimeError, match="missing required properties: message_hash"):
        WeaviateVectorStore(client=client, dimension=3)


def test_weaviate_existing_collection_dimension_mismatch_fails_startup() -> None:
    client = FakeWeaviateClient()
    client.collections.exists_called = True
    client.collections.collection.config.vector_config["vector_index_config"]["dimensions"] = 99

    with pytest.raises(VectorDimensionError, match="Weaviate collection dimensionality mismatch"):
        WeaviateVectorStore(client=client, dimension=3)


def test_weaviate_existing_collection_distance_mismatch_fails_startup() -> None:
    client = FakeWeaviateClient()
    client.collections.exists_called = True
    client.collections.collection.config.vector_config["vector_index_config"]["distance_metric"] = (
        "dot"
    )

    with pytest.raises(RuntimeError, match="Weaviate collection distance mismatch"):
        WeaviateVectorStore(client=client, dimension=3)


def test_elasticsearch_create_index_when_exists_probe_returns_bad_request() -> None:
    client = FakeSearchClient(fail_exists_status=400)

    ElasticsearchVectorStore(client=client, dimension=3)

    assert "memory_vectors" in client.indices.created


def test_elasticsearch_reraises_unexpected_exists_probe_errors() -> None:
    client = FakeSearchClient(fail_exists_status=503)

    with pytest.raises(FakeSearchStatusError, match="search status 503"):
        ElasticsearchVectorStore(client=client, dimension=3)


def test_elasticsearch_existing_mapping_mismatch_fails_startup() -> None:
    client = FakeSearchClient()
    ElasticsearchVectorStore(client=client, dimension=3)
    client.indices.created["memory_vectors"]["mappings"]["properties"]["vector"]["dims"] = 99

    with pytest.raises(
        VectorDimensionError,
        match="Elasticsearch vector mapping dimensionality mismatch",
    ):
        ElasticsearchVectorStore(client=client, dimension=3)


def test_opensearch_existing_mapping_mismatch_fails_startup() -> None:
    client = FakeSearchClient(opensearch=True)
    OpenSearchVectorStore(client=client, dimension=3)
    mapping = client.indices.created["memory_vectors"]["body"]["mappings"]
    mapping["properties"]["vector"]["method"]["space_type"] = "l2"

    with pytest.raises(RuntimeError, match="OpenSearch vector mapping distance mismatch"):
        OpenSearchVectorStore(client=client, dimension=3)


def test_search_mapping_metadata_mismatch_fails_startup() -> None:
    client = FakeSearchClient()
    ElasticsearchVectorStore(client=client, dimension=3)
    client.indices.created["memory_vectors"]["mappings"]["_meta"]["schema_version"] = 99

    with pytest.raises(RuntimeError, match="Elasticsearch mapping schema version mismatch"):
        ElasticsearchVectorStore(client=client, dimension=3)


@pytest.mark.skipif(
    importlib.util.find_spec("chromadb") is None,
    reason="chromadb is not installed",
)
def test_chromadb_persistent_local_vector_contract(tmp_path: Path) -> None:
    _assert_vector_store_contract(
        ChromaDBVectorStore(
            path=tmp_path / "chromadb",
            collection_name=f"memory_vectors_{uuid4().hex}",
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_CHROMADB_URL"),
    reason="AMH_TEST_CHROMADB_URL is not set",
)
def test_live_chromadb_http_vector_contract() -> None:
    _assert_vector_store_contract(
        ChromaDBVectorStore(
            url=str(os.environ["AMH_TEST_CHROMADB_URL"]),
            collection_name=f"memory_vectors_{uuid4().hex}",
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_QDRANT_URL"),
    reason="AMH_TEST_QDRANT_URL is not set",
)
def test_live_qdrant_vector_contract() -> None:
    _assert_vector_store_contract(
        QdrantVectorStore(
            url=str(os.environ["AMH_TEST_QDRANT_URL"]),
            api_key=str(os.getenv("AMH_TEST_QDRANT_API_KEY", "")),
            collection_name=f"memory_vectors_{uuid4().hex}",
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_MONGODB_URI"),
    reason="AMH_TEST_MONGODB_URI is not set",
)
def test_live_mongodb_metadata_contract() -> None:
    _assert_metadata_store_contract(
        MongoDBMetadataStore(
            uri=str(os.environ["AMH_TEST_MONGODB_URI"]),
            database=f"ai_memory_hub_test_{uuid4().hex}",
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_MONGODB_ATLAS_URI"),
    reason="AMH_TEST_MONGODB_ATLAS_URI is not set",
)
def test_live_mongodb_atlas_vector_contract() -> None:
    _assert_vector_store_contract(
        MongoDBAtlasVectorStore(
            uri=str(os.environ["AMH_TEST_MONGODB_ATLAS_URI"]),
            database=str(os.getenv("AMH_TEST_MONGODB_ATLAS_DATABASE", "ai_memory_hub")),
            collection_name=str(os.getenv("AMH_TEST_MONGODB_ATLAS_COLLECTION", "memory_vectors")),
            index_name=str(os.getenv("AMH_TEST_MONGODB_ATLAS_INDEX", "memory_vector_index")),
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_MILVUS_URI"),
    reason="AMH_TEST_MILVUS_URI is not set",
)
def test_live_milvus_vector_contract() -> None:
    _assert_vector_store_contract(
        MilvusVectorStore(
            uri=str(os.environ["AMH_TEST_MILVUS_URI"]),
            token=str(os.getenv("AMH_TEST_MILVUS_TOKEN", "")),
            collection_name=f"memory_vectors_{uuid4().hex}",
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_WEAVIATE_URL"),
    reason="AMH_TEST_WEAVIATE_URL is not set",
)
def test_live_weaviate_vector_contract() -> None:
    _assert_vector_store_contract(
        WeaviateVectorStore(
            url=str(os.environ["AMH_TEST_WEAVIATE_URL"]),
            api_key=str(os.getenv("AMH_TEST_WEAVIATE_API_KEY", "")),
            collection_name=f"MemoryVector{uuid4().hex}",
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_ELASTICSEARCH_URL"),
    reason="AMH_TEST_ELASTICSEARCH_URL is not set",
)
def test_live_elasticsearch_vector_contract() -> None:
    _assert_vector_store_contract(
        ElasticsearchVectorStore(
            url=str(os.environ["AMH_TEST_ELASTICSEARCH_URL"]),
            username=str(os.getenv("AMH_TEST_ELASTICSEARCH_USERNAME", "")),
            password=str(os.getenv("AMH_TEST_ELASTICSEARCH_PASSWORD", "")),
            index_name=f"memory-vectors-{uuid4().hex}",
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_OPENSEARCH_URL"),
    reason="AMH_TEST_OPENSEARCH_URL is not set",
)
def test_live_opensearch_vector_contract() -> None:
    _assert_vector_store_contract(
        OpenSearchVectorStore(
            url=str(os.environ["AMH_TEST_OPENSEARCH_URL"]),
            username=str(os.getenv("AMH_TEST_OPENSEARCH_USERNAME", "")),
            password=str(os.getenv("AMH_TEST_OPENSEARCH_PASSWORD", "")),
            index_name=f"memory-vectors-{uuid4().hex}",
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_REDIS_URL"),
    reason="AMH_TEST_REDIS_URL is not set",
)
def test_live_redis_vector_contract() -> None:
    _assert_vector_store_contract(
        RedisVectorStore(
            url=str(os.environ["AMH_TEST_REDIS_URL"]),
            index_name=f"memory_vectors_{uuid4().hex}",
            key_prefix=f"memory_vectors:{uuid4().hex}:",
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_PINECONE_API_KEY"),
    reason="AMH_TEST_PINECONE_API_KEY is not set",
)
def test_live_pinecone_vector_contract() -> None:
    _assert_vector_store_contract(
        PineconeVectorStore(
            api_key=str(os.environ["AMH_TEST_PINECONE_API_KEY"]),
            index_name=str(os.getenv("AMH_TEST_PINECONE_INDEX", "ai-memory-hub-test")),
            namespace=f"test-{uuid4().hex}",
            dimension=3,
            create_index=os.getenv("AMH_TEST_PINECONE_CREATE_INDEX") == "1",
            cloud=str(os.getenv("AMH_TEST_PINECONE_CLOUD", "aws")),
            region=str(os.getenv("AMH_TEST_PINECONE_REGION", "us-east-1")),
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_TURBOPUFFER_API_KEY"),
    reason="AMH_TEST_TURBOPUFFER_API_KEY is not set",
)
def test_live_turbopuffer_vector_contract() -> None:
    _assert_vector_store_contract(
        TurbopufferVectorStore(
            api_key=str(os.environ["AMH_TEST_TURBOPUFFER_API_KEY"]),
            namespace=str(
                os.getenv(
                    "AMH_TEST_TURBOPUFFER_NAMESPACE",
                    f"ai-memory-hub-test-{uuid4().hex}",
                )
            ),
            region=str(os.getenv("AMH_TEST_TURBOPUFFER_REGION", "gcp-us-central1")),
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_VESPA_URL"),
    reason="AMH_TEST_VESPA_URL is not set",
)
def test_live_vespa_vector_contract() -> None:
    _assert_vector_store_contract(
        VespaVectorStore(
            url=str(os.environ["AMH_TEST_VESPA_URL"]),
            token=str(os.getenv("AMH_TEST_VESPA_TOKEN", "")),
            namespace=str(os.getenv("AMH_TEST_VESPA_NAMESPACE", "memory")),
            schema=str(os.getenv("AMH_TEST_VESPA_SCHEMA", "memory_vector")),
            rank_profile=str(os.getenv("AMH_TEST_VESPA_RANK_PROFILE", "vector_similarity")),
            dimension=3,
        )
    )


@pytest.mark.skipif(
    not os.getenv("AMH_TEST_TYPESENSE_URL"),
    reason="AMH_TEST_TYPESENSE_URL is not set",
)
def test_live_typesense_vector_contract() -> None:
    _assert_vector_store_contract(
        TypesenseVectorStore(
            url=str(os.environ["AMH_TEST_TYPESENSE_URL"]),
            api_key=str(os.getenv("AMH_TEST_TYPESENSE_API_KEY", "")),
            collection_name=f"memory_vectors_{uuid4().hex}",
            dimension=3,
        )
    )


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
    with pytest.raises(VectorDimensionError, match="dimensionality mismatch at startup"):
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

    assert runtime.vector_store.expected_dimensionality == runtime.embedding_provider.dimension


def test_build_runtime_records_embedding_index_metadata_for_persistent_vectors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class EmptyLanceDB:
        expected_dimensionality = 32

        def __init__(self, db_path: Path, table_name: str = "memory_vectors", dimension: int = 32):
            self.db_path = db_path
            self.table_name = table_name
            self.expected_dimensionality = dimension

        def health(self) -> dict[str, Any]:
            return {
                "provider": "lancedb",
                "expected_dimensionality": self.expected_dimensionality,
                "stats": {"rows": 0},
            }

        def get_stats(self) -> dict[str, Any]:
            return {"rows": 0, "expected_dimensionality": self.expected_dimensionality}

    monkeypatch.setattr(mvp_ingestion, "LanceDBVectorStore", EmptyLanceDB)
    runtime = mvp_ingestion.build_runtime(
        {
            "providers": {"embeddings": "local", "vector_db": "lancedb"},
            "paths": {"data_dir": str(tmp_path)},
        }
    )

    records = list(
        runtime.metadata_store._connect().execute(
            "SELECT value FROM runtime_metadata WHERE key LIKE 'vector_index_compatibility:%'"
        )
    )
    assert len(records) == 1
    metadata = json.loads(records[0]["value"])
    assert metadata["embedding"] == {
        "provider": "local",
        "model": "local-deterministic-hash",
        "dimension": 32,
        "options": {},
    }
    assert runtime.health_state["embedding"] == metadata["embedding"]
    assert runtime.health_state["embedding_index_mismatch"] is False


def test_build_runtime_fails_on_same_dimension_embedding_model_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class PopulatedLanceDB:
        expected_dimensionality = 32

        def __init__(self, db_path: Path, table_name: str = "memory_vectors", dimension: int = 32):
            self.db_path = db_path
            self.table_name = table_name
            self.expected_dimensionality = dimension

        def health(self) -> dict[str, Any]:
            return {
                "provider": "lancedb",
                "expected_dimensionality": self.expected_dimensionality,
                "stats": {"rows": 3},
            }

        def get_stats(self) -> dict[str, Any]:
            return {"rows": 3, "expected_dimensionality": self.expected_dimensionality}

    vector_store = PopulatedLanceDB(tmp_path / "lancedb")
    vector_index_id = mvp_ingestion._vector_index_id(
        provider="lancedb",
        vector_store=vector_store,
        vector_health=vector_store.health(),
    )
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    store.set_runtime_metadata(
        f"vector_index_compatibility:{vector_index_id}",
        {
            "embedding": {
                "provider": "openai",
                "model": "old-same-dimension-model",
                "dimension": 32,
                "options": {"base_url": "http://localhost:11434/v1"},
            },
            "vector_index": {
                "provider": "lancedb",
                "id": vector_index_id,
                "distance": "cosine",
            },
        },
    )

    monkeypatch.setattr(mvp_ingestion, "LanceDBVectorStore", PopulatedLanceDB)
    with pytest.raises(RuntimeError, match="Embedding index metadata mismatch"):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "lancedb"},
                "paths": {"data_dir": str(tmp_path)},
            }
        )


def test_log_redaction_for_dsn_and_keyvalue_secrets() -> None:
    mongodb_password = "fixture-mongodb-password"
    mongodb_uri = "mongodb+srv://user:" + mongodb_password + "@example.mongodb.net/app"
    message = (
        "connect failed postgres://user:supersecret@db.local/app "
        f"{mongodb_uri} "
        "http://elastic:elastic-secret@search.local:9200 "
        "password=hunter2 token=abc123 api_key=xyz api-key=qdrant-secret"
    )
    redacted = redact_secrets(message)
    assert "supersecret" not in redacted
    assert mongodb_password not in redacted
    assert "elastic-secret" not in redacted
    assert "hunter2" not in redacted
    assert "abc123" not in redacted
    assert "xyz" not in redacted
    assert "qdrant-secret" not in redacted
    assert "***" in redacted


def test_log_redaction_for_bearer_headers_and_token_query_strings() -> None:
    message = (
        "Authorization: Bearer header-secret "
        "GET /memory/search?access_token=query-secret&token=second-secret&q=hello"
    )

    redacted = redact_secrets(message)

    assert "header-secret" not in redacted
    assert "query-secret" not in redacted
    assert "second-secret" not in redacted
    assert "Authorization: Bearer ***" in redacted
    assert "access_token=***" in redacted
    assert "token=***" in redacted


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
            },
            "paths": {"data_dir": str(tmp_path)},
            "storage": {
                "vector": {"allow_fallback": True},
                "vector_providers": {"pgvector": {"url": "postgres://example"}},
            },
        }
    )

    assert runtime.health_state["mode"] == "degraded"
    assert runtime.health_state["vector_provider"] == "memory"
    assert runtime.health_state["requested_vector_provider"] == "pgvector"
    assert runtime.health_state["vector_fallback_active"] is True
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
                },
                "paths": {"data_dir": str(tmp_path)},
                "storage": {
                    "vector": {"allow_fallback": False},
                    "vector_providers": {"pgvector": {"url": "postgres://example"}},
                },
            }
        )


def test_build_runtime_chromadb_fallback_uses_memory_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenChromaDB:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs
            raise RuntimeError("chroma failed api_key=chroma-secret")

    monkeypatch.setattr(mvp_ingestion, "ChromaDBVectorStore", BrokenChromaDB)
    runtime = mvp_ingestion.build_runtime(
        {
            "providers": {"embeddings": "local", "vector_db": "chromadb"},
            "paths": {"data_dir": str(tmp_path)},
            "storage": {"vector": {"allow_fallback": True}},
        }
    )

    assert runtime.health_state["mode"] == "degraded"
    assert runtime.health_state["vector_provider"] == "memory"
    assert runtime.health_state["requested_vector_provider"] == "chromadb"
    assert runtime.vector_store.expected_dimensionality == runtime.embedding_provider.dimension
    assert "chroma-secret" not in " ".join(runtime.health_state["reasons"])


def test_build_runtime_chromadb_fallback_disabled_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenChromaDB:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs
            raise RuntimeError("chromadb unavailable")

    monkeypatch.setattr(mvp_ingestion, "ChromaDBVectorStore", BrokenChromaDB)

    with pytest.raises(RuntimeError, match="chromadb unavailable"):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "chromadb"},
                "paths": {"data_dir": str(tmp_path)},
                "storage": {"vector": {"allow_fallback": False}},
            }
        )


@pytest.mark.parametrize(
    ("provider", "class_name", "secret_message"),
    [
        ("qdrant", "QdrantVectorStore", "qdrant unavailable api_key=qdrant-secret"),
        ("milvus", "MilvusVectorStore", "milvus unavailable token=milvus-secret"),
        ("weaviate", "WeaviateVectorStore", "weaviate unavailable api_key=weaviate-secret"),
        (
            "mongodb_atlas",
            "MongoDBAtlasVectorStore",
            "mongodb unavailable " + "mongodb://u:" + "fixture-atlas-password" + "@example/app",
        ),
        (
            "elasticsearch",
            "ElasticsearchVectorStore",
            "elastic unavailable username=elastic password=elastic-secret",
        ),
        (
            "opensearch",
            "OpenSearchVectorStore",
            "opensearch unavailable username=admin password=opensearch-secret",
        ),
        (
            "redis",
            "RedisVectorStore",
            "redis unavailable redis://:redis-secret@localhost:6379/0",
        ),
        (
            "pinecone",
            "PineconeVectorStore",
            "pinecone unavailable api_key=pinecone-secret",
        ),
        (
            "turbopuffer",
            "TurbopufferVectorStore",
            "turbopuffer unavailable api_key=turbopuffer-secret",
        ),
        (
            "vespa",
            "VespaVectorStore",
            "vespa unavailable token=vespa-secret",
        ),
        (
            "typesense",
            "TypesenseVectorStore",
            "typesense unavailable api_key=typesense-secret",
        ),
    ],
)
def test_build_runtime_expansion_vector_fallback_uses_memory_provider(
    provider: str,
    class_name: str,
    secret_message: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class BrokenVectorStore:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs
            raise RuntimeError(secret_message)

    monkeypatch.setattr(mvp_ingestion, class_name, BrokenVectorStore)
    atlas_password = "fixture-atlas-password"
    atlas_uri = "mongodb://u:" + atlas_password + "@example/app"
    runtime = mvp_ingestion.build_runtime(
        {
            "providers": {"embeddings": "local", "vector_db": provider},
            "paths": {"data_dir": str(tmp_path)},
            "storage": {
                "vector": {"allow_fallback": True},
                "vector_providers": {
                    "milvus": {"token": "milvus-secret"},
                    "weaviate": {"api_key": "weaviate-secret"},
                    "mongodb_atlas": {"uri": atlas_uri},
                    "elasticsearch": {
                        "username": "elastic",
                        "password": "elastic-secret",
                    },
                    "opensearch": {
                        "username": "admin",
                        "password": "opensearch-secret",
                    },
                    "redis": {"url": "redis://:redis-secret@localhost:6379/0"},
                    "pinecone": {"api_key": "pinecone-secret"},
                    "turbopuffer": {"api_key": "turbopuffer-secret"},
                    "vespa": {"token": "vespa-secret"},
                    "typesense": {"api_key": "typesense-secret"},
                },
            },
        }
    )

    assert runtime.health_state["mode"] == "degraded"
    assert runtime.health_state["vector_provider"] == "memory"
    assert runtime.health_state["requested_vector_provider"] == provider
    assert runtime.health_state["vector_fallback_active"] is True
    assert runtime.vector_store.expected_dimensionality == runtime.embedding_provider.dimension
    assert "qdrant-secret" not in " ".join(runtime.health_state["reasons"])
    assert "milvus-secret" not in " ".join(runtime.health_state["reasons"])
    assert "weaviate-secret" not in " ".join(runtime.health_state["reasons"])
    assert atlas_password not in " ".join(runtime.health_state["reasons"])
    assert "elastic-secret" not in " ".join(runtime.health_state["reasons"])
    assert "opensearch-secret" not in " ".join(runtime.health_state["reasons"])
    assert "redis-secret" not in " ".join(runtime.health_state["reasons"])
    assert "pinecone-secret" not in " ".join(runtime.health_state["reasons"])
    assert "turbopuffer-secret" not in " ".join(runtime.health_state["reasons"])
    assert "vespa-secret" not in " ".join(runtime.health_state["reasons"])
    assert "typesense-secret" not in " ".join(runtime.health_state["reasons"])


@pytest.mark.parametrize(
    ("provider", "class_name"),
    [
        ("qdrant", "QdrantVectorStore"),
        ("milvus", "MilvusVectorStore"),
        ("weaviate", "WeaviateVectorStore"),
        ("mongodb_atlas", "MongoDBAtlasVectorStore"),
        ("elasticsearch", "ElasticsearchVectorStore"),
        ("opensearch", "OpenSearchVectorStore"),
        ("redis", "RedisVectorStore"),
        ("pinecone", "PineconeVectorStore"),
        ("turbopuffer", "TurbopufferVectorStore"),
        ("vespa", "VespaVectorStore"),
        ("typesense", "TypesenseVectorStore"),
    ],
)
def test_build_runtime_expansion_vector_fallback_disabled_raises(
    provider: str,
    class_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class BrokenVectorStore:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs
            raise RuntimeError(f"{provider} unavailable")

    monkeypatch.setattr(mvp_ingestion, class_name, BrokenVectorStore)

    with pytest.raises(RuntimeError, match=f"{provider} unavailable"):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": provider},
                "paths": {"data_dir": str(tmp_path)},
                "storage": {
                    "vector": {"allow_fallback": False},
                    "vector_providers": {"mongodb_atlas": {"uri": "mongodb://example/app"}},
                },
            }
        )


def test_vector_fallback_disabled_startup_error_redacts_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenRedisVectorStore:
        def __init__(self, **kwargs: Any) -> None:
            _ = kwargs
            raise RuntimeError("redis unavailable redis://:redis-secret@localhost:6379/0")

    monkeypatch.setattr(mvp_ingestion, "RedisVectorStore", BrokenRedisVectorStore)

    with pytest.raises(RuntimeError) as exc_info:
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "redis"},
                "paths": {"data_dir": str(tmp_path)},
                "storage": {
                    "vector": {"allow_fallback": False},
                    "vector_providers": {
                        "redis": {"url": "redis://:redis-secret@localhost:6379/0"}
                    },
                },
            }
        )

    message = str(exc_info.value)
    assert "redis unavailable" in message
    assert "redis-secret" not in message
    assert "redis://:***@localhost:6379/0" in message


def test_dry_run_write_is_noop(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="memory.backend.dry_run"):
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
        inserted_id, inserted = runtime.metadata_store.insert_new(conversation)
        runtime.metadata_store.insert_facts(
            [{"id": "fact-1", "subject": "user", "predicate": "prefers", "object": "tea"}]
        )
        runtime.metadata_store.supersede_fact("fact-1", "fact-2")
        runtime.metadata_store.set_runtime_metadata("dry-run-key", {"value": "secret"})
        runtime.vector_store.insert(
            memory_id,
            [{"chunk_index": 0, "role": "user", "text": "hello", "vector": [0.1] * 32}],
        )

    assert memory_id == conversation["id"]
    assert (inserted_id, inserted) == (conversation["id"], True)
    assert runtime.metadata_store.get(memory_id) is None
    assert runtime.metadata_store.get_runtime_metadata("dry-run-key") is None
    records = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "dry_run_write_skipped"
    ]
    assert [record.operation for record in records] == [
        "metadata_insert",
        "metadata_insert_new",
        "metadata_insert_facts",
        "metadata_supersede_fact",
        "metadata_set_runtime_metadata",
        "vector_insert",
    ]
    assert records[2].fact_count == 1
    assert records[-1].chunk_count == 1
    assert "secret" not in caplog.text


def test_unsupported_operations_raise_deterministic_errors(tmp_path: Path) -> None:
    metadata = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    mongodb_metadata = MongoDBMetadataStore(uri="mongodb://example", client=FakeMongoClient())
    vectors = InMemoryVectorStore(dimension=3)

    with pytest.raises(
        NotSupportedError,
        match="Metadata transactions are not exposed by this adapter API",
    ):
        metadata.begin_transaction()
    with pytest.raises(NotSupportedError, match="TTL is not supported by this metadata adapter"):
        metadata.set_ttl("id-1", 60)
    with pytest.raises(
        NotSupportedError,
        match="Metadata transactions are not exposed by this adapter API",
    ):
        mongodb_metadata.begin_transaction()
    with pytest.raises(NotSupportedError, match="TTL is not supported by this metadata adapter"):
        mongodb_metadata.set_ttl("id-1", 60)
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


def test_sqlite_conversation_hash_dedupes_within_project_only(tmp_path: Path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    store.create_project(project_id="project-a", owner_id="owner-a", name="Project A")
    store.create_project(project_id="project-b", owner_id="owner-a", name="Project B")
    first = {
        "id": "11111111-1111-4111-8111-111111111111",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "first", "hash": "sha256:" + ("1" * 64)}],
        "metadata": {
            "project_id": "project-a",
            "imported_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "conversation_hash": "sha256:" + ("a" * 64),
        },
    }
    same_hash_other_project = {
        **first,
        "id": "22222222-2222-4222-8222-222222222222",
        "messages": [{"role": "user", "text": "second", "hash": "sha256:" + ("2" * 64)}],
        "metadata": {**first["metadata"], "project_id": "project-b"},
    }
    same_hash_same_project = {
        **first,
        "id": "33333333-3333-4333-8333-333333333333",
        "messages": [{"role": "user", "text": "third", "hash": "sha256:" + ("3" * 64)}],
    }

    store.insert(first)
    store.insert(same_hash_other_project)
    with pytest.raises(sqlite3.IntegrityError):
        store.insert(same_hash_same_project)

    assert store.get(first["id"])["metadata"]["project_id"] == "project-a"
    assert store.get(same_hash_other_project["id"])["metadata"]["project_id"] == "project-b"


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
    assert health["requested_vector_provider"] == "lancedb"
    assert health["vector_health"]["provider"] == "memory"
    assert health["vector_health"]["stats"]["provider"] == "memory"


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


def test_embedding_readiness_does_not_probe_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ExplodingEmbeddingProvider:
        dimension = 32

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            raise AssertionError("embedding readiness should not call embed_texts")

    monkeypatch.setattr(mvp_ingestion, "LocalEmbeddingProvider", ExplodingEmbeddingProvider)
    mvp_ingestion.configure_runtime(
        config={
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
            "paths": {"data_dir": str(tmp_path)},
        }
    )

    health = mvp_ingestion.runtime_health()

    assert health["embedding_health"] == {
        "provider": "local",
        "model": "local-deterministic-hash",
        "dimension": 32,
        "status": "ok",
        "live_probe": False,
        "mode": "configuration",
    }


def test_embedding_readiness_live_probe_is_explicit_and_redacted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenEmbeddingProvider:
        dimension = 32

        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedding failed api_key=secret-readiness-key")

    monkeypatch.setattr(mvp_ingestion, "LocalEmbeddingProvider", BrokenEmbeddingProvider)
    mvp_ingestion.configure_runtime(
        config={
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
            "paths": {"data_dir": str(tmp_path)},
            "observability": {"embedding_readiness_probe": True},
        }
    )

    health = mvp_ingestion.runtime_health()

    assert health["embedding_health"]["status"] == "degraded"
    assert health["embedding_health"]["live_probe"] is True
    assert health["embedding_health"]["error_type"] == "RuntimeError"
    assert "secret-readiness-key" not in health["embedding_health"]["error"]


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


def test_postgres_metadata_init_failure_fails_startup_without_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenPostgresMetadataStore:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("postgres metadata unavailable postgres://user:pg-secret@db/app")

    monkeypatch.setattr(
        mvp_ingestion,
        "PostgresMetadataStore",
        BrokenPostgresMetadataStore,
    )

    with pytest.raises(RuntimeError) as exc_info:
        mvp_ingestion.build_runtime(
            {
                "providers": {
                    "embeddings": "local",
                    "metadata_db": "postgres",
                    "vector_db": "in_memory",
                },
                "paths": {"data_dir": str(tmp_path)},
                "storage": {
                    "metadata_providers": {"postgres": {"url": "postgres://user:pg-secret@db/app"}},
                    "vector": {"allow_fallback": True},
                },
            }
        )

    message = str(exc_info.value)
    assert "postgres metadata unavailable" in message
    assert "pg-secret" not in message
    assert "postgres://user:***@db/app" in message


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
            self.state["rows"] = [row for row in self.state["rows"] if row[0] != memory_id]
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


def test_postgres_fact_row_mapping_includes_freshness_and_source_quality() -> None:
    store = PostgresMetadataStore.__new__(PostgresMetadataStore)
    fact = {
        "id": "fact-1",
        "subject": "user",
        "predicate": "favorite_holiday",
        "object": "aniversary",
        "qualifiers": {"source_role": "user"},
        "confidence": "high",
        "source_conversation_id": "11111111-1111-4111-8111-111111111111",
        "source_message_indexes": [0],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "last_confirmed_at": "2026-01-02T00:00:00Z",
        "superseded_by": "fact-2",
        "superseded_at": "2026-01-03T00:00:00Z",
        "deleted_at": None,
        "source_quality": "direct_user_statement",
        "confidence_reason": "Extracted from a direct user statement.",
        "object_raw": "aniversary",
        "object_normalized": "anniversary",
    }

    row = store._fact_row(fact)
    loaded = store._fact_from_row(row)

    assert loaded["source_quality"] == "direct_user_statement"
    assert loaded["confidence_reason"] == "Extracted from a direct user statement."
    assert loaded["last_confirmed_at"] == "2026-01-02T00:00:00Z"
    assert loaded["superseded_at"] == "2026-01-03T00:00:00Z"
    assert loaded["project_id"] is None
    assert loaded["object_raw"] == "aniversary"
    assert loaded["object_normalized"] == "anniversary"


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
    with pytest.raises(SchemaVersionError, match="Incompatible metadata schema version"):
        mvp_ingestion.build_runtime(
            {
                "providers": {
                    "embeddings": "local",
                    "vector_db": "in_memory",
                    "metadata_db": "postgres",
                },
                "paths": {"data_dir": str(tmp_path)},
                "storage": {
                    "metadata_schema_versions": [1],
                    "metadata_providers": {"postgres": {"url": "postgres://example"}},
                },
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
                "vector_db": "pgvector",
            },
            "paths": {"data_dir": str(tmp_path)},
            "storage": {
                "vector": {"allow_fallback": False},
                "metadata_providers": {"postgres": {"url": dsn}},
                "vector_providers": {"pgvector": {"url": dsn}},
            },
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


def test_lancedb_index_is_created_lazily(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeTable:
        def __init__(self):
            self.add_calls = 0
            self.create_index_calls = 0
            self.delete_filters: list[str] = []

        def add(self, rows):
            _ = rows
            self.add_calls += 1

        def create_index(self, metric: str = "cosine", index_type: str = "IVF_HNSW_SQ"):
            _ = metric
            self.create_index_calls += 1

        def delete(self, filter: str) -> None:
            self.delete_filters.append(filter)

    class FakeLanceDBModule:
        @staticmethod
        def connect(_path: str):
            return object()

    fake_table = FakeTable()
    monkeypatch.setitem(
        sys.modules, "lancedb", types.SimpleNamespace(connect=FakeLanceDBModule.connect)
    )
    monkeypatch.setattr(LanceDBVectorStore, "_open_or_create_table", lambda self: fake_table)

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

    store.delete(["abc' OR memory_id = 'other"])
    store.insert(
        "replace' OR memory_id = 'other",
        [
            {
                "chunk_index": 2,
                "role": "user",
                "text": "replace",
                "vector": [0.7, 0.8, 0.9],
            }
        ],
        replace=True,
    )

    assert fake_table.delete_filters == [
        "memory_id = 'abc'' OR memory_id = ''other'",
        "memory_id = 'replace'' OR memory_id = ''other'",
    ]
