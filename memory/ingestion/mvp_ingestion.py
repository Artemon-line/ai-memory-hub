from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, Sequence
from uuid import uuid4

from memory.backend.dry_run import DryRunMetadataStore, DryRunVectorStore
from memory.backend.errors import SchemaVersionError, VectorDimensionError
from memory.backend.log_safety import redact_secrets
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.postgres_metadata_store import PostgresMetadataStore
from memory.backend.vector_store import InMemoryVectorStore, LanceDBVectorStore
from memory.config import HubConfig, load_config, parse_config
from memory.ingestion.validate import (
    load_schema,
    set_schema_path,
    validate_conversation,
    validate_schema_compatibility,
)

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    dimension: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbeddingProvider:
    """Deterministic local embedding provider for offline/local-first mode."""

    def __init__(self, dimensions: int = 32):
        self.dimension = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_to_vector(text, self.dimension) for text in texts]


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        embedding_model: str = "text-embedding-3-small",
        dimension: int = 1536,
        base_url: str = "https://api.openai.com",
        api_key: str | None = None,
    ):
        from openai import OpenAI

        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            logger.warning(
                "OPENAI_API_KEY is required when providers.embeddings=openai"
            )

        self.client = OpenAI(base_url=base_url, api_key=key)
        self.embedding_model = embedding_model
        self.dimension = dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            model=self.embedding_model, input=texts, dimensions=self.dimension
        )
        return [list(item.embedding) for item in response.data]


@dataclass
class RuntimeDependencies:
    embedding_provider: EmbeddingProvider
    metadata_store: Any
    vector_store: Any
    health_state: dict[str, Any]


_RUNTIME: RuntimeDependencies | None = None
_ASK_MAX_SCORE = 7.5
_TOPIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("python", re.compile(r"\bpython\b", re.IGNORECASE)),
    (
        "javascript",
        re.compile(r"\b(javascript|js|node\.?js|typescript|ts)\b", re.IGNORECASE),
    ),
    ("sql", re.compile(r"\b(sql|sqlite|postgres|mysql|database|db)\b", re.IGNORECASE)),
    ("api", re.compile(r"\b(api|rest|endpoint|http|json-rpc)\b", re.IGNORECASE)),
    ("mcp", re.compile(r"\b(mcp|model context protocol)\b", re.IGNORECASE)),
    ("docker", re.compile(r"\b(docker|container|kubernetes|k8s)\b", re.IGNORECASE)),
    (
        "testing",
        re.compile(
            r"\b(test|tests|pytest|unit test|integration test)\b", re.IGNORECASE
        ),
    ),
    (
        "machine-learning",
        re.compile(r"\b(ai|llm|rag|embedding|vector)\b", re.IGNORECASE),
    ),
    (
        "frontend",
        re.compile(r"\b(frontend|ui|css|html|react|vue|angular)\b", re.IGNORECASE),
    ),
    ("backend", re.compile(r"\b(backend|fastapi|flask|server)\b", re.IGNORECASE)),
]


def build_runtime(
    config: HubConfig | dict[str, Any] | None = None,
) -> RuntimeDependencies:
    cfg = (
        parse_config(config) if isinstance(config, dict) else (config or load_config())
    )
    set_schema_path(cfg.schema_config.file)
    validate_schema_compatibility(load_schema())
    data_dir = Path(cfg.paths.data_dir)
    if cfg.providers.metadata_db == "postgres":
        metadata_store = PostgresMetadataStore(cfg.providers.metadata_dsn)
    else:
        metadata_store = SQLiteMetadataStore(data_dir / "metadata.sqlite3")
    _validate_metadata_schema(
        metadata_store=metadata_store,
        supported_versions=cfg.storage.metadata_schema_versions,
    )

    if cfg.providers.embeddings == "openai":
        embedding_provider: EmbeddingProvider = OpenAIEmbeddingProvider(
            cfg.providers.embedding_model,
            cfg.providers.embedding_dimension,
            cfg.openai.base_url,
            cfg.openai.api_key,
        )
    else:
        embedding_provider = LocalEmbeddingProvider()

    expected_dimension = embedding_provider.dimension
    vector_fallback_active = False
    fallback_reasons: list[str] = []
    if cfg.providers.vector_db == "lancedb":
        try:
            vector_store = LanceDBVectorStore(
                data_dir / "lancedb", dimension=expected_dimension
            )
        except RuntimeError as exc:
            if not cfg.storage.vector.allow_fallback:
                raise
            logger.warning(
                "Vector store initialization failed (%s); falling back to in-memory provider",
                redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
            vector_store = InMemoryVectorStore(dimension=expected_dimension)
            vector_fallback_active = True
            fallback_reasons.append(type(exc).__name__)
    else:
        vector_store = InMemoryVectorStore(dimension=expected_dimension)

    _validate_vector_dimension(
        embedding_dimension=expected_dimension, vector_store=vector_store
    )

    if cfg.storage.dry_run:
        logger.warning("DRY-RUN enabled: write operations will be skipped")
        metadata_store = DryRunMetadataStore(metadata_store)
        vector_store = DryRunVectorStore(vector_store)

    mode = "ok"
    if vector_fallback_active:
        mode = "degraded"
    if cfg.storage.dry_run:
        mode = "dry_run"
    health_state = {
        "mode": mode,
        "metadata_provider": cfg.providers.metadata_db,
        "vector_provider": "memory"
        if vector_fallback_active or cfg.providers.vector_db != "lancedb"
        else "lancedb",
        "vector_fallback_active": vector_fallback_active,
        "reasons": fallback_reasons,
    }

    return RuntimeDependencies(
        embedding_provider=embedding_provider,
        metadata_store=metadata_store,
        vector_store=vector_store,
        health_state=health_state,
    )


def configure_runtime(
    *,
    runtime: RuntimeDependencies | None = None,
    config: HubConfig | dict[str, Any] | None = None,
) -> RuntimeDependencies:
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


def infer_topics(messages: list[dict[str, Any]]) -> list[str]:
    text_blob = " ".join(str(message.get("text", "")) for message in messages)
    topics: list[str] = []
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(text_blob):
            topics.append(topic)
    return topics


def enrich_topics(obj: dict[str, Any]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        return

    existing = metadata.get("topics")
    inferred = infer_topics(obj.get("messages", []))
    if not inferred and not isinstance(existing, list):
        return

    merged: list[str] = []
    for topic in existing if isinstance(existing, list) else []:
        if isinstance(topic, str) and topic not in merged:
            merged.append(topic)
    for topic in inferred:
        if topic not in merged:
            merged.append(topic)

    metadata["topics"] = merged


def chunk_messages(obj: dict[str, Any]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    messages = obj.get("messages")
    if not isinstance(messages, list):
        raise ValueError("Missing or invalid 'messages' key in conversation object")
    for index, message in enumerate(messages):
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


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_conversation_json(
    payload: dict[str, Any], *, source: str | None = None
) -> dict[str, Any]:
    """Normalize a conversation JSON object with default values."""
    normalized = dict(payload)
    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    top_level_tags = normalized.pop("tags", None)
    if top_level_tags is not None and "tags" not in metadata:
        metadata["tags"] = top_level_tags

    if "messages" not in normalized and isinstance(
        normalized.get("conversation"), list
    ):
        normalized["messages"] = normalized["conversation"]
    normalized.pop("conversation", None)

    messages = normalized.get("messages")
    if isinstance(messages, list):
        normalized["messages"] = [_normalize_message(item) for item in messages]

    normalized.setdefault("id", str(uuid4()))
    normalized.setdefault("source", source or "unknown")
    normalized.setdefault("timestamp", _utc_now_iso())
    if "imported_at" not in metadata and isinstance(metadata.get("saved_at"), str):
        metadata["imported_at"] = metadata["saved_at"]
    metadata.setdefault("imported_at", _utc_now_iso())
    normalized["metadata"] = metadata
    return normalized


def _normalize_message(message: Any) -> Any:
    if not isinstance(message, dict):
        return message
    normalized = dict(message)
    if "text" not in normalized and "content" in normalized:
        normalized["text"] = normalized["content"]
    normalized.pop("content", None)
    return normalized


def ingest_messages(conversation_json: dict[str, Any]) -> dict[str, Any]:
    # 0. Normalize
    conversation_json = normalize_conversation_json(conversation_json)
    
    # 1. Validate JSON against schema
    validate_json(conversation_json)

    # 2. Enrich metadata topics from message text
    enrich_topics(conversation_json)

    # 3. Chunk messages
    chunks = chunk_messages(conversation_json)

    # 4. Embed chunks
    embeddings = embed_chunks(chunks)

    # 5. Store metadata + vectors
    metadata_id = store_metadata(conversation_json)
    store_vectors(metadata_id, embeddings)

    # 6. Return stored object
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


def ask(question: str, top_k: int = 5) -> dict[str, Any]:
    search_result = search(query=question, top_k=top_k)
    matches = search_result.get("results", [])
    if not matches:
        return {
            "status": "ok",
            "answer": "I could not find relevant memory for that question.",
            "citations": [],
        }

    citations: list[dict[str, Any]] = []
    context_lines: list[str] = []
    for row in matches:
        citation = {
            "id": row.get("id"),
            "chunk_index": int(row.get("chunk_index", 0)),
            "score": float(row.get("score", 0.0)),
            "text": row.get("text", ""),
        }
        citations.append(citation)
        context_lines.append(
            f"- [{citation['id']}#{citation['chunk_index']}] {citation['text']}"
        )

    answer = "Based on stored memory:\n" + "\n".join(context_lines[:top_k])
    return {
        "status": "ok",
        "answer": answer,
        "citations": citations[:top_k],
    }


def runtime_health() -> dict[str, Any]:
    runtime = _runtime()
    metadata_health = (
        runtime.metadata_store.health()
        if hasattr(runtime.metadata_store, "health")
        else {}
    )
    vector_health = (
        runtime.vector_store.health() if hasattr(runtime.vector_store, "health") else {}
    )
    return {
        **runtime.health_state,
        "metadata_health": metadata_health,
        "vector_health": vector_health,
    }


def _hash_to_vector(text: str, dimensions: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = list(digest)
    while len(values) < dimensions:
        digest = hashlib.sha256(digest).digest()
        values.extend(list(digest))
    return [(values[index] / 255.0) for index in range(dimensions)]


def _validate_metadata_schema(
    *, metadata_store: Any, supported_versions: Sequence[int]
) -> None:
    version = int(getattr(metadata_store, "schema_version", 0))
    if version not in supported_versions:
        supported = ",".join(str(item) for item in supported_versions)
        raise SchemaVersionError(
            f"Incompatible metadata schema version: {version}. Supported versions: [{supported}]"
        )


def _validate_vector_dimension(*, embedding_dimension: int, vector_store: Any) -> None:
    expected = int(getattr(vector_store, "expected_dimensionality", 0))
    if expected != embedding_dimension:
        raise VectorDimensionError(
            f"Vector store dimensionality mismatch at startup: expected {embedding_dimension}, got {expected}"
        )
