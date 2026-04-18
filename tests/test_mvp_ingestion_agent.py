from __future__ import annotations

from datetime import datetime, timezone

import pytest

from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.ingestion.provider_loader import load_ingestion_agent


class HookedMVPAgent(MVPIngestionAgent):
    def preprocess_messages(self, messages):
        return [
            {
                "role": message["role"],
                "text": message["text"].strip(),
            }
            for message in messages
        ]

    def postprocess_result(self, result):
        output = dict(result)
        output["postprocessed"] = True
        return output


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


def _fixed_now() -> datetime:
    return datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_mvp_ingestion_agent_ingest_messages() -> None:
    logs = {
        "embedder": [],
        "storage": [],
    }

    def formatter(messages: list[dict[str, str]], _schema: dict[str, object]) -> dict[str, object]:
        return _valid_conversation(messages)

    def embedder(texts: list[str]) -> list[list[float]]:
        logs["embedder"].append(texts)
        return [[float(index)] for index, _ in enumerate(texts)]

    def storage(obj: dict[str, object], embeddings: list[dict[str, object]]) -> dict[str, object]:
        logs["storage"].append((obj, embeddings))
        return {
            "stored": True,
            "conversation": obj,
            "embeddings": embeddings,
        }

    agent = HookedMVPAgent(
        config={"providers": {"agent": "mvp"}},
        formatter=formatter,
        embedder=embedder,
        storage=storage,
        now_provider=_fixed_now,
    )

    result = await agent.ingest_messages(
        [{"role": "user", "text": "  hello  "}],
        source="mcp",
        title="MVP Conversation",
        metadata={"tags": ["phase1"]},
    )

    assert logs["embedder"] == [["hello"]]
    assert len(logs["storage"]) == 1
    assert result["stored"] is True
    assert result["conversation"]["source"] == "mcp"
    assert result["conversation"]["title"] == "MVP Conversation"
    assert result["conversation"]["metadata"]["tags"] == ["phase1"]
    assert result["conversation"]["metadata"]["agent"] == "mvp"
    assert result["postprocessed"] is True


@pytest.mark.asyncio
async def test_mvp_ingestion_agent_retry_success() -> None:
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

    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}},
        formatter=formatter,
        embedder=lambda texts: [[0.0] for _ in texts],
        storage=lambda obj, embeddings: {"conversation": obj, "embeddings": embeddings},
        now_provider=_fixed_now,
    )

    result = await agent.ingest_messages(
        [{"role": "user", "text": "retry"}],
        source="manual",
    )

    assert calls["count"] == 2
    assert result["conversation"]["id"] == "d9fd4c95-9cb3-4fd5-b967-3027f8863210"


@pytest.mark.asyncio
async def test_mvp_ingestion_agent_retry_fail_after_three_attempts() -> None:
    calls = {"count": 0}

    def formatter(messages: list[dict[str, str]], _schema: dict[str, object]) -> dict[str, object]:
        calls["count"] += 1
        return {
            "source": "manual",
            "timestamp": "2026-01-01T00:00:00Z",
            "messages": messages,
            "metadata": {"imported_at": "2026-01-01T00:00:00Z"},
        }

    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}},
        formatter=formatter,
        embedder=lambda texts: [[0.0] for _ in texts],
        storage=lambda obj, embeddings: {"conversation": obj, "embeddings": embeddings},
        now_provider=_fixed_now,
    )

    with pytest.raises(Exception):
        await agent.ingest_messages(
            [{"role": "user", "text": "retry"}],
            source="manual",
        )

    assert calls["count"] == 3


def test_provider_loader_supports_mvp_agent() -> None:
    agent = load_ingestion_agent(
        config={"providers": {"agent": "mvp"}},
        formatter=lambda messages, _schema: _valid_conversation(messages),
        embedder=lambda texts: [[0.0] for _ in texts],
        storage=lambda obj, embeddings: {"conversation": obj, "embeddings": embeddings},
        now_provider=_fixed_now,
    )

    assert isinstance(agent, MVPIngestionAgent)
