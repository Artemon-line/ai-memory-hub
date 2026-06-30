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


def _config(
    tmp_path: Path, *, auth: str = "none", insert_policy: str = "permissive"
) -> dict[str, Any]:
    return {
        "api": {"auth": auth},
        "interfaces": {"api": True, "mcp": True},
        "memory": {"insert_policy": insert_policy},
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


def _strict_save_intent_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(config=_config(tmp_path, insert_policy="require_save_intent")))


def _review_pending_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(config=_config(tmp_path, insert_policy="review_pending")))


def _conversation(
    *,
    text: str,
    memory_id: str | None = None,
    source: str = "pytest",
    timestamp: str = "2026-06-27T12:00:00Z",
    tags: list[str] | None = None,
    thread_id: str | None = None,
    save_intent: str | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if tags is not None:
        metadata["tags"] = tags
    if thread_id is not None:
        metadata["thread_id"] = thread_id
    if save_intent is not None:
        metadata["save_intent"] = save_intent
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


def test_api_and_mcp_insert_remain_permissive_by_default(tmp_path: Path) -> None:
    api_payload = _conversation(text="Default permissive API insert phrase.")
    mcp_payload = _conversation(text="Default permissive MCP insert phrase.")

    with _client(tmp_path) as client:
        api_insert = client.post("/memory/insert", json=api_payload)
        headers = _initialize_mcp(client)
        mcp_insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": mcp_payload},
        )

    assert api_insert.status_code == 200, api_insert.text
    assert mcp_insert["status"] == "ok"


def test_api_and_mcp_require_save_intent_when_policy_is_strict(tmp_path: Path) -> None:
    secret_text = "save-intent-secret-phrase"

    with _strict_save_intent_client(tmp_path) as client:
        api_insert = client.post(
            "/memory/insert",
            json=_conversation(text=secret_text),
        )
        headers = _initialize_mcp(client)
        mcp_insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": _conversation(text=secret_text)},
        )
        api_search = client.post("/memory/search", json={"query": secret_text})

    assert api_insert.status_code == 400
    assert api_insert.json()["detail"]["error_code"] == "save_intent_required"
    assert secret_text not in api_insert.text
    assert api_search.status_code == 200
    assert api_search.json()["results"] == []
    assert mcp_insert["status"] == "error"
    assert mcp_insert["error_code"] == "save_intent_required"
    assert "metadata.save_intent" in mcp_insert["error_message"]
    assert secret_text not in json.dumps(mcp_insert)


def test_api_and_mcp_accept_known_save_intents_when_policy_is_strict(
    tmp_path: Path,
) -> None:
    allowed = ["explicit_user_request", "user_confirmed", "client_auto_save"]

    with _strict_save_intent_client(tmp_path) as client:
        headers = _initialize_mcp(client)
        for index, save_intent in enumerate(allowed):
            api_insert = client.post(
                "/memory/insert",
                json=_conversation(
                    text=f"API strict save intent {save_intent}",
                    save_intent=save_intent,
                ),
            )
            mcp_insert = _call_tool(
                client,
                headers,
                request_id=index + 2,
                name="memory_insert",
                arguments={
                    "conversation_json": _conversation(
                        text=f"MCP strict save intent {save_intent}",
                        save_intent=save_intent,
                    )
                },
            )
            assert api_insert.status_code == 200, api_insert.text
            assert mcp_insert["status"] == "ok"


def test_api_and_mcp_reject_unknown_save_intent_values(tmp_path: Path) -> None:
    with _strict_save_intent_client(tmp_path) as client:
        api_insert = client.post(
            "/memory/insert",
            json=_conversation(
                text="Unknown save intent API phrase.",
                save_intent="agent_felt_like_it",
            ),
        )
        headers = _initialize_mcp(client)
        mcp_insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={
                "conversation_json": _conversation(
                    text="Unknown save intent MCP phrase.",
                    save_intent="agent_felt_like_it",
                )
            },
        )

    assert api_insert.status_code == 400
    assert api_insert.json()["detail"]["error_code"] == "invalid_save_intent"
    assert mcp_insert["status"] == "error"
    assert mcp_insert["error_code"] == "invalid_save_intent"


def test_review_pending_api_insert_is_hidden_until_approved(tmp_path: Path) -> None:
    phrase = "pending review api guitar phrase"
    payload = _conversation(text=f"I own a {phrase}.")

    with _review_pending_client(tmp_path) as client:
        insert = client.post("/memory/insert", json=payload)
        assert insert.status_code == 200, insert.text
        memory_id = insert.json()["id"]
        assert insert.json()["status"] == "pending_review"

        retrieve = client.post("/memory/retrieve", json={"id": memory_id})
        search = client.post("/memory/search", json={"query": phrase})
        ask = client.post("/memory/ask", json={"question": f"What do I own about {phrase}?"})
        facts = client.post("/memory/facts/search", json={"predicate": "owns_guitar"})
        profile = client.post("/memory/profile/get", json={"subject": "user"})

        approve = client.post("/memory/pending/approve", json={"id": memory_id})
        approved_search = client.post("/memory/search", json={"query": phrase})
        approved_facts = client.post("/memory/facts/search", json={"predicate": "owns_guitar"})
        approved_profile = client.post("/memory/profile/get", json={"subject": "user"})

    assert retrieve.status_code == 404
    assert search.json()["results"] == []
    assert ask.json()["answer_basis"] == "not_found"
    assert facts.json()["results"] == []
    assert profile.json()["facts"] == []
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "ok"
    assert approve.json()["memory_status"] == "active"
    assert [row["id"] for row in approved_search.json()["results"]] == [memory_id]
    assert [fact["source_conversation_id"] for fact in approved_facts.json()["results"]] == [
        memory_id
    ]
    assert memory_id in {
        fact["source_conversation_id"] for fact in approved_profile.json()["facts"]
    }


def test_review_pending_mcp_insert_can_be_rejected(tmp_path: Path) -> None:
    phrase = "pending review mcp rejection phrase"
    payload = _conversation(text=f"I own a {phrase}.")

    with _review_pending_client(tmp_path) as client:
        headers = _initialize_mcp(client)
        insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        memory_id = insert["id"]
        reject = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_pending_reject",
            arguments={"id": memory_id},
        )
        search = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_search",
            arguments={"query": phrase},
        )
        approve_after_reject = _call_tool(
            client,
            headers,
            request_id=5,
            name="memory_pending_approve",
            arguments={"id": memory_id},
        )

    assert insert["status"] == "pending_review"
    assert insert["memory_status"] == "pending_review"
    assert reject["status"] == "ok"
    assert reject["memory_status"] == "rejected"
    assert search["results"] == []
    assert approve_after_reject["status"] == "error"
    assert approve_after_reject["error_code"] == "memory_not_pending_review"


def test_review_pending_api_read_filters_expose_pending_and_rejected(tmp_path: Path) -> None:
    pending_phrase = "api pending status filter phrase"
    rejected_phrase = "api rejected status filter phrase"

    with _review_pending_client(tmp_path) as client:
        pending_insert = client.post(
            "/memory/insert", json=_conversation(text=f"I own an {pending_phrase}.")
        )
        rejected_insert = client.post(
            "/memory/insert", json=_conversation(text=f"I own an {rejected_phrase}.")
        )
        assert pending_insert.status_code == 200, pending_insert.text
        assert rejected_insert.status_code == 200, rejected_insert.text
        pending_id = pending_insert.json()["id"]
        rejected_id = rejected_insert.json()["id"]
        reject = client.post("/memory/pending/reject", json={"id": rejected_id})
        assert reject.status_code == 200, reject.text

        default_search = client.post("/memory/search", json={"query": "status filter phrase"})
        pending_search = client.post(
            "/memory/search",
            json={"query": pending_phrase, "memory_status": "pending_review"},
        )
        rejected_search = client.post(
            "/memory/search",
            json={"query": rejected_phrase, "memory_status": "rejected"},
        )
        all_search = client.post(
            "/memory/search",
            json={"query": "status filter phrase", "memory_status": "all", "top_k": 5},
        )
        pending_retrieve = client.post(
            "/memory/retrieve",
            json={"id": pending_id, "memory_status": "pending_review"},
        )
        rejected_retrieve = client.post(
            "/memory/retrieve",
            json={"id": rejected_id, "memory_status": "rejected"},
        )

    assert default_search.json()["results"] == []
    assert [row["id"] for row in pending_search.json()["results"]] == [pending_id]
    assert [row["id"] for row in rejected_search.json()["results"]] == [rejected_id]
    assert {row["id"] for row in all_search.json()["results"]} == {pending_id, rejected_id}
    assert pending_retrieve.status_code == 200
    assert pending_retrieve.json()["memory"]["metadata"]["memory_status"] == "pending_review"
    assert rejected_retrieve.status_code == 200
    assert rejected_retrieve.json()["memory"]["metadata"]["memory_status"] == "rejected"


def test_review_pending_mcp_read_filters_expose_pending_for_ask_and_retrieve(
    tmp_path: Path,
) -> None:
    phrase = "mcp pending ask status filter phrase"

    with _review_pending_client(tmp_path) as client:
        headers = _initialize_mcp(client)
        insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": _conversation(text=f"I own a {phrase}.")},
        )
        memory_id = insert["id"]
        default_retrieve = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_retrieve",
            arguments={"id": memory_id},
        )
        pending_retrieve = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_retrieve",
            arguments={"id": memory_id, "memory_status": "pending_review"},
        )
        pending_ask = _call_tool(
            client,
            headers,
            request_id=5,
            name="memory_ask",
            arguments={"question": phrase, "memory_status": "pending_review"},
        )

    assert default_retrieve["status"] == "not_found"
    assert pending_retrieve["status"] == "ok"
    assert pending_retrieve["memory"]["metadata"]["memory_status"] == "pending_review"
    assert pending_ask["answer_basis"] == "direct_memory"
    assert pending_ask["results"][0]["id"] == memory_id
