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


@pytest.mark.asyncio
async def test_mcp_tool_handlers_insert_search_retrieve() -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    handlers = build_tool_handlers(agent)

    insert_result = await handlers["memory.insert"](_conversation())
    assert insert_result["status"] == "ok"

    search_result = await handlers["memory.search"]("hello", 5)
    assert search_result["status"] == "ok"

    retrieve_result = await handlers["memory.retrieve"]("d9fd4c95-9cb3-4fd5-b967-3027f8863210")
    assert retrieve_result["status"] == "ok"
    assert retrieve_result["memory"]["id"] == "d9fd4c95-9cb3-4fd5-b967-3027f8863210"
