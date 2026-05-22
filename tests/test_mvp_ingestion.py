from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from memory.ingestion import mvp_ingestion
from memory.ingestion import validate as ingestion_validate


class StubEmbedder:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), float(index)] for index, text in enumerate(texts)]


class StubMetadataStore:
    def __init__(self):
        self.by_id: dict[str, dict[str, object]] = {}

    def insert(self, conversation_json: dict[str, object]) -> str:
        memory_id = str(conversation_json["id"])
        self.by_id[memory_id] = conversation_json
        return memory_id

    def get(self, memory_id: str):
        return self.by_id.get(memory_id)

    def get_many(self, ids: list[str]):
        return {id_: self.by_id[id_] for id_ in ids if id_ in self.by_id}


class StubVectorStore:
    def __init__(self):
        self.rows: list[dict[str, object]] = []

    def insert(self, metadata_id: str, embeddings: list[dict[str, object]]) -> None:
        for item in embeddings:
            self.rows.append({"memory_id": metadata_id, **item})

    def search(self, query_vector: list[float], top_k: int = 5):
        _ = query_vector
        return [
            {
                "memory_id": row["memory_id"],
                "chunk_index": row["chunk_index"],
                "role": row["role"],
                "text": row["text"],
                "score": float(index),
            }
            for index, row in enumerate(self.rows[:top_k])
        ]


def _valid_conversation() -> dict[str, object]:
    return {
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "world"},
        ],
        "metadata": {
            "imported_at": "2026-01-01T00:00:00Z",
        },
    }


def _configure_stubs() -> tuple[StubMetadataStore, StubVectorStore]:
    metadata = StubMetadataStore()
    vectors = StubVectorStore()
    mvp_ingestion.configure_runtime(
        runtime=mvp_ingestion.RuntimeDependencies(
            embedding_provider=StubEmbedder(), # type: ignore
            metadata_store=metadata,
            vector_store=vectors,
            health_state={"mode": "ok", "vector_fallback_active": False},
        )
    )
    return metadata, vectors


def test_ingest_messages_success() -> None:
    metadata, vectors = _configure_stubs()

    result = mvp_ingestion.ingest_messages(_valid_conversation())

    assert result == {
        "status": "ok",
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "chunks": 2,
    }
    assert "d9fd4c95-9cb3-4fd5-b967-3027f8863210" in metadata.by_id
    assert len(vectors.rows) == 2


def test_ingest_messages_invalid_json_raises() -> None:
    _configure_stubs()
    invalid = _valid_conversation()
    del invalid["id"]

    try:
        mvp_ingestion.ingest_messages(invalid)
        assert False, "Expected validation error"
    except jsonschema.ValidationError:
        assert True


def test_search_and_retrieve() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    mvp_ingestion.ingest_messages(conversation)

    search_result = mvp_ingestion.search(query="hello", top_k=5)
    retrieved = mvp_ingestion.retrieve("d9fd4c95-9cb3-4fd5-b967-3027f8863210")

    assert search_result["status"] == "ok"
    assert len(search_result["results"]) >= 1
    assert retrieved is not None
    assert retrieved["id"] == "d9fd4c95-9cb3-4fd5-b967-3027f8863210"


def test_ingest_messages_enriches_topics() -> None:
    metadata, _ = _configure_stubs()
    conversation = _valid_conversation()
    conversation["messages"] = [
        {"role": "user", "text": "Build FastAPI endpoint with MCP and SQLite search"},
        {"role": "assistant", "text": "Use pytest for tests and add docker setup"},
    ]

    mvp_ingestion.ingest_messages(conversation)
    stored = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]
    topics = stored["metadata"].get("topics", [])

    assert "mcp" in topics
    assert "backend" in topics
    assert "sql" in topics
    assert "testing" in topics
    assert "docker" in topics


def test_ingest_messages_preserves_existing_topics() -> None:
    metadata, _ = _configure_stubs()
    conversation = _valid_conversation()
    conversation["metadata"]["topics"] = ["custom-topic", "mcp"]
    conversation["messages"] = [
        {"role": "user", "text": "MCP API design"},
    ]

    mvp_ingestion.ingest_messages(conversation)
    stored = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]
    topics = stored["metadata"]["topics"]

    assert topics[0] == "custom-topic"
    assert topics.count("mcp") == 1


def test_schema_file_from_config_is_used(tmp_path: Path) -> None:
    schema_path = tmp_path / "conversation.custom.schema.json"
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "source": {"type": "string"},
            "timestamp": {"type": "string"},
            "messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["role", "text"],
                },
            },
            "metadata": {"type": "object"},
            "must_exist": {"type": "string"},
        },
        "required": ["id", "source", "timestamp", "messages", "metadata", "must_exist"],
        "additionalProperties": True,
    }
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    try:
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "in_memory"},
                "paths": {"data_dir": str(tmp_path / "data")},
                "schema": {"file": str(schema_path)},
            }
        )
        with pytest.raises(jsonschema.ValidationError):
            mvp_ingestion.validate_json(_valid_conversation())
    finally:
        ingestion_validate.set_schema_path(None)


def test_schema_missing_code_required_fields_fails_startup(tmp_path: Path) -> None:
    schema_path = tmp_path / "conversation.incompatible.schema.json"
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "source": {"type": "string"},
        },
        "required": ["id", "source"],
        "additionalProperties": True,
    }
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(ValueError, match="incompatible with code expectations"):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "in_memory"},
                "paths": {"data_dir": str(tmp_path / "data")},
                "schema": {"file": str(schema_path)},
            }
        )
