from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)


class DryRunMetadataStore:
    def __init__(self, store: Any):
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def insert(self, conversation_json: dict[str, Any]) -> str:
        memory_id = str(conversation_json["id"])
        logger.warning("DRY-RUN: write skipped for %s", memory_id)
        return memory_id


class DryRunVectorStore:
    def __init__(self, store: Any):
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def insert(self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False) -> None:
        # Preserve validation behavior while skipping persistence.
        if hasattr(self._store, "_validate_dimension"):
            for item in embeddings:
                self._store._validate_dimension([float(v) for v in item["vector"]], operation="insert")
        logger.warning(
            "DRY-RUN: vector write skipped for %s (replace=%s, chunks=%d)",
            metadata_id,
            replace,
            len(embeddings),
        )

