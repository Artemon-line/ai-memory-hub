from __future__ import annotations

import pytest

from memory.ingestion import mvp_ingestion
from memory.ingestion.validate import load_schema


def _valid_conversation(messages: list[dict[str, str]]) -> dict[str, object]:
    return {
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": messages,
        "metadata": {
            "imported_at": "2026-01-01T00:00:00Z",
        },
    }


def test_valid_json() -> None:
    schema = load_schema()
    messages = [{"role": "user", "text": "hello"}]

    mvp_ingestion.configure_runtime(
        formatter=lambda raw_messages, _schema: _valid_conversation(raw_messages),
        embedder=lambda texts: [[float(len(text))] for text in texts],
        storage=lambda obj, embeddings: {"conversation": obj, "embeddings": embeddings},
    )

    obj = mvp_ingestion.retry_format(messages, schema)
    mvp_ingestion.validate_json(obj, schema)

    assert obj["messages"] == messages


def test_invalid_json_retry_success() -> None:
    schema = load_schema()
    calls = {"count": 0}

    def formatter(messages: list[dict[str, str]], _schema: dict[str, object]) -> dict[str, object]:
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "source": "manual",
                "timestamp": "2026-01-01T00:00:00Z",
                "messages": messages,
                "metadata": {"imported_at": "2026-01-01T00:00:00Z"},
            }
        return _valid_conversation(messages)

    mvp_ingestion.configure_runtime(
        formatter=formatter,
        embedder=lambda texts: [[float(len(text))] for text in texts],
        storage=lambda obj, embeddings: {"conversation": obj, "embeddings": embeddings},
    )

    obj = mvp_ingestion.retry_format([{"role": "user", "text": "retry"}], schema, max_attempts=3)

    assert calls["count"] == 2
    assert obj["id"] == "d9fd4c95-9cb3-4fd5-b967-3027f8863210"


def test_invalid_json_retry_fail_after_three_attempts() -> None:
    schema = load_schema()
    calls = {"count": 0}

    def formatter(messages: list[dict[str, str]], _schema: dict[str, object]) -> dict[str, object]:
        calls["count"] += 1
        return {
            "source": "manual",
            "timestamp": "2026-01-01T00:00:00Z",
            "messages": messages,
            "metadata": {"imported_at": "2026-01-01T00:00:00Z"},
        }

    mvp_ingestion.configure_runtime(
        formatter=formatter,
        embedder=lambda texts: [[float(len(text))] for text in texts],
        storage=lambda obj, embeddings: {"conversation": obj, "embeddings": embeddings},
    )

    with pytest.raises(Exception):
        mvp_ingestion.retry_format([{"role": "user", "text": "retry"}], schema, max_attempts=3)

    assert calls["count"] == 3


def test_embedding_and_storage_calls() -> None:
    call_log = {
        "embedder": [],
        "storage": [],
    }

    def embedder(texts: list[str]) -> list[list[float]]:
        call_log["embedder"].append(texts)
        return [[float(index)] for index, _ in enumerate(texts)]

    def storage(obj: dict[str, object], embeddings: list[dict[str, object]]) -> dict[str, object]:
        call_log["storage"].append((obj, embeddings))
        return {"stored": True, "conversation": obj, "embeddings": embeddings}

    mvp_ingestion.configure_runtime(
        formatter=lambda raw_messages, _schema: _valid_conversation(raw_messages),
        embedder=embedder,
        storage=storage,
    )

    obj = _valid_conversation(
        [
            {"role": "user", "text": "one"},
            {"role": "assistant", "text": "two"},
        ]
    )
    chunks = mvp_ingestion.chunk_messages(obj)
    embeddings = mvp_ingestion.embed_chunks(chunks)
    result = mvp_ingestion.store(obj, embeddings)

    assert call_log["embedder"] == [["one", "two"]]
    assert len(call_log["storage"]) == 1
    assert result["stored"] is True
    assert len(result["embeddings"]) == 2


def test_deterministic_behavior() -> None:
    obj = _valid_conversation(
        [
            {"role": "user", "text": "same"},
            {"role": "assistant", "text": "input"},
        ]
    )

    mvp_ingestion.configure_runtime(
        formatter=lambda raw_messages, _schema: _valid_conversation(raw_messages),
        embedder=lambda texts: [[float(len(text))] for text in texts],
        storage=lambda stored_obj, embeddings: {"conversation": stored_obj, "embeddings": embeddings},
    )

    chunks_a = mvp_ingestion.chunk_messages(obj)
    chunks_b = mvp_ingestion.chunk_messages(obj)
    embeddings_a = mvp_ingestion.embed_chunks(chunks_a)
    embeddings_b = mvp_ingestion.embed_chunks(chunks_b)

    assert chunks_a == chunks_b
    assert embeddings_a == embeddings_b
