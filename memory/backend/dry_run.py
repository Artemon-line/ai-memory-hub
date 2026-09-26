from __future__ import annotations

import logging
from typing import Any

from memory.backend.handoff_records import prepare_handoff_record

logger = logging.getLogger(__name__)


class DryRunMetadataStore:
    def __init__(self, store: Any):
        self._store = store

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)

    def insert(self, conversation_json: dict[str, Any]) -> str:
        memory_id = str(conversation_json["id"])
        _log_dry_run_skip("metadata_insert", memory_id=memory_id)
        return memory_id

    def insert_new(self, conversation_json: dict[str, Any]) -> tuple[str, bool]:
        memory_id = str(conversation_json["id"])
        _log_dry_run_skip("metadata_insert_new", memory_id=memory_id)
        return memory_id, True

    def insert_facts(self, facts: list[dict[str, Any]]) -> None:
        _log_dry_run_skip("metadata_insert_facts", fact_count=len(facts))

    def supersede_fact(
        self, fact_id: str, superseded_by: str, project_id: str | None = None
    ) -> bool:
        _ = project_id
        _log_dry_run_skip(
            "metadata_supersede_fact", fact_id=fact_id, superseded_by=superseded_by
        )
        return False

    def set_runtime_metadata(self, key: str, value: dict[str, Any]) -> None:
        _ = value
        _log_dry_run_skip("metadata_set_runtime_metadata", metadata_key=key)

    def append_audit_event(self, event: dict[str, Any]) -> dict[str, Any]:
        _log_dry_run_skip(
            "metadata_append_audit_event",
            event_type=str(event.get("event_type") or "unknown"),
        )
        return dict(event)

    def create_handoff(
        self,
        record: dict[str, Any],
        *,
        owner_id: str | None,
        project_id: str,
        supersedes_handoff_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = prepare_handoff_record(
            record,
            owner_id=owner_id,
            project_id=project_id,
            supersedes_handoff_id=supersedes_handoff_id,
        )
        _log_dry_run_skip(
            "metadata_create_handoff", handoff_id=normalized["handoff_id"]
        )
        return normalized

    def supersede_handoff(
        self,
        handoff_id: str,
        replacement: dict[str, Any],
        *,
        owner_id: str | None,
        project_id: str,
    ) -> dict[str, Any] | None:
        _ = (replacement, owner_id, project_id)
        _log_dry_run_skip("metadata_supersede_handoff", handoff_id=handoff_id)
        return None


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
        _log_dry_run_skip(
            "vector_insert",
            memory_id=metadata_id,
            replace=replace,
            chunk_count=len(embeddings),
        )


def _log_dry_run_skip(operation: str, **extra: Any) -> None:
    logger.warning(
        "DRY-RUN: skipped %s write",
        operation,
        extra={
            "event": "dry_run_write_skipped",
            "operation": operation,
            **extra,
        },
    )
