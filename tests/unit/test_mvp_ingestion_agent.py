from __future__ import annotations

import asyncio
import threading

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
        embedding_provider=StubEmbedder(), # type: ignore
        metadata_store=StubMetadataStore(),
        vector_store=StubVectorStore(),
        health_state={"mode": "ok", "vector_fallback_active": False},
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
async def test_mvp_ingestion_agent_offloads_blocking_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    insert_started = threading.Event()
    release_insert = threading.Event()

    def slow_ingest_messages(*_args, **_kwargs) -> dict[str, object]:
        insert_started.set()
        assert release_insert.wait(timeout=5.0)
        return {"status": "ok", "id": "slow-memory", "chunks": 1}

    def search(*_args, **_kwargs) -> dict[str, object]:
        return {"status": "ok", "results": []}

    monkeypatch.setattr(agent._service, "ingest_messages", slow_ingest_messages)
    monkeypatch.setattr(agent._service, "search", search)

    insert_task = asyncio.create_task(agent.ingest_messages(_valid_conversation()))
    assert await asyncio.wait_for(asyncio.to_thread(insert_started.wait), timeout=5.0)
    try:
        assert await asyncio.wait_for(agent.search("hello"), timeout=5.0) == {
            "status": "ok",
            "results": [],
        }
        assert not insert_task.done()
    finally:
        release_insert.set()
    assert await insert_task == {"status": "ok", "id": "slow-memory", "chunks": 1}


@pytest.mark.asyncio
async def test_mvp_ingestion_agent_serializes_memory_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    first_entered = threading.Event()
    second_attempted = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    write_lock = threading.Lock()
    counter_lock = threading.Lock()
    acquisition_count = 0

    class ObservedWriteLock:
        def __enter__(self) -> None:
            nonlocal acquisition_count
            with counter_lock:
                acquisition_count += 1
                if acquisition_count == 2:
                    second_attempted.set()
            write_lock.acquire()

        def __exit__(self, *_args: object) -> None:
            write_lock.release()

    def coordinated_ingest_messages(conversation_json, **_kwargs) -> dict[str, object]:
        if conversation_json["id"] == first["id"]:
            first_entered.set()
            assert release_first.wait(timeout=5.0)
        else:
            second_entered.set()
        return {"status": "ok", "id": conversation_json["id"], "chunks": 1}

    first = _valid_conversation()
    second = {**_valid_conversation(), "id": "2f39f5cc-6256-4ca9-a9b2-6211bc6e3702"}
    monkeypatch.setattr(agent, "_memory_write_lock", ObservedWriteLock())
    monkeypatch.setattr(agent._service, "ingest_messages", coordinated_ingest_messages)

    first_task = asyncio.create_task(agent.ingest_messages(first))
    assert await asyncio.wait_for(asyncio.to_thread(first_entered.wait), timeout=5.0)
    second_task = asyncio.create_task(agent.ingest_messages(second))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(second_attempted.wait), timeout=5.0)
        assert not second_entered.is_set()
    finally:
        release_first.set()
    results = await asyncio.wait_for(
        asyncio.gather(first_task, second_task),
        timeout=5.0,
    )

    assert [result["id"] for result in results] == [first["id"], second["id"]]
    assert second_entered.is_set()


@pytest.mark.asyncio
async def test_mvp_ingestion_agent_rejects_invalid_json() -> None:
    agent = MVPIngestionAgent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    invalid = _valid_conversation()
    del invalid["messages"]

    with pytest.raises((jsonschema.ValidationError, ValueError)):
        await agent.ingest_messages(invalid)


def test_provider_loader_supports_mvp_agent() -> None:
    agent = load_ingestion_agent(config={"providers": {"agent": "mvp"}}, runtime=_runtime())
    assert isinstance(agent, MVPIngestionAgent)
