from __future__ import annotations

import pytest

from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.interfaces.mcp_server import build_tool_handlers


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

    def get(self, memory_id: str):
        return self.rows.get(memory_id)

    def get_many(self, ids: list[str]):
        return {id_: self.rows[id_] for id_ in ids if id_ in self.rows}


class StubVectorStore:
    def __init__(self):
        self.rows: list[dict[str, object]] = []

    def insert(self, metadata_id: str, embeddings: list[dict[str, object]]) -> None:
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
        embedding_provider=StubEmbedder(), # type: ignore
        metadata_store=StubMetadataStore(),
        vector_store=StubVectorStore(),
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


@pytest.mark.asyncio
async def test_mcp_tool_handlers_insert_search_retrieve() -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    handlers = build_tool_handlers(agent)

    validate_result = await handlers["memory_validate"](_conversation())
    assert validate_result["status"] == "ok"
    assert validate_result["valid"] is True

    insert_result = await handlers["memory_insert"](_conversation())
    assert insert_result["status"] == "ok"

    search_result = await handlers["memory_search"]("hello", 5)
    assert search_result["status"] == "ok"

    retrieve_result = await handlers["memory_retrieve"]("d9fd4c95-9cb3-4fd5-b967-3027f8863210")
    assert retrieve_result["status"] == "ok"
    assert retrieve_result["memory"]["id"] == "d9fd4c95-9cb3-4fd5-b967-3027f8863210"
    assert "results" in retrieve_result
    assert "cursor" in retrieve_result
    assert "error_code" in retrieve_result
    assert "error_message" in retrieve_result

    ask_result = await handlers["memory_ask"]("what was stored?", 3)
    assert ask_result["status"] == "ok"
    assert "answer" in ask_result
    assert isinstance(ask_result["citations"], list)


@pytest.mark.asyncio
async def test_mcp_tool_handlers_invalid_inputs_return_consistent_errors() -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    handlers = build_tool_handlers(agent)

    validate_invalid_type = await handlers["memory_validate"]("invalid")  # type: ignore[arg-type]
    assert validate_invalid_type["status"] == "error"
    assert validate_invalid_type["error_code"] == "invalid_input"
    assert validate_invalid_type["valid"] is False

    invalid_conversation = dict(_conversation())
    invalid_conversation.pop("id")
    validate_invalid_schema = await handlers["memory_validate"](invalid_conversation)
    assert validate_invalid_schema["status"] == "error"
    assert validate_invalid_schema["error_code"] == "invalid_input"
    assert validate_invalid_schema["valid"] is False
    assert "schema validation failed" in validate_invalid_schema["error_message"]

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


@pytest.mark.asyncio
async def test_mcp_tool_handlers_search_pagination_and_filters() -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    handlers = build_tool_handlers(agent)

    await handlers["memory_insert"](_conversation())
    await handlers["memory_insert"](_conversation_two())

    page_one = await handlers["memory_search"]("hello", limit=1, top_k=10)
    assert page_one["status"] == "ok"
    assert len(page_one["results"]) == 1
    assert page_one["cursor"] is not None

    page_two = await handlers["memory_search"]("hello", limit=1, top_k=10, cursor=page_one["cursor"])
    assert page_two["status"] == "ok"
    assert len(page_two["results"]) == 1
    assert page_two["cursor"] is None
    first_id = page_one["results"][0]["id"]
    second_id = page_two["results"][0]["id"]
    assert first_id != second_id

    filtered_source = await handlers["memory_search"]("hello", source="chatgpt", top_k=10)
    assert filtered_source["status"] == "ok"
    assert all(row["conversation"]["source"] == "chatgpt" for row in filtered_source["results"])

    filtered_date = await handlers["memory_search"](
        "hello",
        date_from="2026-01-02T00:00:00Z",
        date_to="2026-01-02T23:59:59Z",
        top_k=10,
    )
    assert filtered_date["status"] == "ok"
    assert all(row["conversation"]["timestamp"].startswith("2026-01-02") for row in filtered_date["results"])

    filtered_tags = await handlers["memory_search"]("hello", tags=["beta"], top_k=10)
    assert filtered_tags["status"] == "ok"
    assert all("beta" in row["conversation"]["metadata"].get("tags", []) for row in filtered_tags["results"])
