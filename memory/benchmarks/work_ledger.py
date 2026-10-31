from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any


@dataclass
class WorkLedger:
    """Request-scoped counters shared by deterministic benchmark reports."""

    storage_calls: int = 0
    embedding_calls: int = 0
    embedding_texts: int = 0
    candidate_count: int = 0
    filtered_candidates: int = 0
    reranker_calls: int = 0
    audit_writes: int = 0
    selected_chunks: int = 0
    context_tokens: int = 0

    def reset(self) -> None:
        for field in fields(self):
            setattr(self, field.name, 0)

    def snapshot(self, *, requests: int = 1) -> dict[str, Any]:
        if requests < 1:
            raise ValueError("requests must be positive")
        totals = {field.name: int(getattr(self, field.name)) for field in fields(self)}
        return {
            "requests": requests,
            "totals": totals,
            "per_request": {
                name: value / requests for name, value in totals.items()
            },
        }
