import pytest

from memory.ingestion.base_agent import BaseIngestionAgent


# ---------------------------------------------------------
# Dummy implementation for testing
# ---------------------------------------------------------

class DummyAgent(BaseIngestionAgent):
    async def ingest_messages(self, messages, *, source, title=None, metadata=None):
        return {
            "ok": True,
            "messages": messages,
            "source": source,
            "title": title,
            "metadata": metadata,
            "config": self.config,
        }


# ---------------------------------------------------------
# Tests
# ---------------------------------------------------------

def test_base_agent_is_abstract():
    """
    BaseIngestionAgent must not be instantiable directly.
    """
    with pytest.raises(TypeError):
        BaseIngestionAgent(config={})  # abstract class → should fail


@pytest.mark.asyncio
async def test_dummy_agent_ingest_messages():
    """
    A subclass must implement ingest_messages and return a dict.
    """
    agent = DummyAgent(config={"test": True})

    messages = [
        {"role": "user", "text": "Hello"},
        {"role": "assistant", "text": "Hi there"},
    ]

    result = await agent.ingest_messages(
        messages,
        source="test-source",
        title="Test Conversation",
        metadata={"foo": "bar"},
    )

    assert result["ok"] is True
    assert result["messages"] == messages
    assert result["source"] == "test-source"
    assert result["title"] == "Test Conversation"
    assert result["metadata"] == {"foo": "bar"}
    assert result["config"] == {"test": True}


def test_preprocess_default_noop():
    """
    preprocess_messages should be a no-op by default.
    """
    agent = DummyAgent(config={})
    messages = [{"role": "user", "text": "Hello"}]

    processed = agent.preprocess_messages(messages)
    assert processed == messages


def test_postprocess_default_noop():
    """
    postprocess_result should be a no-op by default.
    """
    agent = DummyAgent(config={})
    result = {"ok": True}

    processed = agent.postprocess_result(result)
    assert processed == result

