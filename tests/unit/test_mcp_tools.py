from __future__ import annotations

import pytest

from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.interfaces.mcp_server import _deterministic_sort, build_tool_handlers


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
        "metadata": {"imported_at": "2026-01-02T00:00:00Z", "tags": ["beta"]},
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
async def test_mcp_tool_handlers_insert_search_retrieve() -> None:
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}}, runtime=_runtime()
    )
    handlers = build_tool_handlers(agent)

    validate_result = await handlers["memory_validate"](_conversation())
    assert validate_result["status"] == "ok"
    assert validate_result["valid"] is True

    insert_result = await handlers["memory_insert"](_conversation())
    assert insert_result["status"] == "ok"

    search_result = await handlers["memory_search"]("hello", 5)
    assert search_result["status"] == "ok"

    retrieve_result = await handlers["memory_retrieve"](
        "d9fd4c95-9cb3-4fd5-b967-3027f8863210"
    )
    assert retrieve_result["status"] == "ok"
    assert retrieve_result["memory"]["id"] == "d9fd4c95-9cb3-4fd5-b967-3027f8863210"
    assert "hash" not in retrieve_result["memory"]["messages"][0]
    assert "conversation_hash" not in retrieve_result["memory"]["metadata"]
    assert "message_hashes" not in retrieve_result["memory"]["metadata"]
    assert "results" in retrieve_result
    assert "cursor" in retrieve_result
    assert "error_code" in retrieve_result
    assert "error_message" in retrieve_result

    ask_result = await handlers["memory_ask"]("what was stored?", 3)
    assert ask_result["status"] == "ok"
    assert "answer" in ask_result
    assert len(ask_result["results"]) == 1
    assert ask_result["results"][0]["id"] == "d9fd4c95-9cb3-4fd5-b967-3027f8863210"
    assert ask_result["results"][0]["text"] == "hello mcp"
    assert "hash" not in ask_result["results"][0]["conversation"]["messages"][0]
    assert isinstance(ask_result["citations"], list)
    assert ask_result["citations"][0]["id"] == ask_result["results"][0]["id"]
    assert ask_result["citations"][0]["text"] == ask_result["results"][0]["text"]

    budgeted_ask_result = await handlers["memory_ask"](
        "what was stored?", 3, max_context_tokens=100
    )
    assert budgeted_ask_result["status"] == "ok"
    assert budgeted_ask_result["context_tokens_used"] <= 100
    assert budgeted_ask_result["chunks_selected"] == 1


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
        "hello", source="chatgpt", top_k=10
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
    )
    assert filtered_date["status"] == "ok"
    assert all(
        row["conversation"]["timestamp"].startswith("2026-01-02")
        for row in filtered_date["results"]
    )

    filtered_tags = await handlers["memory_search"]("hello", tags=["beta"], top_k=10)
    assert filtered_tags["status"] == "ok"
    assert all(
        "beta" in row["conversation"]["metadata"].get("tags", [])
        for row in filtered_tags["results"]
    )

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
