from __future__ import annotations

import jsonschema

from memory.ingestion import mvp_ingestion


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
            embedding_provider=StubEmbedder(),
            metadata_store=metadata,
            vector_store=vectors,
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
