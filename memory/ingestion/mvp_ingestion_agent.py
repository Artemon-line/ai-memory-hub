from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

import memory.ingestion.mvp_ingestion as mvp_ingestion
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.validate import load_schema


class MVPIngestionAgent(BaseIngestionAgent):
    """Deterministic, schema-first ingestion agent for Phase 1 MVP."""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        formatter: Callable[[list[dict[str, str]], dict[str, Any]], dict[str, Any] | str] | None = None,
        embedder: Callable[[list[str]], list[Any]] | None = None,
        storage: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]] | None = None,
        schema: dict[str, Any] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        super().__init__(config)

        provider_impl = config.get("provider_impl", {}) if isinstance(config, dict) else {}
        self.schema = schema or load_schema()
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))

        mvp_ingestion.configure_runtime(
            formatter=formatter or provider_impl.get("formatter"),
            embedder=embedder or provider_impl.get("embedder"),
            storage=storage or provider_impl.get("storage"),
        )

    def _now_iso(self) -> str:
        return self._now_provider().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def attach_metadata(
        self,
        obj: Dict[str, Any],
        source: str,
        title: Optional[str],
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        conversation = dict(obj)
        conversation["id"] = conversation.get("id") or str(uuid4())
        conversation["source"] = source
        conversation["timestamp"] = conversation.get("timestamp") or self._now_iso()

        if title is not None:
            conversation["title"] = title

        existing_metadata = conversation.get("metadata")
        metadata_dict = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
        if metadata:
            metadata_dict.update(metadata)
        metadata_dict.setdefault("imported_at", self._now_iso())
        metadata_dict.setdefault("agent", "mvp")

        conversation["metadata"] = metadata_dict
        return conversation

    async def ingest_messages(
        self,
        messages: List[Dict[str, str]],
        *,
        source: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        messages = self.preprocess_messages(messages)
        obj = mvp_ingestion.retry_format(messages, self.schema, max_attempts=3)
        mvp_ingestion.validate_json(obj, self.schema)
        obj = self.attach_metadata(obj, source, title, metadata)
        chunks = mvp_ingestion.chunk_messages(obj)
        embeddings = mvp_ingestion.embed_chunks(chunks)
        stored = mvp_ingestion.store(obj, embeddings)
        return self.postprocess_result(stored)
