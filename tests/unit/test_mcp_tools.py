from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest

from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.interfaces import mcp_server
from memory.interfaces.mcp_response_format import format_search_response
from memory.interfaces.mcp_server import (
    _deterministic_sort,
    _emit_mcp_tool_log,
    build_tool_handlers,
)


class StubEmbedder:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


class StubMetadataStore:
    def __init__(self):
        self.rows: dict[str, dict[str, object]] = {}

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


class StubMCPContext:
    def __init__(self) -> None:
        self.logs: list[dict[str, object]] = []

    async def log(self, message, level=None, logger_name=None, extra=None) -> None:
        self.logs.append(
            {
                "message": message,
                "level": level,
                "logger_name": logger_name,
                "extra": extra,
            }
        )


class StubMCPContextWithRequestId(StubMCPContext):
    request_id = "mcp-call-123"


def _runtime() -> mvp_ingestion.RuntimeDependencies:
    return mvp_ingestion.RuntimeDependencies(
        embedding_provider=StubEmbedder(),  # type: ignore
        metadata_store=StubMetadataStore(),
        vector_store=StubVectorStore(),
        health_state={"mode": "ok", "vector_fallback_active": False},
    )


def _conversation() -> dict[str, object]:
    return {
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "source": "claude",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "hello mcp"}],
        "metadata": {"imported_at": "2026-01-01T00:00:00Z"},
    }


def _conversation_two() -> dict[str, object]:
    return {
        "id": "2f39f5cc-6256-4ca9-a9b2-6211bc6e3702",
        "source": "chatgpt",
        "timestamp": "2026-01-02T00:00:00Z",
        "messages": [{"role": "user", "text": "hello again"}],
        "metadata": {
            "imported_at": "2026-01-02T00:00:00Z",
            "tags": ["beta"],
            "thread_id": "thread-beta",
        },
    }


def test_mcp_search_sort_preserves_conversation_group_score() -> None:
    rows = [
        {"id": "conversation-a", "score": 0.01, "chunk_index": 0, "text": "a0"},
        {
            "id": "conversation-b",
            "score": 0.05,
            "chunk_index": 0,
            "text": "b0",
            "conversation_score": 0.05,
        },
        {
            "id": "conversation-a",
            "score": 0.20,
            "chunk_index": 1,
            "text": "a1",
            "conversation_score": 0.01,
        },
    ]

    sorted_rows = _deterministic_sort(rows)

    assert [row["id"] for row in sorted_rows] == [
        "conversation-a",
        "conversation-a",
        "conversation-b",
    ]


@pytest.mark.asyncio
async def test_mcp_insert_records_audit_event_with_tool_request_id() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)
    ctx = StubMCPContextWithRequestId()

    result = await handlers["memory_insert"](
        conversation_json=_conversation(),
        ctx=ctx,
    )

    assert result["status"] == "ok"
    events = getattr(runtime.metadata_store, "_audit_events")
    inserted = next(event for event in events if event["event_type"] == "memory.inserted")
    assert inserted["source_surface"] == "mcp"
    assert inserted["request_id"] == "mcp-call-123"
    assert inserted["memory_id"] == result["id"]


@pytest.mark.asyncio
async def test_mcp_tool_log_payload_is_sanitized() -> None:
    ctx = StubMCPContext()

    await _emit_mcp_tool_log(
        ctx, tool_name="memory_insert", status="error", error_code="insert_failed"
    )

    assert ctx.logs == [
        {
            "message": "mcp tool completed",
            "level": "error",
            "logger_name": "ai-memory-hub.mcp",
            "extra": {
                "tool": "memory_insert",
                "status": "error",
                "error_code": "insert_failed",
            },
        }
    ]


@pytest.mark.asyncio
async def test_mcp_tool_log_includes_tool_call_id_when_available() -> None:
    ctx = StubMCPContextWithRequestId()

    await _emit_mcp_tool_log(ctx, tool_name="memory_search", status="ok")

    assert ctx.logs[0]["extra"] == {
        "tool": "memory_search",
        "status": "ok",
        "mcp_tool_call_id": "mcp-call-123",
    }


@pytest.mark.asyncio
async def test_mcp_tool_log_notifications_are_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_server, "MCP_LOG_NOTIFICATION_LIMIT", 3)
    ctx = StubMCPContext()

    for index in range(5):
        await _emit_mcp_tool_log(ctx, tool_name=f"memory_search_{index}", status="ok")

    assert ctx.logs == [
        {
            "message": "mcp tool completed",
            "level": "info",
            "logger_name": "ai-memory-hub.mcp",
            "extra": {"tool": "memory_search_0", "status": "ok"},
        },
        {
            "message": "mcp tool completed",
            "level": "info",
            "logger_name": "ai-memory-hub.mcp",
            "extra": {"tool": "memory_search_1", "status": "ok"},
        },
        {
            "message": "mcp log notifications rate limited",
            "level": "warning",
            "logger_name": "ai-memory-hub.mcp",
            "extra": {"event": "mcp_log_notifications_rate_limited", "limit": 3},
        },
    ]


@pytest.mark.asyncio
async def test_mcp_tool_handlers_insert_search_retrieve() -> None:
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}}, runtime=_runtime()
    )
    handlers = build_tool_handlers(agent)
    ctx = StubMCPContext()

    validate_result = await handlers["memory_validate"](_conversation(), ctx=ctx)
    assert validate_result["status"] == "ok"
    assert validate_result["valid"] is True

    insert_result = await handlers["memory_insert"](_conversation(), ctx=ctx)
    assert insert_result["status"] == "ok"

    search_result = await handlers["memory_search"]("hello", 5, ctx=ctx)
    assert search_result["status"] == "ok"
    assert "conversation" not in search_result["results"][0]
    assert search_result["results"][0]["citation"]["id"] == (
        "d9fd4c95-9cb3-4fd5-b967-3027f8863210"
    )

    retrieve_result = await handlers["memory_retrieve"](
        "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        ctx=ctx,
    )
    assert retrieve_result["status"] == "ok"
    assert retrieve_result["memory"]["id"] == "d9fd4c95-9cb3-4fd5-b967-3027f8863210"
    assert retrieve_result["memory"]["source"] == "claude"
    assert retrieve_result["memory"]["memory_status"] == "active"
    assert retrieve_result["memory"]["message_count"] == 1
    assert retrieve_result["memory"]["messages"] == [
        {"role": "user", "text": "hello mcp"}
    ]
    assert "metadata" not in retrieve_result["memory"]
    assert "results" in retrieve_result
    assert "cursor" in retrieve_result
    assert "error_code" in retrieve_result
    assert "error_message" in retrieve_result

    detailed_retrieve_result = await handlers["memory_retrieve"](
        "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        response_format="detailed",
        ctx=ctx,
    )
    assert detailed_retrieve_result["status"] == "ok"
    assert "hash" not in detailed_retrieve_result["memory"]["messages"][0]
    assert "conversation_hash" not in detailed_retrieve_result["memory"]["metadata"]
    assert "message_hashes" not in detailed_retrieve_result["memory"]["metadata"]

    ask_result = await handlers["memory_ask"]("what was stored?", 3, ctx=ctx)
    assert ask_result["status"] == "ok"
    assert "answer" in ask_result
    assert "results" not in ask_result
    assert ask_result["memory_result_count"] == 1
    assert ask_result["citation_count"] == 1
    for verbose_key in (
        "citations",
        "evidence",
        "structured_evidence",
        "provenance",
        "context_tokens_used",
        "chunks_selected",
    ):
        assert verbose_key not in ask_result

    detailed_ask_result = await handlers["memory_ask"](
        "what was stored?", 3, response_format="detailed", ctx=ctx
    )
    assert detailed_ask_result["status"] == "ok"
    assert "conversation" in detailed_ask_result["results"][0]
    assert "hash" not in detailed_ask_result["results"][0]["conversation"]["messages"][0]

    budgeted_ask_result = await handlers["memory_ask"](
        "what was stored?",
        3,
        max_context_tokens=100,
        response_format="detailed",
        ctx=ctx,
    )
    assert budgeted_ask_result["status"] == "ok"
    assert budgeted_ask_result["context_tokens_used"] <= 100
    assert budgeted_ask_result["chunks_selected"] == 1
    assert ctx.logs == []


@pytest.mark.asyncio
async def test_mcp_search_response_format_controls_conversation_payloads() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)
    payload = _conversation()
    payload["metadata"]["summary"] = "User greeted MCP in a compact response test."
    payload["metadata"]["thread_id"] = "thread-response-format"

    await handlers["memory_insert"](payload)
    concise = await handlers["memory_search"]("hello", 5)
    detailed = await handlers["memory_search"](
        "hello", 5, response_format="detailed"
    )

    concise_row = concise["results"][0]
    detailed_row = detailed["results"][0]
    assert concise["status"] == "ok"
    assert set(concise) == {
        "status",
        "id",
        "results",
        "cursor",
        "error_code",
        "error_message",
    }
    assert "conversation" not in concise_row
    assert concise_row["citation"]["id"] == payload["id"]
    assert concise_row["citation"]["source"] == "claude"
    assert concise_row["citation"]["timestamp"] == "2026-01-01T00:00:00Z"
    assert concise_row["citation"]["thread_id"] == "thread-response-format"
    assert "hello mcp" in concise_row["citation"]["summary"]
    concise_json = json.dumps(concise)
    assert "index_chunks" not in concise_json
    assert "tag_sources" not in concise_json

    assert detailed["status"] == "ok"
    assert "conversation" in detailed_row
    assert "index_chunks" in detailed_row["conversation"]["metadata"]
    assert "tag_sources" in detailed_row["conversation"]["metadata"]


def test_mcp_concise_search_drops_undocumented_top_level_fields() -> None:
    payload = {
        "status": "ok",
        "cursor": "next-page",
        "total": 3,
        "results": [{"id": "memory-a", "text": "safe result"}],
        "conversation": {"private": "top-level-sentinel"},
        "audit": {"path": "D:/private/backend.db"},
    }

    concise = format_search_response(payload, "concise")

    assert concise["status"] == "ok"
    assert concise["cursor"] == "next-page"
    assert concise["total"] == 3
    assert concise["results"][0]["id"] == "memory-a"
    assert "conversation" not in concise
    assert "audit" not in concise


@pytest.mark.asyncio
async def test_mcp_concise_search_truncates_large_chunk_text() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)
    long_text = "hello " + ("large evidence text " * 80)
    payload = _conversation()
    payload["messages"] = [{"role": "user", "text": long_text}]

    await handlers["memory_insert"](payload)
    concise = await handlers["memory_search"]("hello", 5)
    detailed = await handlers["memory_search"](
        "hello", 5, response_format="detailed"
    )

    concise_text = concise["results"][0]["text"]
    assert len(concise_text) <= 800
    assert concise_text.endswith("...")
    assert detailed["results"][0]["text"] == long_text


@pytest.mark.asyncio
async def test_mcp_tool_handlers_keep_reads_responsive_during_slow_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    handlers = build_tool_handlers(agent)
    insert_started = threading.Event()

    def slow_ingest_messages(*_args, **_kwargs) -> dict[str, object]:
        insert_started.set()
        time.sleep(0.25)
        return {"status": "ok", "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210"}

    def search(*_args, **_kwargs) -> dict[str, object]:
        return {"status": "ok", "results": [], "cursor": None}

    monkeypatch.setattr(agent._service, "ingest_messages", slow_ingest_messages)
    monkeypatch.setattr(agent._service, "search", search)

    started_at = time.perf_counter()
    insert_task = asyncio.create_task(handlers["memory_insert"](_conversation()))
    await asyncio.sleep(0)

    assert insert_started.is_set()
    assert time.perf_counter() - started_at < 0.1
    search_result = await asyncio.wait_for(
        handlers["memory_search"]("hello", top_k=5),
        timeout=0.2,
    )
    assert search_result["status"] == "ok"
    assert search_result["results"] == []
    assert await insert_task == {
        "status": "ok",
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "results": [],
        "cursor": None,
        "error_code": None,
        "error_message": None,
    }


@pytest.mark.asyncio
async def test_mcp_tool_handlers_expose_project_helpers() -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    handlers = build_tool_handlers(agent)

    project_list = await handlers["memory_project_list"]()
    default_project = await handlers["memory_project_default_get"]()
    project_get = await handlers["memory_project_get"]("local-default")
    denied_project = await handlers["memory_project_get"]("shared-project")

    assert project_list["status"] == "ok"
    assert project_list["results"] == [default_project["project"]]
    assert default_project["project"]["id"] == "local-default"
    assert default_project["project"]["is_default"] is True
    assert project_get["project"]["id"] == "local-default"
    assert denied_project["status"] == "error"
    assert denied_project["error_code"] == "permission_denied"


@pytest.mark.asyncio
async def test_mcp_permission_denied_response_does_not_echo_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    handlers = build_tool_handlers(agent)
    sentinel = "D:/private/backend.db::sensitive-identifier"

    async def deny(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise PermissionError(sentinel)

    monkeypatch.setattr(agent, "project_get", deny)

    response = await handlers["memory_project_get"]("shared-project")

    assert response["status"] == "error"
    assert response["error_code"] == "permission_denied"
    assert response["error_message"] == "Access to the requested project was denied"
    assert sentinel not in json.dumps(response)


@pytest.mark.asyncio
async def test_mcp_tool_handlers_accept_project_id_argument() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)
    payload = _conversation()
    payload["messages"] = [{"role": "user", "text": "shared workspace note"}]

    insert_result = await handlers["memory_insert"](payload, project_id="local-default")
    search_result = await handlers["memory_search"](
        "shared workspace", top_k=5, project_id="local-default"
    )

    assert insert_result["status"] == "ok"
    stored = runtime.metadata_store.get(insert_result["id"])
    assert stored["metadata"]["project_id"] == "local-default"
    assert search_result["status"] == "ok"
    assert search_result["results"][0]["id"] == insert_result["id"]


@pytest.mark.asyncio
async def test_mcp_fact_tools_return_profile_facts() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)
    payload = _conversation()
    payload["metadata"]["save_intent"] = "explicit_user_request"
    payload["metadata"]["save_intent_source"] = "codex"
    payload["messages"] = [
        {"role": "user", "text": "I own a Gibson Special with P90 pickups, cherry."}
    ]

    await handlers["memory_insert"](payload)
    facts = await handlers["memory_fact_search"](
        subject="user",
        predicate="owns_guitar",
        save_intent="explicit_user_request",
        response_format="detailed",
    )
    profile = await handlers["memory_profile_get"](
        subject="user", save_intent_source="codex", response_format="detailed"
    )
    ask = await handlers["memory_ask"](
        "What guitar do I own?", 5, response_format="detailed"
    )

    assert facts["status"] == "ok"
    assert facts["results"][0]["predicate"] == "owns_guitar"
    assert facts["results"][0]["source_quality"] == "direct_user_statement"
    assert facts["results"][0]["save_intent_source"] == "codex"
    assert profile["facts"][0]["object"] == facts["results"][0]["object"]
    assert profile["summary"]["filters"]["save_intent_source"] == "codex"
    assert profile["summary"]["basis"] == "active_facts"
    assert "owns_guitar" in profile["summary"]["text"]
    assert ask["answer_basis"] == "fact_layer"
    assert ask["confidence_reason"] == "Extracted from a direct user statement."
    assert ask["evidence"][0]["type"] == "fact"
    assert ask["citations"][0]["save_intent"] == "explicit_user_request"
    assert ask["structured_evidence"]["facts"][0]["source_quality"] == "direct_user_statement"


@pytest.mark.asyncio
async def test_mcp_fact_and_profile_concise_format_reduces_fact_payloads() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)
    payload = _conversation()
    payload["metadata"]["save_intent"] = "explicit_user_request"
    payload["messages"] = [
        {"role": "user", "text": "I own a blue Gibson Special guitar."}
    ]

    await handlers["memory_insert"](payload)
    facts = await handlers["memory_fact_search"](
        subject="user", predicate="owns_guitar"
    )
    profile = await handlers["memory_profile_get"](
        subject="user", predicate="owns_guitar"
    )

    assert facts["status"] == "ok"
    assert "qualifiers" not in facts["results"][0]
    assert "source_message_indexes" not in facts["results"][0]
    assert facts["results"][0]["object_normalized"] == "a blue Gibson Special guitar"
    assert facts["results"][0]["superseded"] is False

    assert profile["status"] == "ok"
    assert set(profile["summary"]) == {
        "text",
        "active_fact_count",
        "freshest_at",
        "confidence_counts",
        "source_quality_counts",
    }
    assert "qualifiers" not in profile["facts"][0]
    assert profile["facts"][0]["predicate"] == "owns_guitar"


@pytest.mark.asyncio
async def test_mcp_concise_ask_includes_latest_value_metadata() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)
    older = _conversation()
    older["id"] = "11111111-1111-4111-8111-111111111111"
    older["source"] = "codex"
    older["timestamp"] = "2026-08-12T00:00:00Z"
    older["messages"] = [{"role": "user", "text": "The command name is alpha runner."}]
    newer = _conversation_two()
    newer["id"] = "22222222-2222-4222-8222-222222222222"
    newer["source"] = "hermes"
    newer["timestamp"] = "2026-12-12T00:00:00Z"
    newer["messages"] = [{"role": "user", "text": "The command name is beta runner."}]

    await handlers["memory_insert"](older)
    await handlers["memory_insert"](newer)
    ask = await handlers["memory_ask"]("What is the command name?", 5)

    assert ask["answer"] == "beta runner"
    assert ask["answer_basis"] == "fact_layer"
    assert ask["latest"]["value"] == "beta runner"
    assert ask["latest"]["stored_at"] == "2026-12-12T00:00:00Z"
    assert ask["latest"]["author"] == "hermes"
    assert ask["fact_count"] == 1
    assert "fact_timeline" not in ask
    assert "facts" not in ask
    assert "evidence" not in ask


@pytest.mark.asyncio
async def test_mcp_fact_and_profile_concise_deduplicates_and_limits_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    handlers = build_tool_handlers(agent)
    fact_search_kwargs: list[dict[str, Any]] = []
    facts = [
        {
            "id": "fact-a",
            "subject": "user",
            "predicate": "owns_guitar",
            "object": "a cherry Gibson guitar",
            "object_normalized": "a cherry Gibson guitar",
            "confidence": "high",
            "qualifiers": {"source_role": "user"},
        },
        {
            "id": "fact-b",
            "subject": "user",
            "predicate": "owns_guitar",
            "object": "a cherry Gibson guitar",
            "object_normalized": "a cherry Gibson guitar",
            "confidence": "high",
            "qualifiers": {"source_role": "user"},
        },
        {
            "id": "fact-c",
            "subject": "user",
            "predicate": "owns_guitar",
            "object": "a blue Jazzmaster guitar",
            "object_normalized": "a blue Jazzmaster guitar",
            "confidence": "medium",
            "qualifiers": {"source_role": "user"},
        },
    ]

    async def fake_fact_search(**kwargs):
        fact_search_kwargs.append(kwargs)
        return {"status": "ok", "results": facts}

    async def fake_profile_get(**_kwargs):
        return {
            "status": "ok",
            "subject": "user",
            "summary": {"text": "User has guitar facts.", "active_fact_count": 3},
            "facts": facts,
        }

    monkeypatch.setattr(agent, "fact_search", fake_fact_search)
    monkeypatch.setattr(agent, "profile_get", fake_profile_get)

    query_facts = await handlers["memory_fact_search"](query="cherry Gibson")
    default_facts = await handlers["memory_fact_search"](subject="user")
    concise_facts = await handlers["memory_fact_search"](subject="user", limit=1)
    detailed_facts = await handlers["memory_fact_search"](
        subject="user", response_format="detailed", limit=1
    )
    concise_profile = await handlers["memory_profile_get"](subject="user", limit=1)

    assert fact_search_kwargs[0]["query"] == "cherry Gibson"
    assert [row["id"] for row in query_facts["results"]] == ["fact-a", "fact-c"]
    assert [row["id"] for row in default_facts["results"]] == ["fact-a", "fact-c"]
    assert default_facts["total_results"] == 3
    assert default_facts["unique_results"] == 2
    assert default_facts["returned_results"] == 2
    assert default_facts["omitted_results"] == 0
    assert default_facts["result_limit"] == 10

    assert [row["id"] for row in concise_facts["results"]] == ["fact-a"]
    assert concise_facts["total_results"] == 3
    assert concise_facts["unique_results"] == 2
    assert concise_facts["returned_results"] == 1
    assert concise_facts["omitted_results"] == 1
    assert concise_facts["result_limit"] == 1
    assert "qualifiers" not in concise_facts["results"][0]

    assert len(detailed_facts["results"]) == 3
    assert "qualifiers" in detailed_facts["results"][0]

    assert [row["id"] for row in concise_profile["facts"]] == ["fact-a"]
    assert concise_profile["total_facts"] == 3
    assert concise_profile["unique_facts"] == 2
    assert concise_profile["returned_facts"] == 1
    assert concise_profile["omitted_facts"] == 1
    assert concise_profile["fact_limit"] == 1


def test_mcp_does_not_expose_agent_facing_forget_or_delete_tools() -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    handlers = build_tool_handlers(agent)

    assert all("forget" not in name and "delete" not in name and "purge" not in name for name in handlers)


def test_mcp_tool_policies_cover_registered_handlers() -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    handlers = build_tool_handlers(agent)

    assert set(handlers) == set(mcp_server.TOOL_DESCRIPTIONS)
    assert set(handlers) == set(mcp_server.MCP_TOOL_POLICIES)


@pytest.mark.asyncio
async def test_mcp_tool_handlers_accept_codex_style_payload() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)
    payload = {
        "source": "codex-cli",
        "tags": ["mcp-test", "ai-memory-hub"],
        "conversation": [
            {"role": "user", "content": "I'm testing mcp right now"},
            {"role": "assistant", "content": "I can help validate the MCP path."},
            {"role": "user", "content": "use connected mcp to save this conversation"},
        ],
        "metadata": {"saved_at": "2026-05-22T00:00:00Z", "timezone": "Europe/Dublin"},
    }

    validate_result = await handlers["memory_validate"](payload)
    insert_result = await handlers["memory_insert"](payload)

    assert validate_result["status"] == "ok"
    assert validate_result["valid"] is True
    assert insert_result["status"] == "ok"
    memory_id = insert_result["id"]
    assert isinstance(memory_id, str)
    stored = runtime.metadata_store.get(memory_id)
    assert stored["metadata"]["tags"] == ["mcp-test", "ai-memory-hub"]
    assert stored["metadata"]["imported_at"] == "2026-05-22T00:00:00Z"
    assert stored["messages"][0]["text"] == "I'm testing mcp right now"
    assert "content" not in stored["messages"][0]


@pytest.mark.asyncio
async def test_mcp_tool_handlers_generate_id_when_missing() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)
    payload = {
        "source": "codex-cli",
        "messages": [{"role": "user", "text": "auto id test"}],
        "metadata": {"imported_at": "2026-05-22T00:00:00Z"},
    }

    insert_result = await handlers["memory_insert"](payload)

    assert insert_result["status"] == "ok"
    memory_id = insert_result["id"]
    assert isinstance(memory_id, str)
    assert memory_id == runtime.metadata_store.get(memory_id)["id"]


@pytest.mark.asyncio
async def test_mcp_tool_handlers_reject_invalid_explicit_id() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)
    payload = {
        "id": "not-a-uuid",
        "source": "codex-cli",
        "messages": [{"role": "user", "text": "bad id test"}],
        "metadata": {"imported_at": "2026-05-22T00:00:00Z"},
    }

    insert_result = await handlers["memory_insert"](payload)

    assert insert_result["status"] == "error"
    assert insert_result["error_code"] == "invalid_input"
    assert "id must be a valid UUID" in insert_result["error_message"]


@pytest.mark.asyncio
async def test_mcp_tool_handlers_invalid_inputs_return_consistent_errors() -> None:
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}}, runtime=_runtime()
    )
    handlers = build_tool_handlers(agent)

    validate_invalid_type = await handlers["memory_validate"]("invalid")  # type: ignore[arg-type]
    assert validate_invalid_type["status"] == "error"
    assert validate_invalid_type["error_code"] == "invalid_input"
    assert validate_invalid_type["valid"] is False

    invalid_conversation = dict(_conversation())
    invalid_conversation.pop("messages")
    validate_invalid_schema = await handlers["memory_validate"](invalid_conversation)
    assert validate_invalid_schema["status"] == "error"
    assert validate_invalid_schema["error_code"] == "invalid_input"
    assert validate_invalid_schema["valid"] is False
    assert "messages" in validate_invalid_schema["error_message"]

    auto_defaults_payload = {
        "messages": [{"role": "user", "text": "auto defaults test"}],
    }
    insert_with_defaults = await handlers["memory_insert"](auto_defaults_payload)
    assert insert_with_defaults["status"] == "ok"
    assert isinstance(insert_with_defaults["id"], str)

    search_result = await handlers["memory_search"]("", 5)
    assert search_result["status"] == "error"
    assert search_result["error_code"] == "invalid_input"
    assert "query" in search_result["error_message"]
    assert "id" in search_result
    assert "results" in search_result
    assert "cursor" in search_result

    top_k_result = await handlers["memory_search"]("hello", 0)
    assert top_k_result["status"] == "error"
    assert top_k_result["error_code"] == "invalid_input"
    assert "top_k" in top_k_result["error_message"]
    assert "id" in top_k_result
    assert "results" in top_k_result
    assert "cursor" in top_k_result

    retrieve_result = await handlers["memory_retrieve"]("")
    assert retrieve_result["status"] == "error"
    assert retrieve_result["error_code"] == "invalid_input"
    assert "id" in retrieve_result["error_message"]
    assert "results" in retrieve_result
    assert "cursor" in retrieve_result

    ask_result = await handlers["memory_ask"]("", 5)
    assert ask_result["status"] == "error"
    assert ask_result["error_code"] == "invalid_input"
    assert "question" in ask_result["error_message"]

    ask_budget_result = await handlers["memory_ask"]("hello", 5, max_context_tokens=0)
    assert ask_budget_result["status"] == "error"
    assert ask_budget_result["error_code"] == "invalid_input"
    assert "max_context_tokens" in ask_budget_result["error_message"]


@pytest.mark.asyncio
async def test_mcp_tool_handlers_search_pagination_and_filters() -> None:
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}}, runtime=_runtime()
    )
    handlers = build_tool_handlers(agent)

    await handlers["memory_insert"](_conversation())
    await handlers["memory_insert"](_conversation_two())

    page_one = await handlers["memory_search"]("hello", limit=1, top_k=10)
    assert page_one["status"] == "ok"
    assert len(page_one["results"]) == 1
    assert page_one["cursor"] is not None

    page_two = await handlers["memory_search"](
        "hello", limit=1, top_k=10, cursor=page_one["cursor"]
    )
    assert page_two["status"] == "ok"
    assert len(page_two["results"]) == 1
    assert page_two["cursor"] is None
    first_id = page_one["results"][0]["id"]
    second_id = page_two["results"][0]["id"]
    assert first_id != second_id

    filtered_source = await handlers["memory_search"](
        "hello", source="chatgpt", top_k=10, response_format="detailed"
    )
    assert filtered_source["status"] == "ok"
    assert all(
        "hash" not in row["conversation"]["messages"][0]
        for row in filtered_source["results"]
    )
    assert all(
        row["conversation"]["source"] == "chatgpt" for row in filtered_source["results"]
    )

    filtered_date = await handlers["memory_search"](
        "hello",
        date_from="2026-01-02T00:00:00Z",
        date_to="2026-01-02T23:59:59Z",
        top_k=10,
        response_format="detailed",
    )
    assert filtered_date["status"] == "ok"
    assert all(
        row["conversation"]["timestamp"].startswith("2026-01-02")
        for row in filtered_date["results"]
    )

    filtered_tags = await handlers["memory_search"](
        "hello", tags=["beta"], top_k=10, response_format="detailed"
    )
    assert filtered_tags["status"] == "ok"
    assert all(
        "beta" in row["conversation"]["metadata"].get("tags", [])
        for row in filtered_tags["results"]
    )

    wrapped_tags = await handlers["memory_search"](
        "hello",
        tags={"item": ["beta"]},  # type: ignore[arg-type]
        top_k=10,
        response_format="detailed",
    )
    assert wrapped_tags["status"] == "ok"
    assert all(
        "beta" in row["conversation"]["metadata"].get("tags", [])
        for row in wrapped_tags["results"]
    )

    filtered_thread = await handlers["memory_search"](
        "hello", thread_id="thread-beta", top_k=10
    )
    assert filtered_thread["status"] == "ok"
    assert [row["id"] for row in filtered_thread["results"]] == [
        "2f39f5cc-6256-4ca9-a9b2-6211bc6e3702"
    ]

    grouped_threads = await handlers["memory_search"](
        "hello", top_k=10, result_mode="threads"
    )
    assert grouped_threads["status"] == "ok"
    assert "thread_id" in grouped_threads["results"][0]

    filtered_ask = await handlers["memory_ask"](
        "hello",
        top_k=10,
        source="chatgpt",
        tags=["beta"],
        thread_id="thread-beta",
        response_format="detailed",
    )
    assert filtered_ask["status"] == "ok"
    assert [row["id"] for row in filtered_ask["results"]] == [
        "2f39f5cc-6256-4ca9-a9b2-6211bc6e3702"
    ]

    invalid_cursor = await handlers["memory_search"](
        "hello", cursor="not-a-number", limit=1, top_k=10
    )
    assert invalid_cursor["status"] == "error"
    assert invalid_cursor["error_code"] == "invalid_input"
    assert "cursor" in invalid_cursor["error_message"]


@pytest.mark.asyncio
async def test_mcp_tool_handlers_payload_compatibility_gemini_style() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)

    # Gemini-style payload often uses 'content' instead of 'text'
    # and might include extra metadata fields
    payload = {
        "source": "gemini",
        "messages": [
            {"role": "user", "content": "Explain quantum computing"},
            {"role": "assistant", "content": "Quantum computing is..."},
        ],
        "metadata": {
            "model": "gemini-1.5-pro",
            "usage": {"prompt_tokens": 10, "candidates_tokens": 50},
        },
    }

    result = await handlers["memory_insert"](payload)
    assert result["status"] == "ok"

    stored = runtime.metadata_store.get(result["id"])
    assert stored["messages"][0]["text"] == "Explain quantum computing"
    assert "content" not in stored["messages"][0]
    assert stored["source"] == "gemini"


@pytest.mark.asyncio
async def test_mcp_tool_handlers_payload_compatibility_copilot_style() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)

    # Copilot-style payload might use top-level 'tags' (which we normalize)
    # and deep metadata
    payload = {
        "source": "copilot",
        "tags": ["vscode", "python"],
        "messages": [
            {"role": "user", "text": "Fix this bug"},
            {"role": "assistant", "text": "I found the issue in line 42."},
        ],
        "metadata": {"session_id": "session-123", "workspace": "ai-memory-hub"},
    }

    result = await handlers["memory_insert"](payload)
    assert result["status"] == "ok"

    stored = runtime.metadata_store.get(result["id"])
    assert "vscode" in stored["metadata"]["tags"]
    assert stored["metadata"]["session_id"] == "session-123"


@pytest.mark.asyncio
async def test_mcp_tool_handlers_payload_compatibility_chatgpt_style() -> None:
    runtime = _runtime()
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=runtime)
    handlers = build_tool_handlers(agent)

    # ChatGPT-style might use 'conversation' as a key for the message list
    # and 'saved_at' in metadata
    payload = {
        "source": "chatgpt",
        "conversation": [
            {"role": "user", "text": "What is the capital of France?"},
            {"role": "assistant", "text": "The capital of France is Paris."},
        ],
        "metadata": {"saved_at": "2026-05-27T10:00:00Z"},
    }

    result = await handlers["memory_insert"](payload)
    assert result["status"] == "ok"

    stored = runtime.metadata_store.get(result["id"])
    assert len(stored["messages"]) == 2
    assert stored["metadata"]["imported_at"] == "2026-05-27T10:00:00Z"
