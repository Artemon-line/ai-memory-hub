from __future__ import annotations

import jsonschema
import pytest

from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.ingestion.provider_loader import load_ingestion_agent


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


def _valid_conversation() -> dict[str, object]:
    return {
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "source": "mcp",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role": "user", "text": "hello"}],
        "metadata": {"imported_at": "2026-01-01T00:00:00Z"},
    }


@pytest.mark.asyncio
async def test_mvp_ingestion_agent_ingest_messages() -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())

    result = await agent.ingest_messages(_valid_conversation())

    assert result["status"] == "ok"
    assert result["chunks"] == 1


@pytest.mark.asyncio
async def test_mvp_ingestion_agent_rejects_invalid_json() -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    invalid = _valid_conversation()
    del invalid["id"]

    with pytest.raises(jsonschema.ValidationError):
        await agent.ingest_messages(invalid)


def test_provider_loader_supports_mvp_agent() -> None:
    agent = load_ingestion_agent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    assert isinstance(agent, MVPIngestionAgent)
