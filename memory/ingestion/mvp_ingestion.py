from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

import jsonschema  # pyright: ignore[reportMissingModuleSource]

from memory.ingestion.validate import validate_conversation

FormatterFn = Callable[[list[dict[str, str]], dict[str, Any]], dict[str, Any] | str]
EmbedderFn = Callable[[list[str]], list[Any]]
StoreFn = Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]]


@dataclass
class _RuntimeDependencies:
    formatter: FormatterFn | None = None
    embedder: EmbedderFn | None = None
    storage: StoreFn | None = None


_RUNTIME = _RuntimeDependencies()


def configure_runtime(
    *,
    formatter: FormatterFn | None = None,
    embedder: EmbedderFn | None = None,
    storage: StoreFn | None = None,
) -> None:
    """Configure provider callables used by the MVP ingestion pipeline."""
    _RUNTIME.formatter = formatter
    _RUNTIME.embedder = embedder
    _RUNTIME.storage = storage


def _require_dependency(name: str, value: Any) -> Any:
    if value is None:
        raise RuntimeError(f"Missing MVP ingestion dependency: {name}")
    return value


def format_with_llm(messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
    formatter = _require_dependency("formatter", _RUNTIME.formatter)
    raw = formatter(messages, schema)
    if isinstance(raw, str):
        return json.loads(raw)
    if isinstance(raw, dict):
        return raw
    raise TypeError("Formatter must return dict or JSON string")


def validate_json(obj: dict[str, Any], schema: dict[str, Any]) -> None:
    validate_conversation(obj)
    jsonschema.validate(instance=obj, schema=schema)


def retry_format(
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    max_attempts: int = 3,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for _ in range(max_attempts):
        try:
            obj = format_with_llm(messages, schema)
            validate_json(obj, schema)
            return obj
        except Exception as exc:
            last_error = exc
    if last_error is None:
        raise RuntimeError("retry_format failed without attempts")
    raise last_error


def chunk_messages(obj: dict[str, Any]) -> list[dict[str, Any]]:
    conversation_id = str(obj.get("id", ""))
    chunks: list[dict[str, Any]] = []
    for index, message in enumerate(obj["messages"]):
        chunks.append(
            {
                "conversation_id": conversation_id,
                "index": index,
                "role": message["role"],
                "text": message["text"],
            }
        )
    return chunks


def embed_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    embedder = _require_dependency("embedder", _RUNTIME.embedder)
    texts = [str(chunk["text"]) for chunk in chunks]
    vectors = embedder(texts)
    if len(vectors) != len(chunks):
        raise ValueError("Embedder must return one vector per chunk")
    return [
        {
            "chunk": chunk,
            "vector": vector,
        }
        for chunk, vector in zip(chunks, vectors)
    ]


def store(obj: dict[str, Any], embeddings: list[dict[str, Any]]) -> dict[str, Any]:
    storage = _require_dependency("storage", _RUNTIME.storage)
    return storage(obj, embeddings)
