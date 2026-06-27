from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.vector_store import InMemoryVectorStore
from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent


class MultilingualTestEmbedder:
    dimension = 3

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        normalized = unicodedata.normalize("NFKD", text).casefold()
        if any(term in normalized for term in ("memory", "recall")):
            return [1.0, 0.0, 0.0]
        if any(term in normalized for term in ("guitarra", "guitar", "ギター")):
            return [0.0, 1.0, 0.0]
        if any(term in normalized for term in ("cafe", "café", "コーヒー")):
            return [0.0, 0.0, 1.0]
        return [0.1, 0.1, 0.1]


def _client(tmp_path: Path) -> TestClient:
    embedder = MultilingualTestEmbedder()
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=embedder,
        metadata_store=SQLiteMetadataStore(tmp_path / "metadata.sqlite3"),
        vector_store=InMemoryVectorStore(dimension=embedder.dimension),
        health_state={
            "mode": "ok",
            "metadata_provider": "sqlite",
            "vector_provider": "memory",
            "requested_vector_provider": "memory",
            "vector_fallback_active": False,
            "reasons": [],
            "embedding": {
                "provider": "test",
                "model": "multilingual-test-embedder",
                "dimension": embedder.dimension,
                "options": {},
            },
            "vector_index": {"provider": "memory", "id": "memory:test", "distance": "cosine"},
            "embedding_index_mismatch": False,
        },
    )
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}, "interfaces": {"api": True, "mcp": True}},
        runtime=runtime,
    )
    return TestClient(
        create_app(
            config={
                "interfaces": {"api": True, "mcp": True},
                "providers": {"embeddings": "local", "vector_db": "in_memory"},
            },
            ingestion_agent=agent,
        )
    )


def _conversation(text: str) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "source": "pytest",
        "messages": [{"role": "user", "text": text}],
    }


def _initialize_mcp(client: TestClient) -> dict[str, str]:
    headers = {"Accept": "application/json, text/event-stream"}
    response = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    session_id = response.headers.get("mcp-session-id")
    assert session_id
    return {**headers, "mcp-session-id": session_id}


def _event_result_json(response_text: str) -> dict[str, Any]:
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line.removeprefix("data: "))
        if "result" in payload:
            return payload
    raise AssertionError(f"No SSE result found in response: {response_text}")


def _read_resource(
    client: TestClient,
    headers: dict[str, str],
    *,
    request_id: int,
    uri: str,
) -> dict[str, Any]:
    response = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "resources/read",
            "params": {"uri": uri},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = _event_result_json(response.text)
    text = payload["result"]["contents"][0]["text"]
    return json.loads(text)


def test_english_insert_and_query_use_configured_embedding_space(tmp_path: Path) -> None:
    payload = _conversation("The memory recall phrase is amber bridge.")

    with _client(tmp_path) as client:
        insert = client.post("/memory/insert", json=payload)
        search = client.post("/memory/search", json={"query": "memory recall", "top_k": 3})

    assert insert.status_code == 200, insert.text
    assert search.status_code == 200, search.text
    assert search.json()["results"][0]["id"] == payload["id"]


def test_non_english_same_language_query_works_with_multilingual_embedder(
    tmp_path: Path,
) -> None:
    payload = _conversation("Mi guitarra favorita es una Fender roja.")

    with _client(tmp_path) as client:
        insert = client.post("/memory/insert", json=payload)
        search = client.post("/memory/search", json={"query": "guitarra roja", "top_k": 3})

    assert insert.status_code == 200, insert.text
    assert search.status_code == 200, search.text
    assert search.json()["results"][0]["id"] == payload["id"]


def test_non_english_insert_english_query_is_model_quality_smoke(tmp_path: Path) -> None:
    payload = _conversation("La guitarra importante es azul.")

    with _client(tmp_path) as client:
        insert = client.post("/memory/insert", json=payload)
        search = client.post("/memory/search", json={"query": "blue guitar", "top_k": 3})

    assert insert.status_code == 200, insert.text
    assert search.status_code == 200, search.text
    assert search.json()["results"][0]["id"] == payload["id"]


def test_unicode_normalization_edges_do_not_crash_memory_or_fact_paths(
    tmp_path: Path,
) -> None:
    decomposed_cafe = "Cafe\u0301"
    payload = _conversation(
        f"My name is José Álvarez. I own a {decomposed_cafe} guitar. "
        "Résumé notes mention mañana and コーヒー."
    )

    with _client(tmp_path) as client:
        insert = client.post("/memory/insert", json=payload)
        search = client.post("/memory/search", json={"query": "café ギター", "top_k": 3})
        ask = client.post("/memory/ask", json={"question": "What guitar do I own?"})
        facts = client.post(
            "/memory/facts/search",
            json={"subject": "user", "predicate": "owns_guitar"},
        )
        profile = client.post("/memory/profile/get", json={"subject": "user"})

    assert insert.status_code == 200, insert.text
    assert search.status_code == 200, search.text
    assert ask.status_code == 200, ask.text
    assert facts.status_code == 200, facts.text
    assert profile.status_code == 200, profile.text
    assert search.json()["results"][0]["id"] == payload["id"]
    assert facts.json()["results"]
    assert profile.json()["facts"]


def test_api_and_mcp_health_expose_embedding_index_metadata_without_secrets(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        ready = client.get("/ready")
        headers = _initialize_mcp(client)
        mcp_health = _read_resource(client, headers, request_id=2, uri="memory://health")

    assert ready.status_code == 200, ready.text
    ready_payload = ready.json()
    assert ready_payload["health"]["embedding"] == {
        "provider": "test",
        "model": "multilingual-test-embedder",
        "dimension": 3,
        "options": {},
    }
    assert ready_payload["health"]["embedding_index_mismatch"] is False
    assert mcp_health["health"]["embedding"] == ready_payload["health"]["embedding"]
    assert "api_key" not in json.dumps(ready_payload).lower()
    assert "api_key" not in json.dumps(mcp_health).lower()
