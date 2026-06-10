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
from memory.backend.vector_store import InMemoryVectorStore, LanceDBVectorStore, PGVectorStore
from memory.config import HubConfig, load_config, parse_config
from memory.ingestion.validate import (
    load_schema,
    set_schema_path,
    validate_conversation,
    validate_schema_compatibility,
)
from memory.ingestion.tokenizer import (
    count_tokens,
    split_token_windows,
    tokenizer_used,
    truncate_to_tokens,
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
    allow_trusted_appends: bool = False
    tokenizer_enabled: bool = False
    tokenizer_encoding: str = "cl100k_base"
    ask_max_context_tokens: int = 2000
    chunking_strategy: str = "message"
    chunking_max_tokens: int = 800
    chunking_overlap_tokens: int = 80
    retrieval_vector_score_threshold: float = 7.5
    retrieval_keyword_enabled: bool = True
    retrieval_keyword_candidate_limit: int = 50
    retrieval_keyword_weight: float = 0.25
    retrieval_metadata_weight: float = 0.15
    retrieval_candidate_multiplier: int = 3


_RUNTIME: RuntimeDependencies | None = None
_SEARCH_CANDIDATE_MULTIPLIER = 3
_CONVERSATION_GROUP_SCORE_WINDOW = 0.25
_MAX_MESSAGES = 10_000
_MAX_MESSAGE_BYTES = 1_000_000
_MAX_PAYLOAD_BYTES = 25_000_000
_MAX_RAW_TRANSCRIPT_BYTES = 5_000_000
_MAX_METADATA_BYTES = 1_000_000
_ROLE_ALIASES = {
    "human": "user",
    "user_message": "user",
    "ai": "assistant",
    "bot": "assistant",
    "assistant_message": "assistant",
}
_TRANSCRIPT_LINE_RE = re.compile(r"^(User|Assistant):\s?(.*)$", re.IGNORECASE)
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
    try:
        validate_schema_compatibility(load_schema())
    except Exception:
        set_schema_path(None)
        raise
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
    requested_vector_provider = (
        "memory" if cfg.providers.vector_db == "in_memory" else cfg.providers.vector_db
    )
    vector_fallback_active = False
    fallback_reasons: list[str] = []
    if requested_vector_provider == "memory":
        vector_store = InMemoryVectorStore(dimension=expected_dimension)
    else:
        try:
            vector_store = _build_vector_store(
                provider=requested_vector_provider,
                cfg=cfg,
                data_dir=data_dir,
                expected_dimension=expected_dimension,
            )
        except Exception as exc:
            if not cfg.storage.vector.allow_fallback:
                raise
            logger.warning(
                "Vector store initialization failed (%s); falling back to in-memory provider",
                redact_secrets(f"{type(exc).__name__}: {exc}"),
            )
            vector_store = InMemoryVectorStore(dimension=expected_dimension)
            vector_fallback_active = True
            fallback_reasons.append(type(exc).__name__)

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
        "vector_provider": "memory" if vector_fallback_active else requested_vector_provider,
        "requested_vector_provider": requested_vector_provider,
        "vector_fallback_active": vector_fallback_active,
        "reasons": fallback_reasons,
    }

    return RuntimeDependencies(
        embedding_provider=embedding_provider,
        metadata_store=metadata_store,
        vector_store=vector_store,
        health_state=health_state,
        allow_trusted_appends=cfg.storage.allow_trusted_appends,
        tokenizer_enabled=cfg.tokenizer.enabled,
        tokenizer_encoding=cfg.tokenizer.encoding,
        ask_max_context_tokens=cfg.ask.max_context_tokens,
        chunking_strategy=cfg.chunking.strategy,
        chunking_max_tokens=cfg.chunking.max_tokens,
        chunking_overlap_tokens=cfg.chunking.overlap_tokens,
        retrieval_vector_score_threshold=cfg.retrieval.vector_score_threshold,
        retrieval_keyword_enabled=cfg.retrieval.keyword_enabled,
        retrieval_keyword_candidate_limit=cfg.retrieval.keyword_candidate_limit,
        retrieval_keyword_weight=cfg.retrieval.keyword_weight,
        retrieval_metadata_weight=cfg.retrieval.metadata_weight,
        retrieval_candidate_multiplier=cfg.retrieval.candidate_multiplier,
    )


def _build_vector_store(
    *,
    provider: str,
    cfg: HubConfig,
    data_dir: Path,
    expected_dimension: int,
) -> Any:
    if provider == "lancedb":
        return LanceDBVectorStore(
            data_dir / "lancedb", dimension=expected_dimension
        )
    if provider == "pgvector":
        dsn = cfg.providers.metadata_dsn
        return PGVectorStore(
            dsn,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    raise ValueError(f"Unsupported vector provider: {provider}")


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
    return chunk_selected_messages(obj, obj.get("messages", []), start_index=0)


def chunk_selected_messages(
    obj: dict[str, Any], messages: list[dict[str, Any]], *, start_index: int
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    if not isinstance(messages, list):
        raise ValueError("Missing or invalid 'messages' key in conversation object")
    runtime = _runtime()
    conversation_id = str(obj["id"])
    for offset, message in enumerate(messages):
        message_hash = str(message["hash"])
        role = str(message["role"])
        text = str(message["text"])
        token_windows = _message_token_windows(
            text,
            strategy=runtime.chunking_strategy,
            max_tokens=runtime.chunking_max_tokens,
            overlap_tokens=runtime.chunking_overlap_tokens,
            encoding=runtime.tokenizer_encoding,
        )
        for token_window_index, chunk_text in enumerate(token_windows):
            chunk_index = start_index + len(chunks)
            chunks.append(
                {
                    "chunk_id": f"{conversation_id}:{chunk_index}:{message_hash}",
                    "chunk_index": chunk_index,
                    "conversation_id": conversation_id,
                    "message_hash": message_hash,
                    "message_index": start_index + offset,
                    "token_window_index": token_window_index,
                    "role": role,
                    "text": chunk_text,
                    "index_state": "pending_index",
                }
            )
    return chunks


def _message_token_windows(
    text: str,
    *,
    strategy: str,
    max_tokens: int,
    overlap_tokens: int,
    encoding: str,
) -> list[str]:
    if strategy == "message":
        return [text]
    if overlap_tokens >= max_tokens:
        raise ValueError("chunking.overlap_tokens must be less than chunking.max_tokens")
    return split_token_windows(
        text,
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
        encoding=encoding,
    )


def _attach_index_chunks(obj: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        obj["metadata"] = metadata
    metadata["index_chunks"] = [
        {
            "chunk_id": str(chunk["chunk_id"]),
            "chunk_index": int(chunk["chunk_index"]),
            "message_hash": str(chunk["message_hash"]),
            "role": str(chunk["role"]),
            "text": str(chunk["text"]),
            "index_state": str(chunk.get("index_state", "pending_index")),
        }
        for chunk in chunks
    ]


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
                "chunk_id": chunk.get("chunk_id"),
                "chunk_index": chunk["chunk_index"],
                "conversation_id": chunk.get("conversation_id"),
                "message_hash": chunk.get("message_hash"),
                "index_state": "indexed",
                "role": chunk["role"],
                "text": chunk["text"],
                "vector": [float(v) for v in vector],
            }
        )
    return embeddings


def store_metadata(obj: dict[str, Any]) -> str:
    runtime = _runtime()
    return runtime.metadata_store.insert(obj)


def store_vectors(metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False) -> None:
    runtime = _runtime()
    runtime.vector_store.insert(metadata_id, embeddings, replace=replace)


def _lookup_by_conversation_hash(conversation_hash: str) -> dict[str, Any] | None:
    store = _runtime().metadata_store
    if hasattr(store, "get_by_conversation_hash"):
        return store.get_by_conversation_hash(conversation_hash)
    for attr in ("by_id", "rows"):
        rows = getattr(store, attr, None)
        if isinstance(rows, dict):
            for conversation in rows.values():
                if _conversation_hash(conversation) == conversation_hash:
                    return conversation
    return None


def _lookup_same_thread(incoming: dict[str, Any]) -> dict[str, Any] | None:
    if not _runtime().allow_trusted_appends:
        return None
    store = _runtime().metadata_store
    memory_id = str(incoming.get("id", ""))
    if memory_id:
        existing = store.get(memory_id) if hasattr(store, "get") else None
        if isinstance(existing, dict):
            return existing

    metadata = incoming.get("metadata", {})
    upstream_thread_id = (
        metadata.get("upstream_thread_id") if isinstance(metadata, dict) else None
    )
    if isinstance(upstream_thread_id, str) and upstream_thread_id:
        source = str(incoming.get("source", ""))
        if hasattr(store, "get_by_upstream_thread"):
            return store.get_by_upstream_thread(source, upstream_thread_id)
        for attr in ("by_id", "rows"):
            rows = getattr(store, attr, None)
            if isinstance(rows, dict):
                for conversation in rows.values():
                    conversation_metadata = conversation.get("metadata", {})
                    if (
                        isinstance(conversation_metadata, dict)
                        and conversation.get("source") == source
                        and conversation_metadata.get("upstream_thread_id")
                        == upstream_thread_id
                    ):
                        return conversation
    return None


def _insert_new_conversation(obj: dict[str, Any]) -> tuple[str, bool]:
    store = _runtime().metadata_store
    if hasattr(store, "insert_new"):
        return store.insert_new(obj)
    duplicate = _lookup_by_conversation_hash(_conversation_hash(obj))
    if duplicate is not None:
        return str(duplicate["id"]), False
    if hasattr(store, "get"):
        existing = store.get(str(obj.get("id", "")))
        if isinstance(existing, dict):
            raise ValueError("unauthorized_update: conversation id already exists")
    return store.insert(obj), True


def _append_conversation(
    existing: dict[str, Any], incoming: dict[str, Any], new_messages: list[dict[str, Any]]
) -> dict[str, Any]:
    updated = dict(existing)
    metadata = dict(updated.get("metadata", {}))
    messages = list(updated.get("messages", [])) + new_messages
    metadata["updated_at"] = incoming["metadata"]["updated_at"]
    metadata["message_hashes"] = [str(message["hash"]) for message in messages]
    metadata["conversation_hash"] = hash_ordered_messages(messages)
    updated["messages"] = messages
    updated["metadata"] = metadata
    store = _runtime().metadata_store
    if hasattr(store, "append_messages"):
        store.append_messages(updated, new_messages)
    else:
        store.insert(updated)
    return updated


def _conversation_hash(conversation: dict[str, Any]) -> str | None:
    metadata = conversation.get("metadata", {})
    if isinstance(metadata, dict):
        value = metadata.get("conversation_hash")
        if isinstance(value, str):
            return value
    return None


def _detect_new_messages(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> list[dict[str, Any]]:
    existing_messages = existing.get("messages", [])
    incoming_messages = incoming.get("messages", [])
    if not isinstance(existing_messages, list) or not isinstance(incoming_messages, list):
        raise ValueError("duplicate_conflict: stored or incoming messages are invalid")
    existing_hashes = [str(message.get("hash", "")) for message in existing_messages]
    incoming_hashes = [str(message.get("hash", "")) for message in incoming_messages]
    common_prefix_len = min(len(existing_hashes), len(incoming_hashes))
    if incoming_hashes[:common_prefix_len] != existing_hashes[:common_prefix_len]:
        raise ValueError("duplicate_conflict: same thread has conflicting message history")
    if len(incoming_hashes) <= len(existing_hashes):
        return []
    seen = set(existing_hashes)
    new_messages: list[dict[str, Any]] = []
    for message in incoming_messages[len(existing_hashes) :]:
        message_hash = str(message["hash"])
        if message_hash not in seen:
            new_messages.append(message)
            seen.add(message_hash)
    return new_messages


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_message(role: str, text: str) -> str:
    digest = hashlib.sha256(f"{role}\n{text}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def hash_ordered_messages(messages: list[dict[str, Any]]) -> str:
    joined = "\n".join(str(message["hash"]) for message in messages)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def normalize_conversation_json(
    payload: Any, *, source: str | None = None, strict_transcript: bool = False
) -> dict[str, Any]:
    """Normalize a conversation JSON object with default values."""
    now = _utc_now_iso()
    payload = _coerce_payload(payload, strict_transcript=strict_transcript)
    _enforce_payload_limits(payload)
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
        normalized["messages"] = _normalize_messages(messages)

    normalized.setdefault("id", str(uuid4()))
    normalized.setdefault("source", source or "unknown")
    normalized["source"] = _normalize_source(normalized.get("source"))
    normalized.setdefault("timestamp", now)
    normalized["timestamp"] = _validate_datetime_string(
        normalized["timestamp"], field_name="timestamp"
    )
    if "imported_at" not in metadata and isinstance(metadata.get("saved_at"), str):
        metadata["imported_at"] = metadata["saved_at"]
    metadata.setdefault("imported_at", now)
    metadata["imported_at"] = _validate_datetime_string(
        metadata["imported_at"], field_name="metadata.imported_at"
    )
    metadata["updated_at"] = now
    if not isinstance(normalized.get("messages"), list):
        raise ValueError("ambiguous input: messages must be an array")
    metadata["message_hashes"] = [
        str(message["hash"]) for message in normalized["messages"]
    ]
    metadata["conversation_hash"] = hash_ordered_messages(normalized["messages"])
    normalized["metadata"] = metadata
    _enforce_payload_limits(normalized)
    return normalized


def _coerce_payload(payload: Any, *, strict_transcript: bool) -> dict[str, Any]:
    if isinstance(payload, dict):
        if any(key in payload for key in ("messages", "conversation", "content", "tags")):
            if "content" in payload and "messages" not in payload and "conversation" not in payload:
                content = payload["content"]
                if isinstance(content, list):
                    normalized = dict(payload)
                    normalized["messages"] = content
                    normalized.pop("content", None)
                    return normalized
            return payload
        raise ValueError("ambiguous input: object must include messages, conversation, content, or tags")
    if isinstance(payload, str):
        if not strict_transcript:
            raise ValueError("raw transcript input requires strict_transcript=True")
        return {"messages": _parse_strict_transcript(payload)}
    raise ValueError("ambiguous input: expected object or strict raw transcript string")


def _parse_strict_transcript(text: str) -> list[dict[str, str]]:
    if len(text.encode("utf-8")) > _MAX_RAW_TRANSCRIPT_BYTES:
        raise ValueError("raw transcript exceeds max_raw_transcript_bytes")
    messages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    lines = text.splitlines()
    for line in lines:
        match = _TRANSCRIPT_LINE_RE.match(line)
        if match:
            role = _normalize_role(match.group(1))
            current = {"role": role, "text": match.group(2)}
            messages.append(current)
            continue
        if current is None:
            if line.strip():
                raise ValueError("ambiguous raw transcript before first speaker boundary")
            continue
        current["text"] += "\n" + line
    if not messages:
        raise ValueError("raw transcript did not contain any speaker boundaries")
    return messages


def _normalize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    normalized_messages: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for index, item in enumerate(messages):
        message = _normalize_message(item, index=index)
        if message["hash"] in seen_hashes:
            continue
        seen_hashes.add(message["hash"])
        normalized_messages.append(message)
    if not normalized_messages:
        raise ValueError("messages must contain at least one non-empty message")
    return normalized_messages


def _normalize_message(message: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ValueError(f"messages[{index}] must be an object")
    normalized = dict(message)
    if "text" not in normalized and "content" in normalized:
        normalized["text"] = normalized["content"]
    normalized.pop("content", None)
    role = _normalize_role(normalized.get("role"))
    text = _normalize_text(normalized.get("text"), index=index)
    computed_hash = sha256_message(role, text)
    supplied_hash = normalized.get("hash")
    if supplied_hash is not None and supplied_hash != computed_hash:
        raise ValueError(f"messages[{index}].hash does not match server-computed hash")
    normalized["role"] = role
    normalized["text"] = text
    normalized["hash"] = computed_hash
    return normalized


def _normalize_role(role: Any) -> str:
    value = str(role).strip().lower()
    value = _ROLE_ALIASES.get(value, value)
    if value not in {"user", "assistant"}:
        raise ValueError(f"unknown message role: {role}")
    return value


def _normalize_source(source: Any) -> str:
    if not isinstance(source, str):
        raise ValueError("source must be a string")
    value = source.strip()
    if not value:
        raise ValueError("source must be non-empty")
    if len(value) > 128:
        raise ValueError("source exceeds max length 128")
    return value


def _validate_datetime_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 datetime string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    return value


def _normalize_text(text: Any, *, index: int) -> str:
    if not isinstance(text, str):
        raise ValueError(f"messages[{index}].text must be a string")
    value = text.strip("\ufeff\r\n")
    if not value.strip():
        raise ValueError(f"messages[{index}].text must be non-empty")
    if len(value.encode("utf-8")) > _MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds max_message_bytes")
    return value


def _enforce_payload_limits(payload: dict[str, Any]) -> None:
    payload_bytes = len(json_dumps(payload).encode("utf-8"))
    if payload_bytes > _MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds max_payload_bytes")
    messages = payload.get("messages")
    if isinstance(messages, list) and len(messages) > _MAX_MESSAGES:
        raise ValueError("payload exceeds max_messages")
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata_bytes = len(json_dumps(metadata).encode("utf-8"))
        if metadata_bytes > _MAX_METADATA_BYTES:
            raise ValueError("metadata exceeds max_metadata_bytes")


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def ingest_messages(
    conversation_json: Any, *, strict_transcript: bool = False
) -> dict[str, Any]:
    # 0. Normalize
    conversation_json = normalize_conversation_json(
        conversation_json, strict_transcript=strict_transcript
    )

    # 1. Validate JSON against schema
    validate_json(conversation_json)

    # 2. Enrich metadata topics from message text
    enrich_topics(conversation_json)

    conversation_hash = conversation_json["metadata"]["conversation_hash"]
    duplicate = _lookup_by_conversation_hash(conversation_hash)
    duplicate_metadata_id = None
    if duplicate is not None:
        duplicate_metadata_id = str(duplicate["id"])
        # We proceed to re-index even if it's a duplicate by hash,
        # to ensure the vector store is in sync with the metadata.
        conversation_json["id"] = duplicate_metadata_id

    existing_by_id = None
    store = _runtime().metadata_store
    if hasattr(store, "get"):
        existing_by_id = store.get(str(conversation_json.get("id", "")))
    if existing_by_id is not None and not _runtime().allow_trusted_appends:
        # If it's a duplicate by ID but not by hash, it's an unauthorized update.
        # But if it's the SAME hash (we already checked), it's fine.
        if _conversation_hash(existing_by_id) != conversation_hash:
            raise ValueError("unauthorized_update: conversation id already exists")

    same_thread = _lookup_same_thread(conversation_json)
    if same_thread is not None:
        new_messages = _detect_new_messages(same_thread, conversation_json)
        if not new_messages:
            # Re-index check for same_thread
            # If it's already fully indexed, we COULD skip, 
            # but for consistency we fall through to ensure vectors are there.
            start_index = 0
            updated = same_thread
        else:
            start_index = len(same_thread.get("messages", []))
            updated = _append_conversation(same_thread, conversation_json, new_messages)
        
        # If we are here, we either have new messages or we are re-indexing existing
        indexing_messages = new_messages if new_messages else updated.get("messages", [])
        chunk_start_index = (
            _next_chunk_index(updated, fallback_start_index=start_index)
            if new_messages
            else 0
        )
        chunks = chunk_selected_messages(
            updated, indexing_messages, start_index=chunk_start_index
        )
        if new_messages:
            _extend_index_chunks(updated, chunks)
        else:
            _attach_index_chunks(updated, chunks)
        if new_messages or chunks:
            store.insert(updated)
        try:
            embeddings = embed_chunks(chunks)
            store_vectors(str(updated["id"]), embeddings, replace=(start_index == 0))
            _mark_chunks_indexed(str(updated["id"]), chunks)
        except Exception:
            _mark_chunks_indexing_failed(str(updated["id"]), chunks)
            raise
        return {
            "status": "ok",
            "id": str(updated["id"]),
            "deduplicated": not bool(new_messages),
            "appended_messages": len(new_messages),
            "embedded_chunks": len(chunks),
            "chunks": len(chunks),
        }

    # 3. Chunk messages
    chunks = chunk_messages(conversation_json)
    _attach_index_chunks(conversation_json, chunks)

    # 4. Store metadata with pending chunks before embedding. Exact duplicates
    # already have metadata; retries should re-index without attempting a second
    # primary-key insert.
    if duplicate_metadata_id is None:
        metadata_id, inserted = _insert_new_conversation(conversation_json)
    else:
        metadata_id, inserted = duplicate_metadata_id, False

    # 5. Embed and write vectors
    try:
        embeddings = embed_chunks(chunks)
        store_vectors(metadata_id, embeddings, replace=not inserted)
        _mark_chunks_indexed(metadata_id, chunks)
    except Exception:
        _mark_chunks_indexing_failed(metadata_id, chunks)
        raise

    # 6. Return stored object
    return {
        "status": "ok",
        "id": metadata_id,
        "deduplicated": not inserted,
        "appended_messages": 0,
        "embedded_chunks": len(chunks),
        "chunks": len(chunks),
    }


def _is_fully_indexed(metadata_id: str) -> bool:
    runtime = _runtime()
    # Always re-index for in-memory store to ensure consistency after restart
    if runtime.health_state.get("vector_provider") == "memory":
        return False

    store = runtime.metadata_store
    if hasattr(store, "is_fully_indexed"):
        return store.is_fully_indexed(metadata_id)
    # Default to False to ensure indexing if we can't check
    return False


def _mark_chunks_indexed(metadata_id: str, chunks: list[dict[str, Any]]) -> None:
    store = _runtime().metadata_store
    if hasattr(store, "mark_chunks_indexed"):
        store.mark_chunks_indexed(metadata_id, [str(chunk["chunk_id"]) for chunk in chunks])


def _mark_chunks_indexing_failed(metadata_id: str, chunks: list[dict[str, Any]]) -> None:
    store = _runtime().metadata_store
    if hasattr(store, "mark_chunks_indexing_failed"):
        store.mark_chunks_indexing_failed(
            metadata_id, [str(chunk["chunk_id"]) for chunk in chunks]
        )


def search(query: str, top_k: int = 5) -> dict[str, Any]:
    runtime = _runtime()
    query_vector = runtime.embedding_provider.embed_texts([query])[0]
    candidate_multiplier = max(
        1, int(getattr(runtime, "retrieval_candidate_multiplier", _SEARCH_CANDIDATE_MULTIPLIER))
    )
    candidate_k = max(top_k, min(top_k * candidate_multiplier, 100))
    matches = runtime.vector_store.search(query_vector, top_k=candidate_k)

    ids = [str(match["memory_id"]) for match in matches]
    keyword_conversations = _keyword_conversations(
        runtime.metadata_store,
        query,
        enabled=runtime.retrieval_keyword_enabled,
        limit=runtime.retrieval_keyword_candidate_limit,
    )
    ids.extend(str(conversation["id"]) for conversation in keyword_conversations if "id" in conversation)
    ids = _unique_strings(ids)
    conversations = runtime.metadata_store.get_many(ids)
    for conversation in keyword_conversations:
        memory_id = str(conversation.get("id", ""))
        if memory_id and memory_id not in conversations:
            conversations[memory_id] = conversation

    results: list[dict[str, Any]] = []
    for match in matches:
        memory_id = str(match["memory_id"])
        score = float(match["score"])
        conversation = conversations.get(memory_id)
        row = {
            "id": memory_id,
            "score": score,
            "chunk_index": int(match["chunk_index"]),
            "role": match["role"],
            "text": match["text"],
            "conversation": conversation,
        }
        if _passes_retrieval_threshold(
            query=query,
            row=row,
            threshold=runtime.retrieval_vector_score_threshold,
        ):
            results.append(row)

    existing_keys = {
        (str(row["id"]), int(row["chunk_index"]), str(row["text"])) for row in results
    }
    for conversation in keyword_conversations:
        memory_id = str(conversation.get("id", ""))
        if not memory_id:
            continue
        row = _keyword_result_row(
            conversation,
            score=runtime.retrieval_vector_score_threshold,
        )
        key = (str(row["id"]), int(row["chunk_index"]), str(row["text"]))
        if key not in existing_keys:
            results.append(row)
            existing_keys.add(key)

    ranked = _rank_retrieval_results(
        query,
        results,
        keyword_weight=runtime.retrieval_keyword_weight,
        metadata_weight=runtime.retrieval_metadata_weight,
    )
    return {"status": "ok", "results": group_conversation_results(ranked)[:top_k]}


def group_conversation_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    group_scores: dict[str, float] = {}
    group_counts: dict[str, int] = {}
    for row in rows:
        memory_id = str(row.get("id", ""))
        if not memory_id:
            continue
        score = float(row.get("_ranking_score", row.get("score", 0.0)))
        group_scores[memory_id] = min(score, group_scores.get(memory_id, score))
        group_counts[memory_id] = group_counts.get(memory_id, 0) + 1

    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        memory_id = str(row.get("id", ""))
        row_score = float(row.get("_ranking_score", row.get("score", 0.0)))
        conversation_score = group_scores.get(memory_id, row_score)
        grouped_score = row_score
        if row_score - conversation_score <= _CONVERSATION_GROUP_SCORE_WINDOW:
            grouped_score = conversation_score
        enriched_row = dict(row)
        enriched_row["conversation_score"] = conversation_score
        enriched_row["conversation_match_count"] = group_counts.get(memory_id, 1)
        enriched_row["_original_rank"] = index
        enriched_row["_grouped_score"] = grouped_score
        enriched.append(enriched_row)

    grouped = sorted(
        enriched,
        key=lambda row: (
            float(row["_grouped_score"]),
            -float(row.get("_ranking_boost", 0.0)),
            str(row.get("id", "")),
            float(row.get("score", 0.0)),
            int(row.get("chunk_index", 0)),
            int(row["_original_rank"]),
        ),
    )
    for row in grouped:
        row.pop("_grouped_score", None)
        row.pop("_original_rank", None)
        row.pop("_ranking_score", None)
        row.pop("_ranking_boost", None)
    return grouped


def _keyword_conversations(
    metadata_store: Any, query: str, *, enabled: bool, limit: int
) -> list[dict[str, Any]]:
    if not enabled:
        return []
    if hasattr(metadata_store, "search_text"):
        return [
            item for item in metadata_store.search_text(query, limit=limit) if isinstance(item, dict)
        ]
    rows = getattr(metadata_store, "by_id", None) or getattr(metadata_store, "rows", None)
    if not isinstance(rows, dict):
        return []
    tokens = _query_tokens(query)
    if not tokens:
        return []
    matches: list[dict[str, Any]] = []
    for conversation in rows.values():
        if not isinstance(conversation, dict):
            continue
        text = _conversation_search_text(conversation).lower()
        if all(token in text for token in tokens):
            matches.append(conversation)
    return matches[:limit]


def _passes_retrieval_threshold(*, query: str, row: dict[str, Any], threshold: float) -> bool:
    score = float(row.get("score", 0.0))
    if score <= threshold:
        return True
    return _keyword_overlap(query, row) > 0 or _metadata_overlap(query, row.get("conversation")) > 0


def _rank_retrieval_results(
    query: str,
    rows: list[dict[str, Any]],
    *,
    keyword_weight: float,
    metadata_weight: float,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        keyword_overlap = _keyword_overlap(query, row)
        metadata_overlap = _metadata_overlap(query, row.get("conversation"))
        ranking_score = float(row.get("score", 0.0))
        ranking_boost = (
            min(keyword_overlap, 3) * keyword_weight
            + min(metadata_overlap, 3) * metadata_weight
        )
        ranking_score -= ranking_boost
        enriched = dict(row)
        enriched["_ranking_score"] = max(0.0, ranking_score)
        enriched["_ranking_boost"] = ranking_boost
        enriched["_original_rank"] = index
        ranked.append(enriched)
    return sorted(
        ranked,
        key=lambda row: (
            float(row["_ranking_score"]),
            str(row.get("id", "")),
            int(row.get("chunk_index", 0)),
            int(row["_original_rank"]),
        ),
    )


def _keyword_result_row(conversation: dict[str, Any], *, score: float) -> dict[str, Any]:
    messages = conversation.get("messages", [])
    first_message = messages[0] if isinstance(messages, list) and messages else {}
    if not isinstance(first_message, dict):
        first_message = {}
    return {
        "id": str(conversation["id"]),
        "score": float(score),
        "chunk_index": 0,
        "role": str(first_message.get("role", "")),
        "text": str(first_message.get("text", "")),
        "conversation": conversation,
    }


def _keyword_overlap(query: str, row: dict[str, Any]) -> int:
    tokens = set(_query_tokens(query))
    if not tokens:
        return 0
    text = str(row.get("text", "")).lower()
    conversation = row.get("conversation")
    if isinstance(conversation, dict):
        text += " " + _conversation_search_text(conversation).lower()
    return sum(1 for token in tokens if token in text)


def _metadata_overlap(query: str, conversation: Any) -> int:
    if not isinstance(conversation, dict):
        return 0
    tokens = set(_query_tokens(query))
    if not tokens:
        return 0
    metadata = conversation.get("metadata", {})
    metadata_values: list[str] = [str(conversation.get("source", "")), str(conversation.get("title", ""))]
    if isinstance(metadata, dict):
        for key in ("tags", "topics"):
            value = metadata.get(key)
            if isinstance(value, list):
                metadata_values.extend(str(item) for item in value)
            elif value is not None:
                metadata_values.append(str(value))
    text = " ".join(metadata_values).lower()
    return sum(1 for token in tokens if token in text)


def _conversation_search_text(conversation: dict[str, Any]) -> str:
    parts = [
        str(conversation.get("id", "")),
        str(conversation.get("source", "")),
        str(conversation.get("title", "")),
    ]
    messages = conversation.get("messages", [])
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                parts.append(str(message.get("role", "")))
                parts.append(str(message.get("text", "")))
    metadata = conversation.get("metadata", {})
    if isinstance(metadata, dict):
        for key in ("tags", "topics"):
            value = metadata.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
            elif value is not None:
                parts.append(str(value))
    return " ".join(parts)


def _query_tokens(query: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", query)
        if len(token) >= 2
    ][:8]


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def retrieve(memory_id: str) -> dict[str, Any] | None:
    runtime = _runtime()
    return runtime.metadata_store.get(memory_id)


def ask(
    question: str, top_k: int = 5, max_context_tokens: int | None = None
) -> dict[str, Any]:
    search_result = search(query=question, top_k=top_k)
    matches = search_result.get("results", [])
    if not matches:
        return {
            "status": "ok",
            "results": [],
            "answer": "I could not find relevant memory for that question.",
            "citations": [],
        }

    runtime = _runtime()
    budget_enabled = runtime.tokenizer_enabled or max_context_tokens is not None
    if not budget_enabled:
        return _ask_from_matches(matches, top_k=top_k)

    token_budget = (
        max_context_tokens
        if max_context_tokens is not None
        else runtime.ask_max_context_tokens
    )
    selected_matches, citations, context_lines, tokens_used, chunks_dropped = (
        _select_ask_context(
            matches=matches[:top_k],
            max_context_tokens=token_budget,
            encoding=runtime.tokenizer_encoding,
        )
    )
    answer = (
        "Based on stored memory:\n" + "\n".join(context_lines)
        if context_lines
        else "I could not fit relevant memory within the context budget."
    )
    return {
        "status": "ok",
        "results": selected_matches,
        "answer": answer,
        "citations": citations,
        "context_tokens_used": tokens_used,
        "chunks_selected": len(selected_matches),
        "chunks_dropped": chunks_dropped,
        "tokenizer_used": tokenizer_used(runtime.tokenizer_encoding),
    }


def _next_chunk_index(
    conversation: dict[str, Any], *, fallback_start_index: int
) -> int:
    metadata = conversation.get("metadata", {})
    index_chunks = metadata.get("index_chunks") if isinstance(metadata, dict) else None
    if isinstance(index_chunks, list):
        indexes = [
            int(chunk.get("chunk_index", -1))
            for chunk in index_chunks
            if isinstance(chunk, dict)
        ]
        if indexes:
            return max(indexes) + 1
    return fallback_start_index


def _extend_index_chunks(obj: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        obj["metadata"] = metadata
    existing = metadata.get("index_chunks")
    if not isinstance(existing, list):
        existing = []
    metadata["index_chunks"] = existing + [
        {
            "chunk_id": str(chunk["chunk_id"]),
            "chunk_index": int(chunk["chunk_index"]),
            "message_hash": str(chunk["message_hash"]),
            "role": str(chunk["role"]),
            "text": str(chunk["text"]),
            "index_state": str(chunk.get("index_state", "pending_index")),
        }
        for chunk in chunks
    ]


def _ask_from_matches(matches: list[dict[str, Any]], *, top_k: int) -> dict[str, Any]:
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
        "results": matches[:top_k],
        "answer": answer,
        "citations": citations[:top_k],
    }


def _select_ask_context(
    *,
    matches: list[dict[str, Any]],
    max_context_tokens: int,
    encoding: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], int, int]:
    selected_matches: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    context_lines: list[str] = []
    tokens_used = 0
    chunks_dropped = 0

    for row in matches:
        citation_id = row.get("id")
        chunk_index = int(row.get("chunk_index", 0))
        prefix = f"- [{citation_id}#{chunk_index}] "
        prefix_tokens = count_tokens(prefix, encoding)
        remaining = max_context_tokens - tokens_used - prefix_tokens
        if remaining <= 0:
            chunks_dropped += 1
            continue

        text = str(row.get("text", ""))
        text_tokens = count_tokens(text, encoding)
        selected_text = text
        if text_tokens > remaining:
            selected_text = truncate_to_tokens(text, remaining, encoding)
            text_tokens = count_tokens(selected_text, encoding)
        if not selected_text:
            chunks_dropped += 1
            continue

        line = prefix + selected_text
        line_tokens = prefix_tokens + text_tokens
        selected_row = dict(row)
        selected_row["text"] = selected_text
        citation = {
            "id": citation_id,
            "chunk_index": chunk_index,
            "score": float(row.get("score", 0.0)),
            "text": selected_text,
        }
        selected_matches.append(selected_row)
        citations.append(citation)
        context_lines.append(line)
        tokens_used += line_tokens

    return selected_matches, citations, context_lines, tokens_used, chunks_dropped


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
