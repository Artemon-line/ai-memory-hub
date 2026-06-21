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
from memory.backend.mongodb_metadata_store import MongoDBMetadataStore
from memory.backend.postgres_metadata_store import PostgresMetadataStore
from memory.backend.vector_store import (
    ChromaDBVectorStore,
    ChromaMetadata,
    InMemoryVectorStore,
    LanceDBVectorStore,
    MongoDBAtlasVectorStore,
    PGVectorStore,
    QdrantVectorStore,
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
    assert any(
        row["id"] == "jane" and row["display_name"] == "Jane"
        for row in store.list_users()
    )
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
        row
        for row in store.search([1.0, 0.0, 0.0], top_k=10)
        if row["memory_id"] == replacement_id
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
        "qdrant",
        "mongodb_atlas",
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
            chunk_id: row
            for chunk_id, row in self.rows.items()
            if row["memory_id"] != memory_id
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
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

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
            "distances": [
                [
                    _test_cosine_distance(query_vector, row["embedding"])
                    for row in rows
                ]
            ],
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
    def __init__(self) -> None:
        self.collection = FakeChromaCollection()

    def get_or_create_collection(self, *, name: str, metadata: dict[str, Any]) -> Any:
        assert name == "memory_vectors"
        assert metadata == {"hnsw:space": "cosine"}
        return self.collection


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
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}
        self.created: dict[str, Any] = {}

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
        _ = collection_name
        return types.SimpleNamespace(points_count=len(self.rows))


class FakeMongoCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def create_index(self, *_args: Any, **_kwargs: Any) -> None:
        return None

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

    def find_one(self, query: dict[str, Any], sort: list[tuple[str, int]] | None = None) -> dict[str, Any] | None:
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
            lambda tmp_path: ChromaDBVectorStore(
                client=FakeChromaClient(), dimension=3
            ),
        ),
        (
            "qdrant",
            lambda tmp_path: QdrantVectorStore(
                client=FakeQdrantClient(), models=FakeQdrantModels, dimension=3
            ),
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
            "fake-sdk",
            lambda tmp_path: FakeSDKVectorStore(
                client=FakeVectorClient(), dimension=3
            ),
        ),
    ],
)
def test_vector_store_contract_for_local_backends(
    provider: str, factory: VectorStoreFactory, tmp_path: Path
) -> None:
    _ = provider
    _assert_vector_store_contract(factory(tmp_path))


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
        "mongodb+srv://user:mongo-secret@example.mongodb.net/app "
        "http://elastic:elastic-secret@search.local:9200 "
        "password=hunter2 token=abc123 api_key=xyz api-key=qdrant-secret"
    )
    redacted = redact_secrets(message)
    assert "supersecret" not in redacted
    assert "mongo-secret" not in redacted
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
        (
            "mongodb_atlas",
            "MongoDBAtlasVectorStore",
            "mongodb unavailable mongodb://u:atlas-secret@example/app",
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
    runtime = mvp_ingestion.build_runtime(
        {
            "providers": {"embeddings": "local", "vector_db": provider},
            "paths": {"data_dir": str(tmp_path)},
            "storage": {
                "vector": {"allow_fallback": True},
                "vector_providers": {
                    "mongodb_atlas": {"uri": "mongodb://u:atlas-secret@example/app"}
                },
            },
        }
    )

    assert runtime.health_state["mode"] == "degraded"
    assert runtime.health_state["vector_provider"] == "memory"
    assert runtime.health_state["requested_vector_provider"] == provider
    assert runtime.vector_store.expected_dimensionality == runtime.embedding_provider.dimension
    assert "qdrant-secret" not in " ".join(runtime.health_state["reasons"])
    assert "atlas-secret" not in " ".join(runtime.health_state["reasons"])


@pytest.mark.parametrize(
    ("provider", "class_name"),
    [
        ("qdrant", "QdrantVectorStore"),
        ("mongodb_atlas", "MongoDBAtlasVectorStore"),
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
                    "vector_providers": {
                        "mongodb_atlas": {"uri": "mongodb://example/app"}
                    },
                },
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
