from __future__ import annotations

from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent


class StubEmbedder(mvp_ingestion.EmbeddingProvider):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]
    dimension: int



class StubMetadataStore:
    def __init__(self):
        self.rows: dict[str, dict[str, object]] = {}

    def insert(self, conversation_json: dict[str, object]) -> str:
        memory_id = str(conversation_json["id"])
        self.rows[memory_id] = conversation_json
        return memory_id

    def get(self, memory_id: str):
        return self.rows.get(memory_id)

    def get_many(self, ids: list[str]):
        return {id_: self.rows[id_] for id_ in ids if id_ in self.rows}


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


def _runtime() -> mvp_ingestion.RuntimeDependencies:
    return mvp_ingestion.RuntimeDependencies(
        embedding_provider=StubEmbedder(),
        metadata_store=StubMetadataStore(),
        vector_store=StubVectorStore(),
    )


def _conversation() -> dict[str, object]:
    return {
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "source": "chatgpt",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "hello"}],
        "metadata": {"imported_at": "2026-01-01T00:00:00Z"},
    }


def _client() -> TestClient:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}, "interfaces": {"api": 'true'}}, runtime=_runtime())
    app = create_app(config={"providers": {"embeddings": "local", "vector_db": "pgvector"}}, ingestion_agent=agent)
    return TestClient(app)


def test_memory_insert_200() -> None:
    client = _client()
    response = client.post("/memory/insert", json=_conversation())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_memory_insert_400_on_invalid_schema() -> None:
    client = _client()
    bad_payload = _conversation()
    bad_payload.pop("id")

    response = client.post("/memory/insert", json=bad_payload)
    assert response.status_code == 400


def test_memory_search_and_retrieve() -> None:
    client = _client()
    payload = _conversation()
    client.post("/memory/insert", json=payload)

    search = client.post("/memory/search", json={"query": "hello", "top_k": 3})
    assert search.status_code == 200
    assert search.json()["status"] == "ok"

    retrieve = client.post("/memory/retrieve", json={"id": payload["id"]})
    assert retrieve.status_code == 200
    assert retrieve.json()["memory"]["id"] == payload["id"]
