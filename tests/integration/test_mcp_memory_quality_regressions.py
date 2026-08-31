from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import PROJECT_ROLE_WRITER, SQLiteMetadataStore
from memory.config import ensure_token_hash_secret, parse_config

CODEX_PROJECT_ID = "codex-cli-findings"
OTHER_PROJECT_ID = "codex-cli-findings-other"
FORBIDDEN_CONCISE_ASK_KEYS = {
    "results",
    "citations",
    "evidence",
    "structured_evidence",
    "provenance",
    "fact_timeline",
}


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


def _client(tmp_path: Path, *, insert_policy: str = "permissive") -> TestClient:
    return TestClient(create_app(config=_config(tmp_path, insert_policy=insert_policy)))


def _auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AMH_TOKEN_HASH_SECRET", "codex-cli-findings-secret")
    config = parse_config(_config(tmp_path, auth="bearer_token"))
    ensure_token_hash_secret(config)
    store = SQLiteMetadataStore(Path(config.paths.data_dir) / "metadata.sqlite3")
    store.create_auth_token(owner_id="owner-a", token="token-a")
    store.create_project(project_id=CODEX_PROJECT_ID, owner_id="owner-a", name="Codex QA")
    store.create_project(
        project_id=OTHER_PROJECT_ID,
        owner_id="owner-a",
        name="Other Codex QA",
    )
    store.add_project_member(
        project_id=CODEX_PROJECT_ID,
        user_id="owner-a",
        role=PROJECT_ROLE_WRITER,
    )
    store.add_project_member(
        project_id=OTHER_PROJECT_ID,
        user_id="owner-a",
        role=PROJECT_ROLE_WRITER,
    )
    return TestClient(create_app(config=config))


def _conversation(
    *,
    text: str,
    memory_id: str | None = None,
    source: str = "codex",
    timestamp: str = "2026-08-28T14:00:00Z",
    tags: list[str] | None = None,
    thread_id: str | None = None,
    summary: str | None = None,
    save_intent: bool = True,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if save_intent:
        metadata.update(
            {
                "save_intent": "explicit_user_request",
                "save_intent_source": "codex-cli",
            }
        )
    if tags is not None:
        metadata["tags"] = tags
    if thread_id is not None:
        metadata["thread_id"] = thread_id
    if summary is not None:
        metadata["summary"] = summary
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
                "clientInfo": {"name": "codex-cli-findings", "version": "0.1"},
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
    raise AssertionError(f"No JSON-RPC result found in response: {response_text}")


def _tool_payload(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    content = _event_result_json(response.text)["result"]["content"]
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
    return _tool_payload(
        client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=headers,
        )
    )


def _assert_concise_ask_contract(payload: dict[str, Any], *, question: str) -> None:
    assert payload["answer"] != question
    assert "Based on stored memory:" not in payload["answer"]
    assert "- [" not in payload["answer"]
    assert FORBIDDEN_CONCISE_ASK_KEYS.isdisjoint(payload)


def test_mcp_concise_ask_uses_latest_temporal_fact_for_generic_attribute(
    tmp_path: Path,
) -> None:
    marker = "QA_CONTRACT_TEMPORAL_ASK"
    attribute = f"{marker} command runner"
    older = _conversation(
        memory_id="0a77288a-62a1-4a62-a29b-b24d73464d7b",
        text=f"In January, {attribute} is alpha runner.",
        source="agent-a",
        timestamp="2026-01-15T10:00:00Z",
        tags=["mcp-ask-contract", "temporal"],
        thread_id="AMH-QA-CONTRACT-ASK",
    )
    newer = _conversation(
        memory_id="8776477f-18f9-4c79-b4e1-9d2d5a8d5ef3",
        text=f"{attribute} changes to beta runner on 2026-06-20.",
        source="agent-b",
        timestamp="2026-06-20T14:30:00Z",
        tags=["mcp-ask-contract", "temporal"],
        thread_id="AMH-QA-CONTRACT-ASK",
    )
    question = f"What is the {attribute}?"

    with _client(tmp_path) as client:
        headers = _initialize_mcp(client)
        for request_id, payload in enumerate((older, newer), start=2):
            insert = _call_tool(
                client,
                headers,
                request_id=request_id,
                name="memory_insert",
                arguments={"conversation_json": payload},
            )
            assert insert["status"] == "ok"
        concise = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_ask",
            arguments={
                "question": question,
                "top_k": 5,
                "response_format": "concise",
            },
        )
        detailed = _call_tool(
            client,
            headers,
            request_id=5,
            name="memory_ask",
            arguments={
                "question": question,
                "top_k": 5,
                "response_format": "detailed",
            },
        )

    _assert_concise_ask_contract(concise, question=question)
    assert concise["answer"] == "beta runner"
    assert concise["answer_basis"] == "fact_layer"
    assert concise["latest"]["value"] == "beta runner"
    assert concise["latest"]["stored_at"] == newer["timestamp"]
    assert concise["latest"]["author"] == newer["source"]
    assert concise["fact_count"] == 1
    assert concise["citation_count"] == 1
    assert detailed["answer_basis"] == "fact_layer"
    assert detailed["latest"]["value"] == "beta runner"
    assert [entry["value"] for entry in detailed["fact_timeline"]] == [
        "beta runner",
        "alpha runner",
    ]
    assert detailed["facts"][0]["subject"] == attribute
    assert detailed["facts"][0]["predicate"] == "description"


def test_mcp_concise_ask_keeps_direct_memory_fallback_compact(
    tmp_path: Path,
) -> None:
    question = "qa contract direct ask raretrm deploymnt note?"
    payload = _conversation(
        memory_id="0269fbf2-fab8-4e0e-997b-7f5e4ead2747",
        text="placeholder",
        source="agent-a",
        timestamp="2026-06-20T15:00:00Z",
        tags=["mcp-ask-contract", "direct-memory"],
        thread_id="AMH-QA-CONTRACT-DIRECT",
    )
    payload["messages"] = [
        {"role": "user", "text": question},
        {
            "role": "assistant",
            "text": (
                "QA_CONTRACT_DIRECT_ASK rareterm deployment note: "
                "use cobalt spool for staging"
            ),
        },
    ]

    with _client(tmp_path) as client:
        headers = _initialize_mcp(client)
        insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        concise = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_ask",
            arguments={
                "question": question,
                "top_k": 5,
                "response_format": "concise",
            },
        )
        detailed = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_ask",
            arguments={
                "question": question,
                "top_k": 5,
                "response_format": "detailed",
            },
        )

    assert insert["status"] == "ok"
    _assert_concise_ask_contract(concise, question=question)
    assert concise["answer"] == (
        "QA_CONTRACT_DIRECT_ASK rareterm deployment note: "
        "use cobalt spool for staging"
    )
    assert concise["answer_basis"] == "direct_memory"
    assert concise["fact_count"] == 0
    assert concise["citation_count"] >= 1
    assert "latest" not in concise
    assert detailed["answer_basis"] == "direct_memory"
    assert detailed["results"]


def test_preference_question_uses_fact_backed_concise_answer(
    tmp_path: Path,
) -> None:
    payload = _conversation(
        memory_id="e733588e-d728-468d-8801-7c7d60ea4138",
        text=(
            "Please remember this QA preference: I like kettle cooked potato chips, "
            "especially sea salt flavor."
        ),
        tags=["codex-cli-qa", "preference"],
        thread_id="AMH-QA-20260828-ASK-01",
        summary="User likes kettle cooked potato chips, especially sea salt flavor.",
    )

    with _client(tmp_path) as client:
        headers = _initialize_mcp(client)
        insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        facts = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_fact_search",
            arguments={
                "subject": "user",
                "predicate": "likes",
                "response_format": "detailed",
            },
        )
        profile = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_profile_get",
            arguments={
                "subject": "user",
                "predicate": "likes",
                "response_format": "detailed",
            },
        )
        concise_ask = _call_tool(
            client,
            headers,
            request_id=5,
            name="memory_ask",
            arguments={
                "question": "What snack do I like? Please handle my chipps typo.",
                "top_k": 5,
            },
        )
        detailed_ask = _call_tool(
            client,
            headers,
            request_id=6,
            name="memory_ask",
            arguments={
                "question": "What snack do I like? Please handle my chipps typo.",
                "top_k": 5,
                "response_format": "detailed",
            },
        )

    assert insert["status"] == "ok"
    assert facts["results"][0]["object_normalized"] == (
        "kettle cooked potato chips, especially sea salt flavor"
    )
    assert profile["facts"][0]["predicate"] == "likes"
    assert concise_ask["answer"] == (
        "kettle cooked potato chips, especially sea salt flavor"
    )
    assert concise_ask["answer_basis"] == "fact_layer"
    assert concise_ask["latest"]["value"] == (
        "kettle cooked potato chips, especially sea salt flavor"
    )
    assert concise_ask["latest"]["stored_at"] == payload["timestamp"]
    assert concise_ask["latest"]["author"] == payload["source"]
    assert concise_ask["fact_count"] == 1
    assert concise_ask["memory_result_count"] == 0
    assert detailed_ask["results"] == []
    assert detailed_ask["facts"][0]["source_conversation_id"] == payload["id"]
    assert detailed_ask["structured_evidence"]["facts"][0]["used_in_answer"] is True


def test_mcp_insert_deduplicates_exact_reinsert(tmp_path: Path) -> None:
    payload = _conversation(
        memory_id="a5858840-3de5-4194-bf37-16f57dd3a353",
        text="Codex CLI exact duplicate QA phrase is violet atlas.",
        tags=["codex-cli-qa", "dedupe"],
        thread_id="AMH-QA-20260828-RESULTS",
    )

    with _client(tmp_path) as client:
        headers = _initialize_mcp(client)
        first_insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        second_insert = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        search = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_search",
            arguments={
                "query": "violet atlas",
                "top_k": 5,
                "response_format": "detailed",
            },
        )

    assert first_insert["status"] == "ok"
    assert second_insert["status"] == "ok"
    assert first_insert["id"] == second_insert["id"] == payload["id"]
    assert first_insert["deduplicated"] is False
    assert second_insert["deduplicated"] is True
    assert [row["id"] for row in search["results"]] == [payload["id"]]


@pytest.mark.parametrize(
    ("correction_text", "expected_new"),
    [
        (
            "Actually, my favorite food is QA pizza slice, not QA avocado toast.",
            "QA pizza slice",
        ),
        (
            "Correction: QA pizza slice replaces QA avocado toast for my favorite food.",
            "QA pizza slice",
        ),
        (
            "My favorite food is QA pizza slice, not QA avocado toast.",
            "QA pizza slice",
        ),
    ],
)
def test_clear_correction_supersedes_prior_favorite_food(
    tmp_path: Path,
    correction_text: str,
    expected_new: str,
) -> None:
    old_food = _conversation(
        memory_id="33d79ee0-ce14-4a32-82b1-7e7406ffc999",
        text="My favorite food is QA avocado toast.",
        source="codex",
        timestamp="2026-08-12T00:00:00Z",
        tags=["codex-cli-qa", "correction"],
        thread_id="AMH-QA-20260828-CORR",
    )
    corrected_food = _conversation(
        memory_id="44d79ee0-ce14-4a32-82b1-7e7406ffc999",
        text=correction_text,
        source="hermes",
        timestamp="2026-12-12T00:00:00Z",
        tags=["codex-cli-qa", "correction"],
        thread_id="AMH-QA-20260828-CORR",
    )

    with _client(tmp_path) as client:
        headers = _initialize_mcp(client)
        for request_id, payload in enumerate((old_food, corrected_food), start=2):
            insert = _call_tool(
                client,
                headers,
                request_id=request_id,
                name="memory_insert",
                arguments={"conversation_json": payload},
            )
            assert insert["status"] == "ok"
        active = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_fact_search",
            arguments={
                "subject": "user",
                "predicate": "favorite_food",
                "response_format": "detailed",
            },
        )
        audit = _call_tool(
            client,
            headers,
            request_id=5,
            name="memory_fact_search",
            arguments={
                "subject": "user",
                "predicate": "favorite_food",
                "include_superseded": True,
                "response_format": "detailed",
            },
        )
        ask = _call_tool(
            client,
            headers,
            request_id=6,
            name="memory_ask",
            arguments={
                "question": "What is my favorite food?",
                "response_format": "detailed",
            },
        )

    assert [fact["object_normalized"] for fact in active["results"]] == [expected_new]
    audit_by_object = {
        fact["object_normalized"]: fact for fact in audit["results"]
    }
    assert audit_by_object["QA avocado toast"]["superseded_by"] == (
        audit_by_object[expected_new]["id"]
    )
    assert audit_by_object[expected_new]["superseded_by"] is None
    assert ask["answer"] == expected_new
    assert ask["latest"]["value"] == expected_new
    assert ask["latest"]["stored_at"] == "2026-12-12T00:00:00Z"
    assert ask["latest"]["author"] == "hermes"
    assert ask["facts"][0]["source_quality"] == "corrected_by_user"


def test_ambiguous_favorite_update_remains_active_conflict(
    tmp_path: Path,
) -> None:
    old_food = _conversation(
        text="My favorite food is QA avocado toast.",
        tags=["codex-cli-qa", "correction"],
        thread_id="AMH-QA-20260828-CORR",
    )
    ambiguous_update = _conversation(
        text="Actually, my favorite food is QA pizza slice.",
        tags=["codex-cli-qa", "correction"],
        thread_id="AMH-QA-20260828-CORR",
    )

    with _client(tmp_path) as client:
        headers = _initialize_mcp(client)
        for request_id, payload in enumerate((old_food, ambiguous_update), start=2):
            insert = _call_tool(
                client,
                headers,
                request_id=request_id,
                name="memory_insert",
                arguments={"conversation_json": payload},
            )
            assert insert["status"] == "ok"
        active = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_fact_search",
            arguments={
                "subject": "user",
                "predicate": "favorite_food",
                "response_format": "detailed",
            },
        )
        ask = _call_tool(
            client,
            headers,
            request_id=5,
            name="memory_ask",
            arguments={
                "question": "What is my favorite food?",
                "response_format": "detailed",
            },
        )

    assert {fact["object_normalized"] for fact in active["results"]} == {
        "QA avocado toast",
        "QA pizza slice",
    }
    assert all(fact["superseded_by"] is None for fact in active["results"])
    assert ask["answer_basis"] == "conflict"
    assert ask["confidence"] == "low"


def test_search_filters_pagination_threads_and_explicit_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _conversation(
        memory_id="1c820292-cf4f-4ba1-bcab-66fdf5f915f0",
        text="Codex filter beacon target mentions MCP and exact metadata tags.",
        source="codex",
        timestamp="2026-08-28T14:00:00Z",
        tags=["codex-cli-qa", "explicit-filter"],
        thread_id="AMH-QA-20260828-FILTER-THREAD",
    )
    wrong_source = _conversation(
        text="Codex filter beacon wrong source.",
        source="opencode",
        timestamp="2026-08-28T14:00:00Z",
        tags=["codex-cli-qa", "explicit-filter"],
        thread_id="AMH-QA-20260828-FILTER-THREAD",
    )
    wrong_tag = _conversation(
        text="Codex filter beacon wrong explicit tag.",
        source="codex",
        timestamp="2026-08-28T14:00:00Z",
        tags=["codex-cli-qa"],
        thread_id="AMH-QA-20260828-FILTER-THREAD",
    )
    wrong_thread = _conversation(
        text="Codex filter beacon wrong thread.",
        source="codex",
        timestamp="2026-08-28T14:00:00Z",
        tags=["codex-cli-qa", "explicit-filter"],
        thread_id="AMH-QA-20260828-OTHER",
    )
    wrong_date = _conversation(
        text="Codex filter beacon wrong date.",
        source="codex",
        timestamp="2026-08-27T14:00:00Z",
        tags=["codex-cli-qa", "explicit-filter"],
        thread_id="AMH-QA-20260828-FILTER-THREAD",
    )
    auto_tag_only = _conversation(
        text="Codex filter beacon auto-tag-only text discusses FastAPI and MCP.",
        source="codex",
        timestamp="2026-08-28T14:00:00Z",
        tags=[],
        thread_id="AMH-QA-20260828-FILTER-THREAD",
    )

    with _auth_client(tmp_path, monkeypatch) as client:
        headers = _initialize_mcp(client, token="token-a")
        for request_id, payload in enumerate(
            (target, wrong_source, wrong_tag, wrong_thread, wrong_date, auto_tag_only),
            start=2,
        ):
            project_id = OTHER_PROJECT_ID if payload is wrong_thread else CODEX_PROJECT_ID
            insert = _call_tool(
                client,
                headers,
                request_id=request_id,
                name="memory_insert",
                arguments={
                    "conversation_json": payload,
                    "project_id": project_id,
                },
            )
            assert insert["status"] == "ok"
        filtered = _call_tool(
            client,
            headers,
            request_id=10,
            name="memory_search",
            arguments={
                "query": "Codex filter beacon target",
                "top_k": 10,
                "source": "codex",
                "date_from": "2026-08-28T00:00:00Z",
                "date_to": "2026-08-29T00:00:00Z",
                "tags": ["explicit-filter"],
                "thread_id": "AMH-QA-20260828-FILTER-THREAD",
                "project_id": CODEX_PROJECT_ID,
                "response_format": "detailed",
            },
        )
        page_one = _call_tool(
            client,
            headers,
            request_id=11,
            name="memory_search",
            arguments={
                "query": "Codex filter beacon",
                "limit": 1,
                "top_k": 10,
                "project_id": CODEX_PROJECT_ID,
            },
        )
        page_two = _call_tool(
            client,
            headers,
            request_id=12,
            name="memory_search",
            arguments={
                "query": "Codex filter beacon",
                "limit": 1,
                "top_k": 10,
                "cursor": page_one["cursor"],
                "project_id": CODEX_PROJECT_ID,
            },
        )
        grouped_threads = _call_tool(
            client,
            headers,
            request_id=13,
            name="memory_search",
            arguments={
                "query": "Codex filter beacon",
                "top_k": 10,
                "result_mode": "threads",
                "project_id": CODEX_PROJECT_ID,
            },
        )
        auto_tag_filter = _call_tool(
            client,
            headers,
            request_id=14,
            name="memory_search",
            arguments={
                "query": "auto-tag-only FastAPI MCP",
                "top_k": 10,
                "tags": ["mcp"],
                "project_id": CODEX_PROJECT_ID,
                "response_format": "detailed",
            },
        )

    assert [row["id"] for row in filtered["results"]] == [target["id"]]
    assert page_one["cursor"] is not None
    assert page_one["results"][0]["id"] != page_two["results"][0]["id"]
    assert page_two["cursor"] is not None
    assert {
        row["thread_id"] for row in grouped_threads["results"]
    } == {"AMH-QA-20260828-FILTER-THREAD"}
    assert auto_tag_filter["results"] == []


def test_review_state_reads_are_explicit_and_secret_safe(
    tmp_path: Path,
) -> None:
    pending_phrase = "codex pending review status phrase"
    rejected_phrase = "codex rejected review status phrase"
    quarantined_phrase = "codex quarantine status phrase"
    secret = "sk-proj-codexCliFindingsSecretValue123456789"

    with _client(tmp_path, insert_policy="review_pending") as client:
        headers = _initialize_mcp(client)
        pending_insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={
                "conversation_json": _conversation(
                    text=f"I own a {pending_phrase}.",
                    save_intent=False,
                )
            },
        )
        rejected_insert = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_insert",
            arguments={
                "conversation_json": _conversation(
                    text=f"I own a {rejected_phrase}.",
                    save_intent=False,
                )
            },
        )
        quarantine_insert = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_insert",
            arguments={
                "conversation_json": _conversation(
                    text=f"I own a {quarantined_phrase}. API_KEY={secret}",
                    save_intent=False,
                )
            },
        )
        reject = _call_tool(
            client,
            headers,
            request_id=5,
            name="memory_pending_reject",
            arguments={"id": rejected_insert["id"]},
        )
        default_search = _call_tool(
            client,
            headers,
            request_id=6,
            name="memory_search",
            arguments={"query": "codex review status phrase", "top_k": 10},
        )
        pending_search = _call_tool(
            client,
            headers,
            request_id=7,
            name="memory_search",
            arguments={
                "query": pending_phrase,
                "memory_status": "pending_review",
            },
        )
        rejected_search = _call_tool(
            client,
            headers,
            request_id=8,
            name="memory_search",
            arguments={"query": rejected_phrase, "memory_status": "rejected"},
        )
        quarantined_search = _call_tool(
            client,
            headers,
            request_id=9,
            name="memory_search",
            arguments={
                "query": quarantined_phrase,
                "memory_status": "quarantined",
            },
        )
        pending_ask = _call_tool(
            client,
            headers,
            request_id=10,
            name="memory_ask",
            arguments={
                "question": pending_phrase,
                "memory_status": "pending_review",
                "response_format": "detailed",
            },
        )

    assert pending_insert["status"] == "pending_review"
    assert rejected_insert["status"] == "pending_review"
    assert quarantine_insert["status"] == "quarantined"
    assert secret not in json.dumps(quarantine_insert)
    assert reject["memory_status"] == "rejected"
    assert default_search["results"] == []
    assert [row["id"] for row in pending_search["results"]] == [pending_insert["id"]]
    assert [row["id"] for row in rejected_search["results"]] == [rejected_insert["id"]]
    assert [row["id"] for row in quarantined_search["results"]] == [
        quarantine_insert["id"]
    ]
    assert pending_ask["answer_basis"] == "direct_memory"
    assert pending_ask["results"][0]["id"] == pending_insert["id"]


def test_tight_context_budget_reports_truncated_evidence(
    tmp_path: Path,
) -> None:
    payload = _conversation(
        text=(
            "The Codex tight budget answer can be recovered as lapis compass, with enough extra "
            "detail after the phrase to force truncation under a tiny context budget."
        ),
        tags=["codex-cli-qa", "budget"],
        thread_id="AMH-QA-20260828-BUDGET",
    )

    with _client(tmp_path) as client:
        headers = _initialize_mcp(client)
        insert = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_insert",
            arguments={"conversation_json": payload},
        )
        ask = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_ask",
            arguments={
                "question": "What is the Codex tight budget answer?",
                "top_k": 5,
                "max_context_tokens": 14,
                "response_format": "detailed",
            },
        )

    assert insert["status"] == "ok"
    assert ask["context_truncated"] is True
    assert ask["confidence"] in {"low", "none"}
    assert "truncated" in ask["confidence_reason"].lower()
    assert "lapis compass" not in ask["answer"]
