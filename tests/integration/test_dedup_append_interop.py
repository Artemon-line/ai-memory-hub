from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.config import ensure_token_hash_secret, parse_config


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


def _auth_client(tmp_path: Path) -> TestClient:
    os.environ["AMH_TOKEN_HASH_SECRET"] = "dedup-append-secret"
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
    tmp_path: Path,
) -> None:
    first = _conversation(text="Same content across projects phrase is brass mirror.")
    second = _conversation(text="Same content across projects phrase is brass mirror.")

    with _auth_client(tmp_path) as client:
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
