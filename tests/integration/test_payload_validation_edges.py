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
    }


def _auth_client(tmp_path: Path) -> TestClient:
    os.environ["AMH_TOKEN_HASH_SECRET"] = "payload-validation-secret"
    config = parse_config(_config(tmp_path, auth="bearer_token"))
    ensure_token_hash_secret(config)
    store = SQLiteMetadataStore(Path(config.paths.data_dir) / "metadata.sqlite3")
    store.create_auth_token(owner_id="owner-a", token="token-a")
    return TestClient(create_app(config=config))


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(config=_config(tmp_path)))


def _conversation(
    *,
    text: str,
    memory_id: str | None = None,
    source: str = "pytest",
    timestamp: str = "2026-06-27T12:00:00Z",
    tags: list[str] | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if tags is not None:
        metadata["tags"] = tags
    if thread_id is not None:
        metadata["thread_id"] = thread_id
    return {
        "id": memory_id or str(uuid4()),
        "source": source,
        "timestamp": timestamp,
        "messages": [{"role": "user", "text": text}],
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


def test_api_insert_ignores_client_supplied_owner_id_and_stamps_authenticated_owner(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="API owner stamp phrase is blue archive.")
    payload["metadata"]["owner_id"] = "client-supplied"

    with _auth_client(tmp_path) as client:
        insert = client.post(
            "/memory/insert",
            json=payload,
            headers={"Authorization": "Bearer token-a"},
        )
        retrieve = client.post(
            "/memory/retrieve",
            json={"id": payload["id"]},
            headers={"Authorization": "Bearer token-a"},
        )

    assert insert.status_code == 200, insert.text
    assert retrieve.status_code == 200, retrieve.text
    assert retrieve.json()["memory"]["metadata"]["owner_id"] == "owner-a"


def test_mcp_insert_ignores_client_supplied_owner_id_and_stamps_authenticated_owner(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="MCP owner stamp phrase is green archive.")
    payload["metadata"]["owner_id"] = "client-supplied"

    with _auth_client(tmp_path) as client:
        headers = _initialize_mcp(client, token="token-a")
        insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        retrieve = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_retrieve",
            arguments={"id": payload["id"]},
        )

    assert insert["status"] == "ok"
    assert retrieve["status"] == "ok"
    assert retrieve["memory"]["metadata"]["owner_id"] == "owner-a"


def test_api_and_mcp_reject_invalid_project_id_formats(tmp_path: Path) -> None:
    bad_project_id = "bad/project"

    with _auth_client(tmp_path) as client:
        api_search = client.post(
            "/memory/search",
            json={"query": "anything", "project_id": bad_project_id},
            headers={"Authorization": "Bearer token-a"},
        )
        api_retrieve = client.post(
            "/memory/retrieve",
            json={"id": str(uuid4()), "project_id": bad_project_id},
            headers={"Authorization": "Bearer token-a"},
        )
        api_ask = client.post(
            "/memory/ask",
            json={"question": "anything?", "project_id": bad_project_id},
            headers={"Authorization": "Bearer token-a"},
        )
        headers = _initialize_mcp(client, token="token-a")
        mcp_search = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments={"query": "anything", "project_id": bad_project_id},
        )
        mcp_retrieve = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_retrieve",
            arguments={"id": str(uuid4()), "project_id": bad_project_id},
        )
        mcp_ask = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_ask",
            arguments={"question": "anything?", "project_id": bad_project_id},
        )

    assert api_search.status_code == 400
    assert api_retrieve.status_code == 400
    assert api_ask.status_code == 400
    for payload in (mcp_search, mcp_retrieve, mcp_ask):
        assert payload["status"] == "error"
        assert payload["error_code"] == "invalid_input"
        assert "project_id" in payload["error_message"]


def test_api_and_mcp_reject_unknown_result_modes(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        api_search = client.post(
            "/memory/search",
            json={"query": "payload-secret-query", "result_mode": "unknown"},
        )
        api_ask = client.post(
            "/memory/ask",
            json={"question": "payload-secret-question", "result_mode": "unknown"},
        )
        headers = _initialize_mcp(client)
        mcp_search = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments={"query": "payload-secret-query", "result_mode": "unknown"},
        )
        mcp_ask = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_ask",
            arguments={"question": "payload-secret-question", "result_mode": "unknown"},
        )

    assert api_search.status_code == 400
    assert api_ask.status_code == 400
    assert "payload-secret-query" not in api_search.text
    assert "payload-secret-question" not in api_ask.text
    for payload in (mcp_search, mcp_ask):
        assert payload["status"] == "error"
        assert payload["error_code"] == "invalid_input"
        assert "result_mode must be one of" in payload["error_message"]
        assert "payload-secret" not in json.dumps(payload)


def test_api_and_mcp_reject_extreme_numeric_request_values(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        api_search = client.post("/memory/search", json={"query": "x", "top_k": 101})
        api_ask_top_k = client.post(
            "/memory/ask",
            json={"question": "x?", "top_k": 101},
        )
        api_ask_context = client.post(
            "/memory/ask",
            json={"question": "x?", "max_context_tokens": 0},
        )
        headers = _initialize_mcp(client)
        mcp_search_top_k = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments={"query": "x", "top_k": 101},
        )
        mcp_search_limit = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_search",
            arguments={"query": "x", "limit": 101},
        )
        mcp_ask_top_k = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_ask",
            arguments={"question": "x?", "top_k": 101},
        )
        mcp_ask_context = _call_tool(
            client,
            headers,
            request_id=5,
            name="memory_ask",
            arguments={"question": "x?", "max_context_tokens": 0},
        )

    assert api_search.status_code == 422
    assert api_ask_top_k.status_code == 422
    assert api_ask_context.status_code == 422
    for payload in (mcp_search_top_k, mcp_search_limit, mcp_ask_top_k, mcp_ask_context):
        assert payload["status"] == "error"
        assert payload["error_code"] == "invalid_input"


def test_api_and_mcp_preserve_date_tag_thread_and_source_filters(tmp_path: Path) -> None:
    target = _conversation(
        text="filter beacon target",
        source="codex",
        timestamp="2026-06-20T12:00:00Z",
        tags=["release", "p1"],
        thread_id="thread-target",
    )
    wrong_source = _conversation(
        text="filter beacon wrong source",
        source="opencode",
        timestamp="2026-06-20T12:00:00Z",
        tags=["release", "p1"],
        thread_id="thread-target",
    )
    wrong_tag = _conversation(
        text="filter beacon wrong tag",
        source="codex",
        timestamp="2026-06-20T12:00:00Z",
        tags=["release"],
        thread_id="thread-target",
    )
    wrong_thread = _conversation(
        text="filter beacon wrong thread",
        source="codex",
        timestamp="2026-06-20T12:00:00Z",
        tags=["release", "p1"],
        thread_id="thread-other",
    )
    wrong_date = _conversation(
        text="filter beacon wrong date",
        source="codex",
        timestamp="2026-06-10T12:00:00Z",
        tags=["release", "p1"],
        thread_id="thread-target",
    )
    filter_payload = {
        "query": "filter beacon",
        "top_k": 5,
        "source": "codex",
        "date_from": "2026-06-19T00:00:00Z",
        "date_to": "2026-06-21T00:00:00Z",
        "tags": ["p1"],
        "thread_id": "thread-target",
    }

    with _client(tmp_path) as client:
        for payload in (target, wrong_source, wrong_tag, wrong_thread, wrong_date):
            insert = client.post("/memory/insert", json=payload)
            assert insert.status_code == 200, insert.text
        api_search = client.post("/memory/search", json=filter_payload)
        headers = _initialize_mcp(client)
        mcp_search = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments=filter_payload,
        )

    assert api_search.status_code == 200
    assert [row["id"] for row in api_search.json()["results"]] == [target["id"]]
    assert [row["id"] for row in mcp_search["results"]] == [target["id"]]


def test_api_and_mcp_validation_errors_do_not_echo_sensitive_payload_text(
    tmp_path: Path,
) -> None:
    secret = "payload-secret-velvet"
    invalid_payload = {
        "id": str(uuid4()),
        "source": "pytest",
        "timestamp": "2026-06-27T12:00:00Z",
        "title": secret,
    }

    with _client(tmp_path) as client:
        api_insert = client.post("/memory/insert", json=invalid_payload)
        headers = _initialize_mcp(client)
        mcp_insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": invalid_payload},
        )

    assert api_insert.status_code == 400
    assert secret not in api_insert.text
    assert mcp_insert["status"] == "error"
    assert mcp_insert["error_code"] == "invalid_input"
    assert secret not in json.dumps(mcp_insert)
