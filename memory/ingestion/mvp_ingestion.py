from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.vector_store import InMemoryVectorStore, LanceDBVectorStore
from memory.config import HubConfig, load_config, parse_config
from memory.ingestion.validate import validate_conversation


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...


class LocalEmbeddingProvider:
    """Deterministic local embedding provider for offline/local-first mode."""

    def __init__(self, dimensions: int = 32):
        self.dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_to_vector(text, self.dimensions) for text in texts]


class OpenAIEmbeddingProvider:
    def __init__(self, model: str = "text-embedding-3-small", api_key: str | None = None):
        from openai import OpenAI

        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is required when providers.embeddings=openai")

        self.client = OpenAI(api_key=key)
        self.model = model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [list(item.embedding) for item in response.data]


@dataclass
class RuntimeDependencies:
    embedding_provider: EmbeddingProvider
    metadata_store: Any
    vector_store: Any


_RUNTIME: RuntimeDependencies | None = None


def build_runtime(config: HubConfig | dict[str, Any] | None = None) -> RuntimeDependencies:
    cfg = parse_config(config) if isinstance(config, dict) else (config or load_config())
    data_dir = Path(cfg.paths.data_dir)
    metadata_store = SQLiteMetadataStore(data_dir / "metadata.sqlite3")

    if cfg.providers.vector_db == "lancedb":
        try:
            vector_store = LanceDBVectorStore(data_dir / "lancedb")
        except RuntimeError:
            vector_store = InMemoryVectorStore()
    else:
        vector_store = InMemoryVectorStore()

    if cfg.providers.embeddings == "openai":
        embedding_provider: EmbeddingProvider = OpenAIEmbeddingProvider()
    else:
        embedding_provider = LocalEmbeddingProvider()

    return RuntimeDependencies(
        embedding_provider=embedding_provider,
        metadata_store=metadata_store,
        vector_store=vector_store,
    )


def configure_runtime(*, runtime: RuntimeDependencies | None = None, config: HubConfig | dict[str, Any] | None = None) -> RuntimeDependencies:
    global _RUNTIME
    _RUNTIME = runtime or build_runtime(config)
    return _RUNTIME


def _runtime() -> RuntimeDependencies:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = build_runtime()
    return _RUNTIME


def validate_json(obj: dict[str, Any]) -> None:
    validate_conversation(obj)


def chunk_messages(obj: dict[str, Any]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for index, message in enumerate(obj["messages"]):
        chunks.append(
            {
                "chunk_index": index,
                "role": str(message["role"]),
                "text": str(message["text"]),
            }
        )
    return chunks


def embed_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runtime = _runtime()
    texts = [chunk["text"] for chunk in chunks]
    vectors = runtime.embedding_provider.embed_texts(texts)
    if len(vectors) != len(chunks):
        raise ValueError("Embedding provider must return one vector per chunk")

    embeddings: list[dict[str, Any]] = []
    for chunk, vector in zip(chunks, vectors):
        embeddings.append(
            {
                "chunk_index": chunk["chunk_index"],
                "role": chunk["role"],
                "text": chunk["text"],
                "vector": [float(v) for v in vector],
            }
        )
    return embeddings


def store_metadata(obj: dict[str, Any]) -> str:
    runtime = _runtime()
    return runtime.metadata_store.insert(obj)


def store_vectors(metadata_id: str, embeddings: list[dict[str, Any]]) -> None:
    runtime = _runtime()
    runtime.vector_store.insert(metadata_id, embeddings)


def ingest_messages(conversation_json: dict[str, Any]) -> dict[str, Any]:
    # 1. Validate JSON against schema
    validate_json(conversation_json)

    # 2. Chunk messages
    chunks = chunk_messages(conversation_json)

    # 3. Embed chunks
    embeddings = embed_chunks(chunks)

    # 4. Store metadata + vectors
    metadata_id = store_metadata(conversation_json)
    store_vectors(metadata_id, embeddings)

    # 5. Return stored object
    return {
        "status": "ok",
        "id": metadata_id,
        "chunks": len(chunks),
    }


def search(query: str, top_k: int = 5) -> dict[str, Any]:
    runtime = _runtime()
    query_vector = runtime.embedding_provider.embed_texts([query])[0]
    matches = runtime.vector_store.search(query_vector, top_k=top_k)

    ids = [str(match["memory_id"]) for match in matches]
    conversations = runtime.metadata_store.get_many(ids)

    results: list[dict[str, Any]] = []
    for match in matches:
        memory_id = str(match["memory_id"])
        results.append(
            {
                "id": memory_id,
                "score": float(match["score"]),
                "chunk_index": int(match["chunk_index"]),
                "role": match["role"],
                "text": match["text"],
                "conversation": conversations.get(memory_id),
            }
        )

    return {"status": "ok", "results": results}


def retrieve(memory_id: str) -> dict[str, Any] | None:
    runtime = _runtime()
    return runtime.metadata_store.get(memory_id)


def _hash_to_vector(text: str, dimensions: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = list(digest)
    while len(values) < dimensions:
        digest = hashlib.sha256(digest).digest()
        values.extend(list(digest))
    return [(values[index] / 255.0) for index in range(dimensions)]

