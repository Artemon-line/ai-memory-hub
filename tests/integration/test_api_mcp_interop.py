import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import PROJECT_ROLE_WRITER, SQLiteMetadataStore
from memory.backend.vector_store import InMemoryVectorStore
from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent


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
                "clientInfo": {"name": "pytest-interop", "version": "0.1"},
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
    result = _event_result_json(response.text)["result"]
    contents = result["contents"]
    assert isinstance(contents, list) and contents
    text = contents[0]["text"]
    assert isinstance(text, str)
    return json.loads(text)


def _conversation(*, memory_id: str | None = None, text: str, source: str = "pytest") -> dict[str, Any]:
    return {
        "id": memory_id or str(uuid4()),
        "source": source,
        "timestamp": "2026-06-27T12:00:00Z",
        "messages": [{"role": "user", "text": text}],
        "metadata": {"tags": ["interop"]},
    }


def _runtime(
    metadata_store: Any,
    *,
    health_state: dict[str, Any] | None = None,
) -> mvp_ingestion.RuntimeDependencies:
    embedding_provider = mvp_ingestion.LocalEmbeddingProvider()
    return mvp_ingestion.RuntimeDependencies(
        embedding_provider=embedding_provider,
        metadata_store=metadata_store,
        vector_store=InMemoryVectorStore(dimension=embedding_provider.dimension),
        health_state=health_state or {"mode": "ok", "vector_fallback_active": False},
    )


def _client(
    *,
    tmp_path: Path | None = None,
    config: dict[str, Any] | None = None,
    metadata_store: Any | None = None,
    runtime: mvp_ingestion.RuntimeDependencies | None = None,
) -> TestClient:
    if runtime is None:
        if metadata_store is None:
            if tmp_path is None:
                raise ValueError("tmp_path is required when metadata_store is omitted")
            metadata_store = SQLiteMetadataStore(tmp_path / "metadata.sqlite")
        runtime = _runtime(metadata_store)
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}, "interfaces": {"api": True, "mcp": True}},
        runtime=runtime,
    )
    app = create_app(
        config={
            "interfaces": {"api": True, "mcp": True},
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
            **(config or {}),
        },
        ingestion_agent=agent,
    )
    return TestClient(app)


def _auth_client(tmp_path: Path) -> TestClient:
    os.environ["AMH_TOKEN_HASH_SECRET"] = "test-token-secret"
    config = {
        "api": {"auth": "bearer_token"},
        "interfaces": {"api": True, "mcp": True},
        "paths": {"data_dir": str(tmp_path / "data")},
        "providers": {"embeddings": "local", "vector_db": "in_memory"},
    }
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite")
    store.create_auth_token(owner_id="owner-a", token="token-a")
    store.create_auth_token(owner_id="owner-b", token="token-b")
    store.create_project(project_id="shared-interop", owner_id="owner-a", name="Shared")
    store.add_project_member(
        project_id="shared-interop", user_id="owner-b", role=PROJECT_ROLE_WRITER
    )
    return _client(config=config, metadata_store=store)


def _assert_public_memory(memory: dict[str, Any], expected: dict[str, Any]) -> None:
    assert memory["id"] == expected["id"]
    assert memory["source"] == expected["source"]
    assert memory["messages"][0]["text"] == expected["messages"][0]["text"]
    assert "hash" not in memory["messages"][0]
    assert "conversation_hash" not in memory["metadata"]


def _assert_search_shape(
    result: dict[str, Any], expected_id: str, *, expect_cursor: bool = False
) -> None:
    assert result["status"] == "ok"
    assert "results" in result
    if expect_cursor:
        assert "cursor" in result
    assert result["results"]
    assert result["results"][0]["id"] == expected_id


def _assert_ask_shape(result: dict[str, Any], expected_id: str) -> None:
    assert result["status"] == "ok"
    assert result["results"]
    assert result["results"][0]["id"] == expected_id
    assert result["citations"]
    assert result["citations"][0]["id"] == expected_id
    assert result["answer"]
    assert result["confidence"] in {"high", "medium", "low"}
    assert result["answer_basis"] in {"direct_memory", "mixed", "fact_layer", "conflict"}


def test_degraded_vector_health_is_consistent_across_api_and_mcp(
    tmp_path: Path,
) -> None:
    runtime = _runtime(
        SQLiteMetadataStore(tmp_path / "metadata.sqlite"),
        health_state={
            "mode": "degraded",
            "metadata_provider": "sqlite",
            "vector_provider": "memory",
            "requested_vector_provider": "redis",
            "vector_fallback_active": True,
            "reasons": ["RuntimeError"],
        },
    )

    with _client(runtime=runtime) as client:
        ready = client.get("/ready")
        headers = _initialize_mcp(client)
        mcp_health = _read_resource(
            client,
            headers,
            request_id=2,
            uri="memory://health",
        )

    assert ready.status_code == 200
    assert "redis-secret" not in ready.text
    ready_payload = ready.json()
    assert ready_payload["status"] == "ok"
    assert ready_payload["mode"] == "degraded"
    assert ready_payload["health"]["requested_vector_provider"] == "redis"
    assert ready_payload["health"]["vector_provider"] == "memory"
    assert ready_payload["health"]["vector_fallback_active"] is True

    assert "redis-secret" not in json.dumps(mcp_health)
    assert mcp_health["status"] == "ok"
    assert mcp_health["mode"] == "degraded"
    assert mcp_health["health"]["requested_vector_provider"] == "redis"
    assert mcp_health["health"]["vector_provider"] == "memory"
    assert mcp_health["health"]["vector_fallback_active"] is True


def test_api_insert_can_be_read_through_mcp(tmp_path: Path) -> None:
    payload = _conversation(text="The API to MCP interop phrase is amber bridge.")

    with _client(tmp_path=tmp_path) as client:
        insert = client.post("/memory/insert", json=payload)
        headers = _initialize_mcp(client)
        search = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments={"query": "amber bridge", "top_k": 5},
        )
        retrieve = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_retrieve",
            arguments={"id": payload["id"]},
        )
        ask = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_ask",
            arguments={"question": "What is the API to MCP interop phrase?", "top_k": 5},
        )

    assert insert.status_code == 200
    assert insert.json()["status"] == "ok"
    _assert_search_shape(search, payload["id"], expect_cursor=True)
    assert retrieve["status"] == "ok"
    _assert_public_memory(retrieve["memory"], payload)
    _assert_ask_shape(ask, payload["id"])


def test_mcp_insert_can_be_read_through_api(tmp_path: Path) -> None:
    payload = _conversation(text="The MCP to API interop phrase is blue harbor.")

    with _client(tmp_path=tmp_path) as client:
        headers = _initialize_mcp(client)
        insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        search = client.post("/memory/search", json={"query": "blue harbor", "top_k": 5})
        retrieve = client.post("/memory/retrieve", json={"id": payload["id"]})
        ask = client.post(
            "/memory/ask",
            json={"question": "What is the MCP to API interop phrase?", "top_k": 5},
        )

    assert insert["status"] == "ok"
    assert insert["id"] == payload["id"]
    assert search.status_code == 200
    _assert_search_shape(search.json(), payload["id"])
    assert retrieve.status_code == 200
    _assert_public_memory(retrieve.json()["memory"], payload)
    assert ask.status_code == 200
    _assert_ask_shape(ask.json(), payload["id"])


def test_api_project_insert_can_be_read_through_mcp_with_same_project(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="The shared API to MCP project phrase is copper atlas.")

    with _auth_client(tmp_path) as client:
        owner_headers = {"Authorization": "Bearer token-a"}
        insert = client.post(
            "/memory/insert",
            json={**payload, "project_id": "shared-interop"},
            headers=owner_headers,
        )
        headers = _initialize_mcp(client, token="token-a")
        search = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments={
                "query": "copper atlas",
                "top_k": 5,
                "project_id": "shared-interop",
            },
        )
        retrieve = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_retrieve",
            arguments={"id": payload["id"], "project_id": "shared-interop"},
        )
        ask = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_ask",
            arguments={
                "question": "What is the shared API to MCP project phrase?",
                "top_k": 5,
                "project_id": "shared-interop",
            },
        )

    assert insert.status_code == 200
    _assert_search_shape(search, payload["id"], expect_cursor=True)
    assert retrieve["status"] == "ok"
    _assert_public_memory(retrieve["memory"], payload)
    _assert_ask_shape(ask, payload["id"])


def test_mcp_project_insert_can_be_read_through_api_with_same_project(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="The shared MCP to API project phrase is silver quay.")

    with _auth_client(tmp_path) as client:
        headers = _initialize_mcp(client, token="token-a")
        insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload, "project_id": "shared-interop"},
        )
        owner_headers = {"Authorization": "Bearer token-a"}
        search = client.post(
            "/memory/search",
            json={"query": "silver quay", "top_k": 5, "project_id": "shared-interop"},
            headers=owner_headers,
        )
        retrieve = client.post(
            "/memory/retrieve",
            json={"id": payload["id"], "project_id": "shared-interop"},
            headers=owner_headers,
        )
        ask = client.post(
            "/memory/ask",
            json={
                "question": "What is the shared MCP to API project phrase?",
                "top_k": 5,
                "project_id": "shared-interop",
            },
            headers=owner_headers,
        )

    assert insert["status"] == "ok"
    _assert_search_shape(search.json(), payload["id"])
    assert retrieve.status_code == 200
    _assert_public_memory(retrieve.json()["memory"], payload)
    assert ask.status_code == 200
    _assert_ask_shape(ask.json(), payload["id"])


def test_api_bearer_insert_is_readable_by_mcp_same_owner_and_hidden_from_other_owner(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="Owner A API to MCP private phrase is green citadel.")

    with _auth_client(tmp_path) as client:
        owner_a = {"Authorization": "Bearer token-a"}
        insert = client.post("/memory/insert", json=payload, headers=owner_a)
        headers_a = _initialize_mcp(client, token="token-a")
        headers_b = _initialize_mcp(client, token="token-b")
        search_a = _call_tool(
            client,
            headers_a,
            request_id=2,
            name="memory_search",
            arguments={"query": "green citadel", "top_k": 5},
        )
        retrieve_a = _call_tool(
            client,
            headers_a,
            request_id=3,
            name="memory_retrieve",
            arguments={"id": payload["id"]},
        )
        ask_a = _call_tool(
            client,
            headers_a,
            request_id=4,
            name="memory_ask",
            arguments={"question": "What is Owner A's private phrase?", "top_k": 5},
        )
        search_b = _call_tool(
            client,
            headers_b,
            request_id=5,
            name="memory_search",
            arguments={"query": "green citadel", "top_k": 5},
        )
        retrieve_b = _call_tool(
            client,
            headers_b,
            request_id=6,
            name="memory_retrieve",
            arguments={"id": payload["id"]},
        )

    assert insert.status_code == 200
    _assert_search_shape(search_a, payload["id"], expect_cursor=True)
    assert retrieve_a["status"] == "ok"
    _assert_ask_shape(ask_a, payload["id"])
    assert search_b["status"] == "ok"
    assert payload["id"] not in {row["id"] for row in search_b["results"]}
    assert retrieve_b["status"] == "not_found"


def test_mcp_bearer_insert_is_readable_by_api_same_owner_and_hidden_from_other_owner(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="Owner A MCP to API private phrase is violet engine.")

    with _auth_client(tmp_path) as client:
        headers = _initialize_mcp(client, token="token-a")
        insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        owner_a = {"Authorization": "Bearer token-a"}
        owner_b = {"Authorization": "Bearer token-b"}
        search_a = client.post(
            "/memory/search",
            json={"query": "violet engine", "top_k": 5},
            headers=owner_a,
        )
        retrieve_a = client.post(
            "/memory/retrieve", json={"id": payload["id"]}, headers=owner_a
        )
        ask_a = client.post(
            "/memory/ask",
            json={"question": "What is Owner A's private phrase?", "top_k": 5},
            headers=owner_a,
        )
        search_b = client.post(
            "/memory/search",
            json={"query": "violet engine", "top_k": 5},
            headers=owner_b,
        )
        retrieve_b = client.post(
            "/memory/retrieve", json={"id": payload["id"]}, headers=owner_b
        )

    assert insert["status"] == "ok"
    _assert_search_shape(search_a.json(), payload["id"])
    assert retrieve_a.status_code == 200
    _assert_public_memory(retrieve_a.json()["memory"], payload)
    assert ask_a.status_code == 200
    _assert_ask_shape(ask_a.json(), payload["id"])
    assert search_b.status_code == 200
    assert payload["id"] not in {row["id"] for row in search_b.json()["results"]}
    assert retrieve_b.status_code == 404


def test_api_and_mcp_response_shapes_share_public_fields(tmp_path: Path) -> None:
    payload = _conversation(text="The shared response shape phrase is golden compass.")

    with _client(tmp_path=tmp_path) as client:
        client.post("/memory/insert", json=payload)
        headers = _initialize_mcp(client)
        api_search = client.post(
            "/memory/search", json={"query": "golden compass", "top_k": 5}
        ).json()
        mcp_search = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments={"query": "golden compass", "top_k": 5},
        )
        api_retrieve = client.post("/memory/retrieve", json={"id": payload["id"]}).json()
        mcp_retrieve = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_retrieve",
            arguments={"id": payload["id"]},
        )
        api_ask = client.post(
            "/memory/ask",
            json={"question": "What is the shared response shape phrase?", "top_k": 5},
        ).json()
        mcp_ask = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_ask",
            arguments={"question": "What is the shared response shape phrase?", "top_k": 5},
        )

    assert set(api_search).issuperset({"status", "results"})
    assert set(mcp_search).issuperset({"status", "results", "cursor"})
    assert api_search["results"][0]["id"] == mcp_search["results"][0]["id"] == payload["id"]
    assert set(api_retrieve).issuperset({"status", "memory"})
    assert set(mcp_retrieve).issuperset({"status", "id", "memory"})
    assert api_retrieve["memory"]["id"] == mcp_retrieve["memory"]["id"] == payload["id"]
    for result in (api_ask, mcp_ask):
        assert set(result).issuperset(
            {"status", "results", "answer", "citations", "confidence", "answer_basis"}
        )
        assert result["results"][0]["id"] == payload["id"]
