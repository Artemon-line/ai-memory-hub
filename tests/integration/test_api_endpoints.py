from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import (
    PROJECT_ROLE_READER,
    PROJECT_ROLE_WRITER,
    SQLiteMetadataStore,
)
from memory.backend.vector_store import InMemoryVectorStore
from memory.config import ensure_token_hash_secret, parse_config
from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.observability.metrics import metrics


class StubEmbedder(mvp_ingestion.EmbeddingProvider):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]
    dimension: int = 32



class StubMetadataStore:
    def __init__(self):
        self.rows: dict[str, dict[str, object]] = {}
        self.auth_tokens: dict[str, str] = {}

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
    runtime = _runtime()
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}, "interfaces": {"api": "true"}},
        runtime=runtime,
    )
    app = create_app(
        config={"providers": {"embeddings": "local", "vector_db": "in_memory"}},
        ingestion_agent=agent,
    )
    return TestClient(app)


def _auth_client() -> tuple[TestClient, StubMetadataStore]:
    runtime = _runtime()
    store = runtime.metadata_store
    store.auth_tokens = {"token-a": "owner-a", "token-b": "owner-b"}
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}, "interfaces": {"api": "true"}},
        runtime=runtime,
    )
    app = create_app(
        config={
            "api": {"auth": "bearer_token"},
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
        },
        ingestion_agent=agent,
    )
    return TestClient(app), store


def _oauth_client() -> TestClient:
    runtime = _runtime()
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}, "interfaces": {"api": "true"}},
        runtime=runtime,
    )
    app = create_app(
        config={
            "api": {
                "auth": "oauth_resource_server",
                "public_base_url": "https://memory.example.com",
                "oauth": {
                    "authorization_servers": ["https://auth.example.com"],
                    "issuer": "https://auth.example.com",
                    "jwt_secret": "test-secret",
                },
            },
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
        },
        ingestion_agent=agent,
    )
    return TestClient(app)


def _oauth_token(**claims: object) -> str:
    now = int(time.time())
    payload = {
        "sub": "owner-a",
        "iss": "https://auth.example.com",
        "aud": "https://memory.example.com/mcp",
        "exp": now + 300,
        "scope": "memory:read memory:write",
        **claims,
    }
    return _sign_hs256(payload, "test-secret")


def _sign_hs256(payload: dict[str, object], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_part = _base64url_json(header)
    payload_part = _base64url_json(payload)
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_base64url(signature)}"


def _mcp_result_payload(response_text: str) -> dict[str, object]:
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line.removeprefix("data: "))
        if "result" not in payload:
            continue
        content = payload["result"]["content"]
        assert isinstance(content, list) and content
        text = content[0]["text"]
        assert isinstance(text, str)
        return json.loads(text)
    raise AssertionError(f"No MCP result payload found in response: {response_text}")


def _base64url_json(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _base64url(raw)


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sqlite_auth_client(tmp_path) -> tuple[TestClient, SQLiteMetadataStore]:
    config = parse_config(
        {
            "api": {"auth": "bearer_token"},
            "paths": {"data_dir": str(tmp_path / "data")},
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
        }
    )
    ensure_token_hash_secret(config)
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    store.create_auth_token(owner_id="owner-a", token="token-a")
    store.create_auth_token(owner_id="owner-b", token="token-b")
    store.create_auth_token(owner_id="owner-c", token="token-c")
    store.create_auth_token(owner_id="owner-d", token="token-d")
    store.create_auth_token(owner_id="owner-a", token="token-read", scopes=["memory:read"])
    store.create_project(
        project_id="shared-321",
        owner_id="owner-a",
        name="Shared 321",
    )
    store.add_project_member(
        project_id="shared-321", user_id="owner-b", role=PROJECT_ROLE_WRITER
    )
    store.add_project_member(
        project_id="shared-321", user_id="owner-d", role=PROJECT_ROLE_READER
    )
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=StubEmbedder(),
        metadata_store=store,
        vector_store=InMemoryVectorStore(dimension=1),
        health_state={"mode": "ok", "vector_fallback_active": False},
    )
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}, "interfaces": {"api": "true"}},
        runtime=runtime,
    )
    app = create_app(
        config=config,
        ingestion_agent=agent,
    )
    return TestClient(app), store


def test_bearer_auth_enforces_stored_token_scopes(tmp_path) -> None:
    client, _ = _sqlite_auth_client(tmp_path)
    read_only_headers = {"Authorization": "Bearer token-read"}

    search = client.post(
        "/memory/search",
        json={"query": "hello"},
        headers=read_only_headers,
    )
    insert = client.post(
        "/memory/insert",
        json=_conversation(),
        headers=read_only_headers,
    )

    assert search.status_code == 200
    assert insert.status_code == 403
    assert 'error="insufficient_scope"' in insert.headers["www-authenticate"]
    assert 'scope="memory:write"' in insert.headers["www-authenticate"]


def test_memory_insert_200() -> None:
    client = _client()
    response = client.post("/memory/insert", json=_conversation())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_and_ready_are_public_and_redacted() -> None:
    metrics.reset()
    client = _client()

    health = client.get("/health")
    ready = client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code == 200
    assert health.json()["status"] == "ok"
    assert ready.json()["status"] == "ok"
    assert ready.json()["mode"] == "ok"
    assert ready.json()["metadata_provider"] == "sqlite"
    assert ready.json()["vector_provider"] == "memory"
    assert ready.json()["embedding_provider"] == "local"
    assert ready.json()["telemetry_enabled"] is False
    assert "content_hash" not in str(health.json())
    assert "content_hash" not in str(ready.json())
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["memory_api_requests_total{route=/health,status_code=200}"] == 1
    assert snapshot["counters"]["memory_api_requests_total{route=/ready,status_code=200}"] == 1
    assert snapshot["gauges"]["memory_health_mode{mode=ok}"] == 1.0


def test_observability_endpoint_is_public_and_redacted() -> None:
    client = _client()

    response = client.get("/observability")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["observability"]["logging"]["format"] == "text"
    assert body["observability"]["telemetry_enabled"] is False
    assert body["observability"]["tracing"] == {
        "enabled": False,
        "configured": False,
        "exporter": None,
        "protocol": None,
        "error_type": None,
    }
    assert body["observability"]["metrics"] == {
        "enabled": False,
        "configured": False,
        "exporter": None,
        "protocol": None,
        "error_type": None,
    }
    assert body["observability"]["embedding_readiness_probe"] is False
    assert body["runtime"]["mode"] == "ok"
    assert "content_hash" not in str(body)


def test_configured_request_id_header_is_echoed() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}, "interfaces": {"api": "true"}},
        runtime=runtime,
    )
    app = create_app(
        config={
            "observability": {"logging": {"request_id_header": "x-correlation-id"}},
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
        },
        ingestion_agent=agent,
    )
    client = TestClient(app)

    response = client.get("/health", headers={"x-correlation-id": "release-docs-check"})

    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == "release-docs-check"
    assert "x-request-id" not in response.headers


def test_bearer_auth_rejects_missing_and_invalid_tokens() -> None:
    client, _ = _auth_client()

    health = client.get("/health")
    missing = client.post("/memory/search", json={"query": "hello"})
    invalid = client.post(
        "/memory/search",
        json={"query": "hello"},
        headers={"Authorization": "Bearer wrong"},
    )
    mcp_missing = client.post("/mcp/")

    assert health.status_code == 200
    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert mcp_missing.status_code == 401
    assert missing.headers["www-authenticate"].startswith("Bearer")


def test_oauth_protected_resource_metadata_is_public_and_secret_free() -> None:
    client = _oauth_client()

    root_response = client.get("/.well-known/oauth-protected-resource")
    response = client.get("/.well-known/oauth-protected-resource/mcp")

    assert root_response.status_code == 200
    assert root_response.json()["resource"] == "https://memory.example.com/mcp"
    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == "https://memory.example.com/mcp"
    assert body["authorization_servers"] == ["https://auth.example.com"]
    assert "memory:read" in body["scopes_supported"]
    assert "test-secret" not in str(body)


def test_oauth_auth_rejects_missing_wrong_audience_and_query_tokens(
    caplog,
) -> None:
    client = _oauth_client()
    wrong_audience = _oauth_token(aud="https://other.example.com/mcp")
    expired = _oauth_token(exp=int(time.time()) - 1)
    query_token_value = _oauth_token()

    missing = client.post("/memory/search", json={"query": "hello"})
    wrong = client.post(
        "/memory/search",
        json={"query": "hello"},
        headers={"Authorization": f"Bearer {wrong_audience}"},
    )
    with caplog.at_level(logging.INFO):
        query_token = client.post(
            f"/memory/search?access_token={query_token_value}",
            json={"query": "hello"},
        )
    expired_response = client.post(
        "/memory/search",
        json={"query": "hello"},
        headers={"Authorization": f"Bearer {expired}"},
    )

    for response in (missing, wrong, query_token, expired_response):
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert 'resource_metadata="https://memory.example.com/.well-known/oauth-protected-resource"' in challenge
        assert "memory:read" in challenge
    assert wrong_audience not in str(wrong.json())
    assert query_token_value not in caplog.text
    assert wrong_audience not in caplog.text


def test_oauth_auth_accepts_valid_token_and_enforces_scopes() -> None:
    client = _oauth_client()
    read_token = _oauth_token(scope="memory:read")
    full_token = _oauth_token()

    read_response = client.post(
        "/memory/search",
        json={"query": "hello"},
        headers={"Authorization": f"Bearer {read_token}"},
    )
    write_response = client.post(
        "/memory/insert",
        json=_conversation(),
        headers={"Authorization": f"Bearer {read_token}"},
    )
    full_response = client.post(
        "/memory/insert",
        json=_conversation(),
        headers={"Authorization": f"Bearer {full_token}"},
    )

    assert read_response.status_code == 200
    assert write_response.status_code == 403
    challenge = write_response.headers["www-authenticate"]
    assert 'error="insufficient_scope"' in challenge
    assert 'scope="memory:write"' in challenge
    assert full_response.status_code == 200


def test_oauth_auth_protects_mcp_initialize() -> None:
    initialize_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.1"},
        },
    }

    with _oauth_client() as client:
        missing = client.post("/mcp/", json=initialize_payload)
        valid = client.post(
            "/mcp/",
            json=initialize_payload,
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {_oauth_token(scope='memory:read')}",
            },
        )

    assert missing.status_code == 401
    assert "/.well-known/oauth-protected-resource/mcp" in missing.headers["www-authenticate"]
    assert valid.status_code == 200
    assert valid.headers.get("mcp-session-id")


def test_oauth_auth_accepts_mcp_tools_list_with_session() -> None:
    initialize_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.1"},
        },
    }
    token = _oauth_token(scope="memory:read")
    base_headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }

    with _oauth_client() as client:
        initialize = client.post("/mcp/", json=initialize_payload, headers=base_headers)
        session_id = initialize.headers.get("mcp-session-id")
        tools_list = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={**base_headers, "Mcp-Session-Id": session_id or ""},
        )

    assert initialize.status_code == 200
    assert session_id
    assert tools_list.status_code == 200
    assert "memory_search" in tools_list.text


def test_oauth_mcp_write_tool_requires_write_scope() -> None:
    initialize_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.1"},
        },
    }
    token = _oauth_token(scope="memory:read")
    base_headers = {
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {token}",
    }
    payload = _conversation()

    with _oauth_client() as client:
        initialize = client.post("/mcp/", json=initialize_payload, headers=base_headers)
        session_id = initialize.headers.get("mcp-session-id")
        insert = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "memory_insert",
                    "arguments": {"conversation_json": payload},
                },
            },
            headers={**base_headers, "Mcp-Session-Id": session_id or ""},
        )
        read_search = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "memory_search",
                    "arguments": {"query": "hello", "top_k": 5},
                },
            },
            headers={**base_headers, "Mcp-Session-Id": session_id or ""},
        )

    assert initialize.status_code == 200
    assert insert.status_code == 200
    insert_payload = _mcp_result_payload(insert.text)
    assert insert_payload["status"] == "error"
    assert insert_payload["error_code"] == "insufficient_scope"
    assert insert_payload["required_scope"] == "memory:write"
    assert read_search.status_code == 200
    assert _mcp_result_payload(read_search.text)["status"] == "ok"


def test_bearer_auth_stamps_owner_and_isolates_memory() -> None:
    client, store = _auth_client()
    owner_a_headers = {"Authorization": "Bearer token-a"}
    owner_b_headers = {"Authorization": "Bearer token-b"}
    owner_a_payload = _conversation()
    owner_a_payload["metadata"]["owner_id"] = "client-supplied"
    owner_a_payload["messages"] = [
        {"role": "user", "text": "I own a blue Gibson guitar."}
    ]
    owner_b_payload = _conversation()
    owner_b_payload["id"] = "11111111-1111-4111-8111-111111111111"
    owner_b_payload["messages"] = [
        {"role": "user", "text": "I own a red Fender guitar."}
    ]

    insert_a = client.post("/memory/insert", json=owner_a_payload, headers=owner_a_headers)
    insert_b = client.post("/memory/insert", json=owner_b_payload, headers=owner_b_headers)
    search_a = client.post("/memory/search", json={"query": "Gibson"}, headers=owner_a_headers)
    search_b = client.post("/memory/search", json={"query": "Gibson"}, headers=owner_b_headers)
    retrieve_cross = client.post(
        "/memory/retrieve", json={"id": owner_a_payload["id"]}, headers=owner_b_headers
    )
    ask_a = client.post(
        "/memory/ask", json={"question": "What guitar do I own?"}, headers=owner_a_headers
    )
    ask_b = client.post(
        "/memory/ask", json={"question": "What guitar do I own?"}, headers=owner_b_headers
    )

    assert insert_a.status_code == 200
    assert insert_b.status_code == 200
    assert store.rows[owner_a_payload["id"]]["metadata"]["owner_id"] == "owner-a"
    assert search_a.json()["results"][0]["id"] == owner_a_payload["id"]
    assert owner_a_payload["id"] not in {row["id"] for row in search_b.json()["results"]}
    assert retrieve_cross.status_code == 404
    assert "blue Gibson" in ask_a.json()["answer"]
    assert "red Fender" in ask_b.json()["answer"]


def test_bearer_auth_exposes_visible_project_helpers(tmp_path) -> None:
    client, _ = _sqlite_auth_client(tmp_path)
    owner_a_headers = {"Authorization": "Bearer token-a"}
    owner_b_headers = {"Authorization": "Bearer token-b"}
    owner_c_headers = {"Authorization": "Bearer token-c"}

    owner_a_projects = client.get("/memory/projects", headers=owner_a_headers)
    owner_b_shared = client.get("/memory/projects/shared-321", headers=owner_b_headers)
    owner_c_shared = client.get("/memory/projects/shared-321", headers=owner_c_headers)
    owner_a_default = client.get("/memory/projects/default", headers=owner_a_headers)

    assert owner_a_projects.status_code == 200
    project_ids = {project["id"] for project in owner_a_projects.json()["results"]}
    assert "shared-321" in project_ids
    default_project = owner_a_default.json()["project"]
    assert default_project["id"] in project_ids
    assert default_project["id"].startswith("private-")
    assert owner_b_shared.status_code == 200
    assert owner_b_shared.json()["project"]["id"] == "shared-321"
    assert owner_b_shared.json()["project"]["role"] == PROJECT_ROLE_WRITER
    assert owner_c_shared.status_code == 403
    assert owner_a_default.status_code == 200
    assert default_project["is_default"] is True

def test_bearer_auth_allows_shared_project_collaboration(tmp_path) -> None:
    client, store = _sqlite_auth_client(tmp_path)
    owner_a_headers = {"Authorization": "Bearer token-a"}
    owner_b_headers = {"Authorization": "Bearer token-b"}
    owner_c_headers = {"Authorization": "Bearer token-c"}
    owner_d_headers = {"Authorization": "Bearer token-d"}
    private_payload = _conversation()
    private_payload["messages"] = [
        {"role": "user", "text": "Jane private note mentions cobalt."}
    ]
    shared_payload = _conversation()
    shared_payload["id"] = "11111111-1111-4111-8111-111111111111"
    shared_payload["project_id"] = "shared-321"
    shared_payload["messages"] = [
        {"role": "user", "text": "Shared project 321 codename is Velvet Lantern."}
    ]

    private_insert = client.post(
        "/memory/insert", json=private_payload, headers=owner_a_headers
    )
    shared_insert = client.post(
        "/memory/insert", json=shared_payload, headers=owner_a_headers
    )
    owner_b_shared_search = client.post(
        "/memory/search",
        json={"query": "Velvet Lantern", "project_id": "shared-321"},
        headers=owner_b_headers,
    )
    owner_b_default_search = client.post(
        "/memory/search", json={"query": "cobalt"}, headers=owner_b_headers
    )
    owner_c_shared_search = client.post(
        "/memory/search",
        json={"query": "Velvet Lantern", "project_id": "shared-321"},
        headers=owner_c_headers,
    )
    reader_write = client.post(
        "/memory/insert",
        json={**_conversation(), "project_id": "shared-321"},
        headers=owner_d_headers,
    )

    assert private_insert.status_code == 200
    assert shared_insert.status_code == 200
    assert store.get(shared_payload["id"])["metadata"]["project_id"] == "shared-321"
    assert owner_b_shared_search.status_code == 200
    assert owner_b_shared_search.json()["results"][0]["id"] == shared_payload["id"]
    assert owner_b_default_search.status_code == 200
    assert owner_b_default_search.json()["results"] == []
    assert owner_c_shared_search.status_code == 403
    assert reader_write.status_code == 403


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


def test_memory_ask_accepts_conversation_filters() -> None:
    client = _client()
    first = _conversation()
    second = _conversation()
    second["id"] = "22222222-2222-4222-8222-222222222222"
    second["source"] = "opencode"
    second["timestamp"] = "2026-01-02T00:00:00Z"
    second["messages"] = [{"role": "user", "text": "hello from opencode"}]
    second["metadata"] = {
        "imported_at": "2026-01-02T00:00:00Z",
        "tags": ["shared"],
        "thread_id": "thread-opencode",
    }
    client.post("/memory/insert", json=first)
    client.post("/memory/insert", json=second)

    ask = client.post(
        "/memory/ask",
        json={
            "question": "hello",
            "top_k": 5,
            "source": "opencode",
            "tags": ["shared"],
            "thread_id": "thread-opencode",
        },
    )

    assert ask.status_code == 200
    body = ask.json()
    assert body["status"] == "ok"
    assert [row["id"] for row in body["results"]] == [second["id"]]


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
