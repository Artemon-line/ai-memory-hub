import pytest

from memory.ingestion.base_agent import BaseIngestionAgent


class DummyAgent(BaseIngestionAgent):
    async def ingest_messages(self, conversation_json):
        return {"ok": True, "conversation": conversation_json}

    async def search(self, query: str, *, top_k: int = 5):
        return {"query": query, "top_k": top_k}

    async def retrieve(self, memory_id: str):
        return {"id": memory_id}


def test_base_agent_is_abstract() -> None:
    with pytest.raises(TypeError):
        BaseIngestionAgent(config={})


@pytest.mark.asyncio
async def test_dummy_agent_ingest_messages() -> None:
    agent = DummyAgent(config={"test": True})
    payload = {"messages": [{"role": "user", "text": "hello"}]}

    result = await agent.ingest_messages(payload)

    assert result["ok"] is True
    assert result["conversation"] == payload


def test_preprocess_default_noop() -> None:
    agent = DummyAgent(config={})
    payload = {"foo": "bar"}
    assert agent.preprocess_messages(payload) == payload


def test_postprocess_default_noop() -> None:
    agent = DummyAgent(config={})
    result = {"ok": True}
    assert agent.postprocess_result(result) == result
