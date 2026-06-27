from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import SQLiteMetadataStore, _default_project_id
from memory.config import ensure_token_hash_secret, parse_config


def _base_config(tmp_path: Path, *, auth: str = "none") -> dict[str, Any]:
    config: dict[str, Any] = {
        "api": {"auth": auth},
        "interfaces": {"api": True, "mcp": True},
        "paths": {"data_dir": str(tmp_path / "data")},
        "providers": {
            "embeddings": "local",
            "metadata_db": "sqlite",
            "vector_db": "lancedb",
        },
        "storage": {"vector": {"allow_fallback": False}},
    }
    if auth == "oauth_resource_server":
        config["api"] = {
            "auth": "oauth_resource_server",
            "public_base_url": "https://memory.example.com",
            "oauth": {
                "authorization_servers": ["https://auth.example.com"],
                "issuer": "https://auth.example.com",
                "jwt_secret": "test-secret",
            },
        }
    return config


def _conversation(*, text: str, memory_id: str | None = None) -> dict[str, Any]:
    return {
        "id": memory_id or str(uuid4()),
        "source": "pytest",
        "timestamp": "2026-06-27T00:00:00Z",
        "title": "Auth mode transition",
        "messages": [{"role": "user", "text": text}],
    }


def _seed_bearer_token(tmp_path: Path, *, owner_id: str = "owner-a", token: str = "token-a") -> None:
    config = parse_config(_base_config(tmp_path, auth="bearer_token"))
    ensure_token_hash_secret(config)
    store = SQLiteMetadataStore(Path(config.paths.data_dir) / "metadata.sqlite3")
    store.create_auth_token(owner_id=owner_id, token=token)


def _client(config: dict[str, Any]) -> TestClient:
    return TestClient(create_app(config=config))


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


def _base64url_json(payload: dict[str, object]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _base64url(raw)


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_bearer_owner_default_data_is_not_readable_after_restart_with_auth_none(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AMH_TOKEN_HASH_SECRET", "transition-secret")
    _seed_bearer_token(tmp_path)
    payload = _conversation(text="My favorite launch phrase is owner amber.")

    with _client(_base_config(tmp_path, auth="bearer_token")) as client:
        insert = client.post(
            "/memory/insert",
            json=payload,
            headers={"Authorization": "Bearer token-a"},
        )
        assert insert.status_code == 200, insert.text

    with _client(_base_config(tmp_path, auth="none")) as client:
        search = client.post("/memory/search", json={"query": "owner amber"})
        retrieve = client.post("/memory/retrieve", json={"id": payload["id"]})
        ask = client.post("/memory/ask", json={"question": "What is my favorite launch phrase?"})

    assert search.status_code == 200
    assert search.json()["results"] == []
    assert retrieve.status_code == 404
    assert ask.status_code == 200
    assert ask.json()["answer_basis"] == "not_found"
    assert "owner amber" not in ask.json()["answer"].lower()


def test_bearer_owner_default_data_is_not_readable_through_mcp_after_auth_none_restart(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AMH_TOKEN_HASH_SECRET", "transition-secret")
    _seed_bearer_token(tmp_path)
    payload = _conversation(text="My command name is owner only bell.")

    with _client(_base_config(tmp_path, auth="bearer_token")) as client:
        insert = client.post(
            "/memory/insert",
            json=payload,
            headers={"Authorization": "Bearer token-a"},
        )
        assert insert.status_code == 200, insert.text

    with _client(_base_config(tmp_path, auth="none")) as client:
        headers = _initialize_mcp(client)
        search = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments={"query": "owner only bell"},
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
            arguments={"question": "What is my command name?"},
        )

    assert search["results"] == []
    assert retrieve["status"] == "not_found"
    assert ask["answer_basis"] == "not_found"
    assert "owner only bell" not in ask["answer"].lower()


def test_auth_none_rejects_explicit_authenticated_owner_default_project(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AMH_TOKEN_HASH_SECRET", "transition-secret")
    _seed_bearer_token(tmp_path)
    owner_default_project = _default_project_id("owner-a")

    with _client(_base_config(tmp_path, auth="none")) as client:
        search = client.post(
            "/memory/search",
            json={"query": "anything", "project_id": owner_default_project},
        )
        retrieve = client.post(
            "/memory/retrieve",
            json={"id": str(uuid4()), "project_id": owner_default_project},
        )

    assert search.status_code == 403
    assert retrieve.status_code == 403


def test_oauth_owner_data_is_not_readable_after_restart_with_auth_none(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="The OAuth-only recall code is copper iris.")
    oauth_headers = {"Authorization": f"Bearer {_oauth_token(sub='owner-a')}"}

    with _client(_base_config(tmp_path, auth="oauth_resource_server")) as client:
        insert = client.post("/memory/insert", json=payload, headers=oauth_headers)
        assert insert.status_code == 200, insert.text

    with _client(_base_config(tmp_path, auth="none")) as client:
        search = client.post("/memory/search", json={"query": "copper iris"})
        headers = _initialize_mcp(client)
        mcp_search = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments={"query": "copper iris"},
        )
        mcp_retrieve = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_retrieve",
            arguments={"id": payload["id"]},
        )

    assert search.status_code == 200
    assert search.json()["results"] == []
    assert mcp_search["results"] == []
    assert mcp_retrieve["status"] == "not_found"


def test_authenticated_shared_project_data_is_not_readable_after_auth_none_restart(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AMH_TOKEN_HASH_SECRET", "transition-secret")
    _seed_bearer_token(tmp_path)
    config = parse_config(_base_config(tmp_path, auth="bearer_token"))
    store = SQLiteMetadataStore(Path(config.paths.data_dir) / "metadata.sqlite3")
    store.create_project(project_id="shared-transition", owner_id="owner-a", name="Shared")
    payload = {
        **_conversation(text="The shared project phrase is sealed brass."),
        "project_id": "shared-transition",
    }

    with _client(_base_config(tmp_path, auth="bearer_token")) as client:
        insert = client.post(
            "/memory/insert",
            json=payload,
            headers={"Authorization": "Bearer token-a"},
        )
        assert insert.status_code == 200, insert.text

    with _client(_base_config(tmp_path, auth="none")) as client:
        search = client.post("/memory/search", json={"query": "sealed brass"})
        explicit_project = client.post(
            "/memory/search",
            json={"query": "sealed brass", "project_id": "shared-transition"},
        )

    assert search.status_code == 200
    assert search.json()["results"] == []
    assert explicit_project.status_code == 403


def test_unauthenticated_local_default_data_remains_readable_after_auth_none_restart(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="The local default restart phrase is plain jade.")

    with _client(_base_config(tmp_path, auth="none")) as client:
        insert = client.post("/memory/insert", json=payload)
        assert insert.status_code == 200, insert.text

    with _client(_base_config(tmp_path, auth="none")) as client:
        search = client.post("/memory/search", json={"query": "plain jade"})
        retrieve = client.post("/memory/retrieve", json={"id": payload["id"]})
        headers = _initialize_mcp(client)
        mcp_search = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_search",
            arguments={"query": "plain jade"},
        )

    assert search.status_code == 200
    assert search.json()["results"][0]["id"] == payload["id"]
    assert retrieve.status_code == 200
    assert retrieve.json()["memory"]["id"] == payload["id"]
    assert mcp_search["results"][0]["id"] == payload["id"]


def test_bearer_owner_facts_are_not_readable_after_restart_with_auth_none(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AMH_TOKEN_HASH_SECRET", "transition-secret")
    _seed_bearer_token(tmp_path)
    payload = _conversation(text="I own a teal Jazzmaster.")

    with _client(_base_config(tmp_path, auth="bearer_token")) as client:
        insert = client.post(
            "/memory/insert",
            json=payload,
            headers={"Authorization": "Bearer token-a"},
        )
        assert insert.status_code == 200, insert.text

    with _client(_base_config(tmp_path, auth="none")) as client:
        facts = client.post(
            "/memory/facts/search",
            json={"subject": "user", "predicate": "owns_guitar"},
        )
        profile = client.post("/memory/profile/get", json={"subject": "user"})
        ask = client.post("/memory/ask", json={"question": "What guitar do I own?"})

    assert facts.status_code == 200
    assert facts.json()["results"] == []
    assert profile.status_code == 200
    assert profile.json()["facts"] == []
    assert ask.status_code == 200
    assert ask.json()["answer_basis"] == "not_found"
