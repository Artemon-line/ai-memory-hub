from __future__ import annotations

import pytest

from memory.benchmarks.work_ledger import WorkLedger


def test_work_ledger_has_stable_totals_and_per_request_schema() -> None:
    ledger = WorkLedger(storage_calls=4, candidate_count=10, selected_chunks=6)

    snapshot = ledger.snapshot(requests=2)

    assert snapshot["totals"] == {
        "storage_calls": 4,
        "embedding_calls": 0,
        "embedding_texts": 0,
        "candidate_count": 10,
        "filtered_candidates": 0,
        "reranker_calls": 0,
        "audit_writes": 0,
        "selected_chunks": 6,
        "context_tokens": 0,
    }
    assert snapshot["per_request"]["storage_calls"] == 2.0
    assert snapshot["per_request"]["candidate_count"] == 5.0


def test_work_ledger_reset_and_request_validation() -> None:
    ledger = WorkLedger(audit_writes=2)

    ledger.reset()

    assert all(value == 0 for value in ledger.snapshot()["totals"].values())
    with pytest.raises(ValueError, match="requests must be positive"):
        ledger.snapshot(requests=0)
