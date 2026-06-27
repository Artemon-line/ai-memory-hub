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
    os.environ["AMH_TOKEN_HASH_SECRET"] = "fact-profile-interop-secret"
    config = parse_config(_config(tmp_path, auth="bearer_token"))
    ensure_token_hash_secret(config)
    store = SQLiteMetadataStore(Path(config.paths.data_dir) / "metadata.sqlite3")
    store.create_auth_token(owner_id="owner-a", token="token-a")
    store.create_auth_token(owner_id="owner-b", token="token-b")
    store.create_project(project_id="fact-shared", owner_id="owner-a", name="Fact Shared")
    return TestClient(create_app(config=config))


def _client(tmp_path: Path, *, auth: str = "none") -> TestClient:
    return TestClient(create_app(config=_config(tmp_path, auth=auth)))


def _conversation(
    *,
    text: str,
    memory_id: str | None = None,
    source: str = "pytest",
) -> dict[str, Any]:
    return {
        "id": memory_id or str(uuid4()),
        "source": source,
        "messages": [{"role": "user", "text": text}],
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
    raise AssertionError(f"No SSE data line found in response: {response_text}")


def _tool_payload(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    payload = _event_result_json(response.text)
    content = payload["result"]["content"]
    assert content and content[0]["type"] == "text"
    return json.loads(content[0]["text"])


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


def _fact_objects(payload: dict[str, Any]) -> list[str]:
    return [str(row["object"]) for row in payload["results"]]


def test_api_inserted_facts_are_readable_through_mcp_same_owner_and_project(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="My name is Alex Prism. I own a cherry Gibson guitar.")
    headers = {"Authorization": "Bearer token-a"}

    with _auth_client(tmp_path) as client:
        insert = client.post(
            "/memory/insert",
            json={**payload, "project_id": "fact-shared"},
            headers=headers,
        )
        mcp_headers = _initialize_mcp(client, token="token-a")
        facts = _call_tool(
            client,
            mcp_headers,
            request_id=2,
            name="memory_fact_search",
            arguments={
                "subject": "user",
                "predicate": "owns_guitar",
                "project_id": "fact-shared",
            },
        )
        profile = _call_tool(
            client,
            mcp_headers,
            request_id=3,
            name="memory_profile_get",
            arguments={"subject": "user", "project_id": "fact-shared"},
        )

    assert insert.status_code == 200, insert.text
    assert facts["status"] == "ok"
    assert _fact_objects(facts) == ["a cherry Gibson guitar"]
    assert {fact["predicate"] for fact in profile["facts"]} >= {"profile_name", "owns_guitar"}


def test_mcp_inserted_facts_are_readable_through_api_same_owner_and_project(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="My name is Morgan Slate. I own a blue Jazzmaster guitar.")
    headers = {"Authorization": "Bearer token-a"}

    with _auth_client(tmp_path) as client:
        mcp_headers = _initialize_mcp(client, token="token-a")
        insert = _call_tool(
            client,
            mcp_headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload, "project_id": "fact-shared"},
        )
        facts = client.post(
            "/memory/facts/search",
            json={
                "subject": "user",
                "predicate": "owns_guitar",
                "project_id": "fact-shared",
            },
            headers=headers,
        )
        profile = client.post(
            "/memory/profile/get",
            json={"subject": "user", "project_id": "fact-shared"},
            headers=headers,
        )

    assert insert["status"] == "ok"
    assert facts.status_code == 200, facts.text
    assert _fact_objects(facts.json()) == ["a blue Jazzmaster guitar"]
    assert profile.status_code == 200, profile.text
    assert {fact["predicate"] for fact in profile.json()["facts"]} >= {
        "profile_name",
        "owns_guitar",
    }


def test_cross_owner_fact_and_profile_queries_do_not_leak(tmp_path: Path) -> None:
    payload = _conversation(text="My name is Ada Cipher. I own a silver Gibson guitar.")
    owner_a_headers = {"Authorization": "Bearer token-a"}
    owner_b_headers = {"Authorization": "Bearer token-b"}

    with _auth_client(tmp_path) as client:
        insert = client.post("/memory/insert", json=payload, headers=owner_a_headers)
        facts_a = client.post(
            "/memory/facts/search",
            json={"subject": "user", "predicate": "owns_guitar"},
            headers=owner_a_headers,
        )
        facts_b = client.post(
            "/memory/facts/search",
            json={"subject": "user", "predicate": "owns_guitar"},
            headers=owner_b_headers,
        )
        profile_b = client.post(
            "/memory/profile/get", json={"subject": "user"}, headers=owner_b_headers
        )
        mcp_b = _initialize_mcp(client, token="token-b")
        mcp_facts_b = _call_tool(
            client,
            mcp_b,
            request_id=2,
            name="memory_fact_search",
            arguments={"subject": "user", "predicate": "owns_guitar"},
        )
        mcp_profile_b = _call_tool(
            client,
            mcp_b,
            request_id=3,
            name="memory_profile_get",
            arguments={"subject": "user"},
        )

    assert insert.status_code == 200, insert.text
    assert _fact_objects(facts_a.json()) == ["a silver Gibson guitar"]
    assert facts_b.status_code == 200
    assert facts_b.json()["results"] == []
    assert profile_b.status_code == 200
    assert profile_b.json()["facts"] == []
    assert mcp_facts_b["results"] == []
    assert mcp_profile_b["facts"] == []


def test_auth_none_restart_cannot_read_owner_scoped_facts_through_api_or_mcp(
    tmp_path: Path,
) -> None:
    payload = _conversation(text="My name is Rowan Vault. I own a violet Gibson guitar.")

    with _auth_client(tmp_path) as client:
        insert = client.post(
            "/memory/insert",
            json=payload,
            headers={"Authorization": "Bearer token-a"},
        )
    assert insert.status_code == 200, insert.text

    with _client(tmp_path, auth="none") as client:
        api_facts = client.post(
            "/memory/facts/search",
            json={"subject": "user", "predicate": "owns_guitar"},
        )
        api_profile = client.post("/memory/profile/get", json={"subject": "user"})
        mcp_headers = _initialize_mcp(client)
        mcp_facts = _call_tool(
            client,
            mcp_headers,
            request_id=2,
            name="memory_fact_search",
            arguments={"subject": "user", "predicate": "owns_guitar"},
        )
        mcp_profile = _call_tool(
            client,
            mcp_headers,
            request_id=3,
            name="memory_profile_get",
            arguments={"subject": "user"},
        )

    assert api_facts.status_code == 200
    assert api_facts.json()["results"] == []
    assert api_profile.status_code == 200
    assert api_profile.json()["facts"] == []
    assert mcp_facts["results"] == []
    assert mcp_profile["facts"] == []


def test_supersession_through_mcp_is_visible_through_api(tmp_path: Path) -> None:
    old_payload = _conversation(text="I own a cherry Gibson guitar.")
    new_payload = _conversation(text="I own a TV yellow Gibson guitar.")
    headers = {"Authorization": "Bearer token-a"}

    with _auth_client(tmp_path) as client:
        old_insert = client.post("/memory/insert", json=old_payload, headers=headers)
        new_insert = client.post("/memory/insert", json=new_payload, headers=headers)
        before = client.post(
            "/memory/facts/search",
            json={"subject": "user", "predicate": "owns_guitar", "include_superseded": True},
            headers=headers,
        )
        old_fact = next(fact for fact in before.json()["results"] if "cherry" in fact["object"])
        new_fact = next(fact for fact in before.json()["results"] if "TV yellow" in fact["object"])
        mcp_headers = _initialize_mcp(client, token="token-a")
        supersede = _call_tool(
            client,
            mcp_headers,
            request_id=2,
            name="memory_fact_supersede",
            arguments={"fact_id": old_fact["id"], "superseded_by": new_fact["id"]},
        )
        after = client.post(
            "/memory/facts/search",
            json={"subject": "user", "predicate": "owns_guitar", "include_superseded": True},
            headers=headers,
        )
        active_profile = client.post(
            "/memory/profile/get",
            json={"subject": "user", "predicate": "owns_guitar"},
            headers=headers,
        )

    assert old_insert.status_code == 200, old_insert.text
    assert new_insert.status_code == 200, new_insert.text
    assert supersede["status"] == "ok"
    superseded_old = next(fact for fact in after.json()["results"] if fact["id"] == old_fact["id"])
    assert superseded_old["superseded_by"] == new_fact["id"]
    assert [fact["object"] for fact in active_profile.json()["facts"]] == [
        "a TV yellow Gibson guitar"
    ]
