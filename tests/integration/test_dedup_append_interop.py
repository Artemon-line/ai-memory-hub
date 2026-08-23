from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.vector_store import InMemoryVectorStore
from memory.config import ensure_token_hash_secret, parse_config
from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent


class _StubEmbedder:
    dimension: int = 32

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] * self.dimension for text in texts]


class _StubMetadataStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    def insert(self, conversation_json: dict[str, object]) -> str:
        memory_id = str(conversation_json["id"])
        self.rows[memory_id] = conversation_json
        return memory_id

    def is_fully_indexed(self, conversation_id: str) -> bool:
        return conversation_id in self.rows

    def get(self, memory_id: str) -> dict[str, object] | None:
        return self.rows.get(memory_id)

    def get_many(self, ids: list[str]) -> dict[str, dict[str, object]]:
        return {id_: self.rows[id_] for id_ in ids if id_ in self.rows}


def _config(tmp_path: Path, *, auth: str = "none") -> dict[str, Any]:
    return {
        "api": {"auth": auth},
        "interfaces": {"api": True, "mcp": True},
        "paths": {"data_dir": str(tmp_path / "data")},
        "providers": {
            "embeddings": "local",
            "metadata_db": "sqlite",
            "vector_db": "in_memory",
        },
        "storage": {"allow_trusted_appends": True},
    }


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(config=_config(tmp_path)))


def _sqlite_agent_client(tmp_path: Path) -> TestClient:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=mvp_ingestion.LocalEmbeddingProvider(),
        metadata_store=store,
        vector_store=InMemoryVectorStore(dimension=32),
        health_state={
            "mode": "ok",
            "metadata_provider": "sqlite",
            "vector_provider": "memory",
            "vector_fallback_active": False,
            "embedding": {
                "provider": "local",
                "model": "local-deterministic-hash",
                "dimension": 32,
            },
            "embedding_health": {
                "provider": "local",
                "model": "local-deterministic-hash",
                "dimension": 32,
                "status": "ok",
                "live_probe": False,
                "mode": "configuration",
            },
        },
        allow_trusted_appends=True,
    )
    agent = MVPIngestionAgent(config=_config(tmp_path), runtime=runtime)
    return TestClient(create_app(config=_config(tmp_path), ingestion_agent=agent))


def _mcp_transport_client(agent: MVPIngestionAgent) -> TestClient:
    app = create_app(
        config={"interfaces": {"api": False, "mcp": True}},
        ingestion_agent=agent,
    )
    return TestClient(app)


def _auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AMH_TOKEN_HASH_SECRET", "dedup-append-secret")
    config = parse_config(_config(tmp_path, auth="bearer_token"))
    ensure_token_hash_secret(config)
    store = SQLiteMetadataStore(Path(config.paths.data_dir) / "metadata.sqlite3")
    store.create_auth_token(owner_id="owner-a", token="token-a")
    store.create_project(project_id="project-a", owner_id="owner-a", name="Project A")
    store.create_project(project_id="project-b", owner_id="owner-a", name="Project B")
    return TestClient(create_app(config=config))


def _conversation(
    *,
    text: str,
    memory_id: str | None = None,
    source: str = "pytest",
    messages: list[dict[str, str]] | None = None,
    upstream_thread_id: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if upstream_thread_id is not None:
        metadata["upstream_thread_id"] = upstream_thread_id
    return {
        "id": memory_id or str(uuid4()),
        "source": source,
        "timestamp": "2026-06-27T12:00:00Z",
        "messages": messages or [{"role": "user", "text": text}],
        "metadata": metadata,
    }


def _initialize_mcp(client: TestClient, *, token: str | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    response = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0.1"},
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
        if line.startswith("data: "):
            payload = json.loads(line.removeprefix("data: "))
            if "result" in payload:
                return payload
    raise AssertionError(f"No JSON-RPC result found in response: {response_text}")


def _tool_payload(response: Any) -> dict[str, Any]:
    payload = _event_result_json(response.text)
    content = payload["result"]["content"]
    assert isinstance(content, list) and content
    text = content[0]["text"]
    assert isinstance(text, str)
    return json.loads(text)


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
    assert response.status_code == 200, response.text
    return _tool_payload(response)


def test_mcp_transport_keeps_read_session_responsive_during_slow_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=_StubEmbedder(),
        metadata_store=_StubMetadataStore(),
        vector_store=InMemoryVectorStore(dimension=32),
        health_state={"mode": "ok", "vector_fallback_active": False},
    )
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    insert_started = threading.Event()

    def slow_ingest_messages(*_args: object, **_kwargs: object) -> dict[str, object]:
        insert_started.set()
        time.sleep(0.3)
        return {"status": "ok", "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210"}

    def fast_search(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"status": "ok", "results": [], "cursor": None}

    monkeypatch.setattr(agent._service, "ingest_messages", slow_ingest_messages)
    monkeypatch.setattr(agent._service, "search", fast_search)

    with _mcp_transport_client(agent) as client:
        insert_headers = _initialize_mcp(client)
        read_headers = _initialize_mcp(client)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            insert_future = executor.submit(
                _call_tool,
                client,
                insert_headers,
                request_id=2,
                name="memory_insert",
                arguments={"conversation_json": _conversation(text="slow mcp insert")},
            )
            assert insert_started.wait(timeout=1.0)

            started_at = time.perf_counter()
            search_result = _call_tool(
                client,
                read_headers,
                request_id=2,
                name="memory_search",
                arguments={"query": "anything", "top_k": 5},
            )
            elapsed = time.perf_counter() - started_at

            insert_result = insert_future.result(timeout=1.0)

    assert elapsed < 0.2
    assert search_result["status"] == "ok"
    assert search_result["results"] == []
    assert insert_result["status"] == "ok"


def test_api_insert_then_mcp_retry_same_conversation_deduplicates(tmp_path: Path) -> None:
    payload = _conversation(text="Dedup API then MCP phrase is amber clasp.")

    with _client(tmp_path) as client:
        api_insert = client.post("/memory/insert", json=payload)
        headers = _initialize_mcp(client)
        mcp_retry = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        retrieve = client.post("/memory/retrieve", json={"id": payload["id"]})

    assert api_insert.status_code == 200
    assert api_insert.json()["deduplicated"] is False
    assert mcp_retry["status"] == "ok"
    assert mcp_retry["id"] == payload["id"]
    assert mcp_retry["deduplicated"] is True
    assert mcp_retry["appended_messages"] == 0
    assert retrieve.json()["memory"]["messages"][0]["text"] == payload["messages"][0]["text"]


def test_mcp_insert_then_api_retry_same_conversation_deduplicates(tmp_path: Path) -> None:
    payload = _conversation(text="Dedup MCP then API phrase is cobalt clasp.")

    with _client(tmp_path) as client:
        headers = _initialize_mcp(client)
        mcp_insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        api_retry = client.post("/memory/insert", json=payload)

    assert mcp_insert["status"] == "ok"
    assert mcp_insert["deduplicated"] is False
    assert api_retry.status_code == 200
    assert api_retry.json()["id"] == payload["id"]
    assert api_retry.json()["deduplicated"] is True
    assert api_retry.json()["appended_messages"] == 0


def test_api_initial_thread_then_mcp_append_by_upstream_thread_metadata(
    tmp_path: Path,
) -> None:
    first_message = {"role": "user", "text": "thread api first amber"}
    second_message = {"role": "assistant", "text": "thread mcp second cobalt"}
    initial = _conversation(
        text="unused",
        source="codex",
        upstream_thread_id="thread-alpha",
        messages=[first_message],
    )
    continuation = _conversation(
        text="unused",
        source="codex",
        upstream_thread_id="thread-alpha",
        messages=[first_message, second_message],
    )
    continuation.pop("id")

    with _client(tmp_path) as client:
        api_insert = client.post("/memory/insert", json=initial)
        headers = _initialize_mcp(client)
        mcp_append = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": continuation},
        )
        retrieve = client.post("/memory/retrieve", json={"id": initial["id"]})

    assert api_insert.status_code == 200
    assert mcp_append["status"] == "ok"
    assert mcp_append["id"] == initial["id"]
    assert mcp_append["deduplicated"] is False
    assert mcp_append["appended_messages"] == 1
    assert [message["text"] for message in retrieve.json()["memory"]["messages"]] == [
        first_message["text"],
        second_message["text"],
    ]


def test_concurrent_api_continuations_preserve_both_same_thread_appends(
    tmp_path: Path,
) -> None:
    first_message = {"role": "user", "text": "thread concurrent first message"}
    second_message = {"role": "assistant", "text": "thread concurrent codex append"}
    third_message = {"role": "assistant", "text": "thread concurrent copilot append"}
    initial = _conversation(
        text="unused",
        source="codex",
        upstream_thread_id="thread-concurrent-append",
        messages=[first_message],
    )
    codex_continuation = _conversation(
        text="unused",
        source="codex",
        upstream_thread_id="thread-concurrent-append",
        messages=[first_message, second_message],
    )
    copilot_continuation = _conversation(
        text="unused",
        source="codex",
        upstream_thread_id="thread-concurrent-append",
        messages=[first_message, third_message],
    )
    codex_continuation.pop("id")
    copilot_continuation.pop("id")

    with _sqlite_agent_client(tmp_path) as client:
        initial_response = client.post("/memory/insert", json=initial)
        assert initial_response.status_code == 200, initial_response.text

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(client.post, "/memory/insert", json=codex_continuation),
                executor.submit(client.post, "/memory/insert", json=copilot_continuation),
            ]
            append_responses = [future.result(timeout=5.0) for future in futures]

        retrieve = client.post("/memory/retrieve", json={"id": initial["id"]})

    assert [response.status_code for response in append_responses] == [200, 200]
    assert sorted(response.json()["appended_messages"] for response in append_responses) == [1, 1]
    assert retrieve.status_code == 200, retrieve.text
    texts = [message["text"] for message in retrieve.json()["memory"]["messages"]]
    assert texts[0] == first_message["text"]
    assert set(texts[1:]) == {second_message["text"], third_message["text"]}


def test_mcp_initial_thread_then_api_append_by_upstream_thread_metadata(
    tmp_path: Path,
) -> None:
    first_message = {"role": "user", "text": "thread mcp first green"}
    second_message = {"role": "assistant", "text": "thread api second silver"}
    initial = _conversation(
        text="unused",
        source="opencode",
        upstream_thread_id="thread-beta",
        messages=[first_message],
    )
    continuation = _conversation(
        text="unused",
        source="opencode",
        upstream_thread_id="thread-beta",
        messages=[first_message, second_message],
    )
    continuation.pop("id")

    with _client(tmp_path) as client:
        headers = _initialize_mcp(client)
        mcp_insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": initial},
        )
        api_append = client.post("/memory/insert", json=continuation)
        retrieve = client.post("/memory/retrieve", json={"id": initial["id"]})

    assert mcp_insert["status"] == "ok"
    assert api_append.status_code == 200
    assert api_append.json()["id"] == initial["id"]
    assert api_append.json()["deduplicated"] is False
    assert api_append.json()["appended_messages"] == 1
    assert [message["text"] for message in retrieve.json()["memory"]["messages"]] == [
        first_message["text"],
        second_message["text"],
    ]


def test_same_id_different_content_conflicts_are_deterministic_across_api_and_mcp(
    tmp_path: Path,
) -> None:
    api_id = str(uuid4())
    mcp_id = str(uuid4())
    api_first = _conversation(memory_id=api_id, text="api original text")
    api_conflict = _conversation(memory_id=api_id, text="api edited text")
    mcp_first = _conversation(memory_id=mcp_id, text="mcp original text")
    mcp_conflict = _conversation(memory_id=mcp_id, text="mcp edited text")

    with _client(tmp_path) as client:
        api_insert = client.post("/memory/insert", json=api_first)
        api_conflict_response = client.post("/memory/insert", json=api_conflict)
        headers = _initialize_mcp(client)
        mcp_insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": mcp_first},
        )
        mcp_conflict_response = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_insert",
            arguments={"conversation_json": mcp_conflict},
        )

    assert api_insert.status_code == 200
    assert api_conflict_response.status_code == 400
    assert "duplicate_conflict" in api_conflict_response.json()["detail"]
    assert mcp_insert["status"] == "ok"
    assert mcp_conflict_response["status"] == "error"
    assert mcp_conflict_response["error_code"] == "duplicate_conflict"
    assert "duplicate_conflict" in mcp_conflict_response["error_message"]


def test_same_conversation_content_is_allowed_and_isolated_across_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _conversation(text="Same content across projects phrase is brass mirror.")
    second = _conversation(text="Same content across projects phrase is brass mirror.")

    with _auth_client(tmp_path, monkeypatch) as client:
        owner_headers = {"Authorization": "Bearer token-a"}
        api_insert = client.post(
            "/memory/insert",
            json={**first, "project_id": "project-a"},
            headers=owner_headers,
        )
        headers = _initialize_mcp(client, token="token-a")
        mcp_insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": second, "project_id": "project-b"},
        )
        project_a_search = client.post(
            "/memory/search",
            json={"query": "brass mirror", "project_id": "project-a"},
            headers=owner_headers,
        )
        project_b_search = client.post(
            "/memory/search",
            json={"query": "brass mirror", "project_id": "project-b"},
            headers=owner_headers,
        )

    assert api_insert.status_code == 200
    assert api_insert.json()["id"] == first["id"]
    assert mcp_insert["status"] == "ok"
    assert mcp_insert["id"] == second["id"]
    assert [row["id"] for row in project_a_search.json()["results"]] == [first["id"]]
    assert [row["id"] for row in project_b_search.json()["results"]] == [second["id"]]
