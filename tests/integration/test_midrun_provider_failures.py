from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent


class FailingInsertVectorStore:
    expected_dimensionality = 32

    def __init__(self) -> None:
        self.insert_calls = 0

    def insert(
        self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False
    ) -> None:
        self.insert_calls += 1
        message = "vector insert failed password=vector-secret"
        raise RuntimeError(message)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        return []

    def health(self) -> dict[str, Any]:
        return {
            "provider": "redis",
            "status": "ok",
            "stats": {"rows": 0, "expected_dimensionality": self.expected_dimensionality},
        }


class FailingSearchVectorStore:
    expected_dimensionality = 32

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.search_calls = 0

    def insert(
        self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False
    ) -> None:
        if replace:
            self.rows = [row for row in self.rows if row["metadata_id"] != metadata_id]
        self.rows.extend({"metadata_id": metadata_id, **embedding} for embedding in embeddings)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        self.search_calls += 1
        message = "vector search failed token=search-secret"
        raise RuntimeError(message)

    def health(self) -> dict[str, Any]:
        return {
            "provider": "redis",
            "status": "ok",
            "stats": {
                "rows": len(self.rows),
                "expected_dimensionality": self.expected_dimensionality,
            },
        }


def _event_result_json(event_stream_body: str) -> dict[str, Any]:
    for line in event_stream_body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line.removeprefix("data: "))
        if "result" in payload:
            return payload
    raise AssertionError(f"No event-stream result payload found: {event_stream_body}")


def _tool_payload(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    result = _event_result_json(response.text)["result"]
    content = result["content"]
    assert isinstance(content, list) and content
    text = content[0]["text"]
    assert isinstance(text, str)
    return json.loads(text)


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
                "clientInfo": {"name": "pytest-midrun", "version": "0.1"},
            },
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    session_id = response.headers.get("mcp-session-id")
    assert session_id
    return {**headers, "Mcp-Session-Id": session_id}


def _call_tool(
    client: TestClient,
    headers: dict[str, str],
    *,
    request_id: int,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers,
    )
    return _tool_payload(response)


def _conversation(*, memory_id: str | None = None, text: str) -> dict[str, Any]:
    return {
        "id": memory_id or str(uuid4()),
        "source": "pytest",
        "timestamp": "2026-06-27T12:00:00Z",
        "messages": [{"role": "user", "text": text}],
        "metadata": {"tags": ["midrun"]},
    }


def _client(
    tmp_path: Path, vector_store: Any
) -> tuple[TestClient, Path, SQLiteMetadataStore]:
    db_path = tmp_path / "metadata.sqlite"
    metadata_store = SQLiteMetadataStore(db_path)
    embedding_provider = mvp_ingestion.LocalEmbeddingProvider()
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=embedding_provider,
        metadata_store=metadata_store,
        vector_store=vector_store,
        health_state={
            "mode": "ok",
            "metadata_provider": "sqlite",
            "vector_provider": "redis",
            "requested_vector_provider": "redis",
            "vector_fallback_active": False,
            "reasons": [],
        },
    )
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}, "interfaces": {"api": True, "mcp": True}},
        runtime=runtime,
    )
    app = create_app(
        config={
            "interfaces": {"api": True, "mcp": True},
            "providers": {"embeddings": "local", "vector_db": "redis"},
        },
        ingestion_agent=agent,
    )
    return TestClient(app), db_path, metadata_store


def _chunk_states(db_path: Path, conversation_id: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT index_state FROM chunks WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def _assert_fake_vector_health(client: TestClient) -> None:
    health = client.get("/ready")
    assert health.status_code == 200, health.text
    payload = health.json()["health"]
    assert payload["vector_provider"] == "redis"
    assert payload["requested_vector_provider"] == "redis"
    assert payload["vector_fallback_active"] is False
    assert payload["vector_health"]["provider"] == "redis"


def test_api_insert_vector_outage_returns_redacted_503_without_runtime_fallback(
    tmp_path: Path,
) -> None:
    vector_store = FailingInsertVectorStore()
    client, db_path, metadata_store = _client(tmp_path, vector_store)
    conversation = _conversation(text="midrun api insert outage")

    with client:
        response = client.post("/memory/insert", json=conversation)
        _assert_fake_vector_health(client)

    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["error_code"] == "insert_failed"
    assert "password=***" in detail["error_message"]
    assert "vector-secret" not in json.dumps(response.json())
    assert vector_store.insert_calls == 1
    assert metadata_store.get(conversation["id"]) is not None
    assert _chunk_states(db_path, conversation["id"]) == ["indexing_failed"]


def test_mcp_insert_vector_outage_returns_redacted_envelope_without_runtime_fallback(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    vector_store = FailingInsertVectorStore()
    client, db_path, metadata_store = _client(tmp_path, vector_store)
    conversation = _conversation(text="midrun mcp insert outage")

    with client:
        headers = _initialize_mcp(client)
        with caplog.at_level(logging.ERROR, logger="memory.interfaces.mcp_server"):
            result = _call_tool(
                client,
                headers,
                request_id=2,
                name="memory_insert",
                arguments={"conversation_json": conversation},
            )
        _assert_fake_vector_health(client)

    assert result["status"] == "error"
    assert result["error_code"] == "insert_failed"
    assert "password=***" in result["error_message"]
    assert "vector-secret" not in json.dumps(result)
    assert vector_store.insert_calls == 1
    assert metadata_store.get(conversation["id"]) is not None
    assert _chunk_states(db_path, conversation["id"]) == ["indexing_failed"]
    log_record = next(
        record
        for record in caplog.records
        if getattr(record, "event", "") == "mcp_tool_failed"
    )
    assert log_record.operation == "memory_insert"
    assert log_record.error_code == "insert_failed"
    assert log_record.exc_info is not None
    assert "vector-secret" not in caplog.text


def test_api_search_vector_outage_returns_redacted_503_without_runtime_fallback(
    tmp_path: Path,
) -> None:
    vector_store = FailingSearchVectorStore()
    client, _db_path, _metadata_store = _client(tmp_path, vector_store)
    conversation = _conversation(text="midrun api search outage")

    with client:
        insert = client.post("/memory/insert", json=conversation)
        response = client.post("/memory/search", json={"query": "midrun", "top_k": 5})
        _assert_fake_vector_health(client)

    assert insert.status_code == 200, insert.text
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert detail["error_code"] == "search_failed"
    assert "token=***" in detail["error_message"]
    assert "search-secret" not in json.dumps(response.json())
    assert vector_store.search_calls == 1


def test_mcp_search_vector_outage_returns_redacted_envelope_without_runtime_fallback(
    tmp_path: Path,
) -> None:
    vector_store = FailingSearchVectorStore()
    client, _db_path, _metadata_store = _client(tmp_path, vector_store)
    conversation = _conversation(text="midrun mcp search outage")

    with client:
        headers = _initialize_mcp(client)
        insert = client.post("/memory/insert", json=conversation)
        result = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments={"query": "midrun", "top_k": 5},
        )
        _assert_fake_vector_health(client)

    assert insert.status_code == 200, insert.text
    assert result["status"] == "error"
    assert result["error_code"] == "search_failed"
    assert "token=***" in result["error_message"]
    assert "search-secret" not in json.dumps(result)
    assert vector_store.search_calls == 1
