from __future__ import annotations

from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent


class StubEmbedder(mvp_ingestion.EmbeddingProvider):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]
    dimension: int = 32



class StubMetadataStore:
    def __init__(self):
        self.rows: dict[str, dict[str, object]] = {}

    def insert(self, conversation_json: dict[str, object]) -> str:
        memory_id = str(conversation_json["id"])
        self.rows[memory_id] = conversation_json
        return memory_id

    def is_fully_indexed(self, conversation_id: str) -> bool:
        return conversation_id in self.rows

    def get(self, memory_id: str):
        return self.rows.get(memory_id)

    def get_many(self, ids: list[str]):
        return {id_: self.rows[id_] for id_ in ids if id_ in self.rows}


class StubVectorStore:
    def __init__(self):
        self.rows: list[dict[str, object]] = []

    def insert(self, metadata_id: str, embeddings: list[dict[str, object]], replace: bool = False) -> None:
        if replace:
            self.rows = [row for row in self.rows if row["memory_id"] != metadata_id]
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
        health_state={"mode": "ok", "vector_fallback_active": False},
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
    app = create_app(config={"providers": {"embeddings": "local", "vector_db": "in_memory"}}, ingestion_agent=agent)
    return TestClient(app)


def test_memory_insert_200() -> None:
    client = _client()
    response = client.post("/memory/insert", json=_conversation())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_and_ready_are_public_and_redacted() -> None:
    client = _client()

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.json()["status"] == "ok"
    assert ready.json()["mode"] == "ok"
    assert "content_hash" not in str(health.json())
    assert "content_hash" not in str(ready.json())


def test_memory_insert_400_on_invalid_schema() -> None:
    client = _client()
    bad_payload = _conversation()
    bad_payload.pop("messages")

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
    assert "hash" not in retrieve.json()["memory"]["messages"][0]
    assert "conversation_hash" not in retrieve.json()["memory"]["metadata"]


def test_memory_ask() -> None:
    client = _client()
    payload = _conversation()
    client.post("/memory/insert", json=payload)

    ask = client.post("/memory/ask", json={"question": "what did I say?", "top_k": 3})
    assert ask.status_code == 200
    body = ask.json()
    assert body["status"] == "ok"
    assert "answer" in body
    assert len(body["results"]) == 1
    assert body["results"][0]["id"] == payload["id"]
    assert body["results"][0]["text"] == "hello"
    assert "hash" not in body["results"][0]["conversation"]["messages"][0]
    assert isinstance(body["citations"], list)
    assert body["citations"][0]["id"] == body["results"][0]["id"]
    assert body["citations"][0]["text"] == body["results"][0]["text"]


def test_memory_ask_accepts_context_budget() -> None:
    client = _client()
    payload = _conversation()
    client.post("/memory/insert", json=payload)

    ask = client.post(
        "/memory/ask",
        json={"question": "what did I say?", "top_k": 3, "max_context_tokens": 100},
    )

    assert ask.status_code == 200
    body = ask.json()
    assert body["status"] == "ok"
    assert body["context_tokens_used"] <= 100
    assert body["chunks_selected"] == 1
    assert body["chunks_dropped"] == 0
    assert body["tokenizer_used"] in {"heuristic", "tiktoken:cl100k_base"}


def test_memory_fact_endpoints() -> None:
    client = _client()
    payload = _conversation()
    payload["messages"] = [
        {"role": "user", "text": "I own a Gibson Special with P90 pickups, cherry."}
    ]
    client.post("/memory/insert", json=payload)

    facts = client.post(
        "/memory/facts/search",
        json={"subject": "user", "predicate": "owns_guitar"},
    )
    profile = client.post("/memory/profile/get", json={"subject": "user"})
    ask = client.post("/memory/ask", json={"question": "What guitar do I own?"})

    assert facts.status_code == 200
    assert facts.json()["results"][0]["predicate"] == "owns_guitar"
    assert facts.json()["results"][0]["source_quality"] == "direct_user_statement"
    assert facts.json()["results"][0]["last_confirmed_at"] == facts.json()["results"][0]["updated_at"]
    assert profile.status_code == 200
    assert profile.json()["facts"][0]["object"] == facts.json()["results"][0]["object"]
    ask_body = ask.json()
    assert ask_body["answer_basis"] == "fact_layer"
    assert ask_body["confidence_reason"] == "Extracted from a direct user statement."
    assert ask_body["evidence"][0]["type"] == "fact"
    assert ask_body["structured_evidence"]["results"] == []
    assert ask_body["facts"][0]["last_confirmed_at"] == ask_body["facts"][0]["updated_at"]
