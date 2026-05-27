import pytest
from typing import Any
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.ingestion import mvp_ingestion
from memory.inference.providers import LocalInferenceProvider

class StubEmbedder:
    dimension = 32
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self.dimension for _ in texts]

class StubMetadataStore:
    def __init__(self):
        self.rows = {}
    def insert(self, conversation_json: dict[str, Any]) -> str:
        memory_id = conversation_json.get("id", "test-id")
        self.rows[memory_id] = conversation_json
        return memory_id
    def get(self, memory_id: str):
        return self.rows.get(memory_id)

class StubVectorStore:
    expected_dimensionality = 32
    def insert(self, metadata_id: str, embeddings: list[dict[str, Any]]) -> None:
        pass

def _runtime():
    return mvp_ingestion.RuntimeDependencies(
        embedding_provider=StubEmbedder(),
        inference_provider=LocalInferenceProvider(),
        metadata_store=StubMetadataStore(),
        vector_store=StubVectorStore(),
        health_state={"mode": "ok"}
    )

@pytest.mark.asyncio
async def test_ingest_raw_mvp():
    runtime = _runtime()
    agent = MVPIngestionAgent(config={}, runtime=runtime)
    
    # LocalInferenceProvider returns a fixed mock JSON
    result = await agent.ingest_raw("Some raw text chatter")
    
    assert result["status"] == "ok"
    assert result["chunks"] == 1
    
    stored = runtime.metadata_store.get(result["id"])
    assert stored["messages"][0]["text"] == "mocked text"
    assert "mock" in stored["metadata"]["topics"]

@pytest.mark.asyncio
async def test_parse_raw_only():
    runtime = _runtime()
    agent = MVPIngestionAgent(config={}, runtime=runtime)
    
    result = await agent.parse_raw("Some raw text chatter")
    
    assert "messages" in result
    assert result["messages"][0]["text"] == "mocked text"
    # Verify it's NOT stored
    assert len(runtime.metadata_store.rows) == 0
