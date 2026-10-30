from __future__ import annotations

import pytest

from memory.import_outcomes import (
    ImportOutcome,
    classify_ingestion_result,
    conflict_receipt,
    import_counts,
    ingestion_receipt,
    is_conflict_error,
    is_retryable_conflict,
)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "ok"}, ImportOutcome.INSERTED),
        ({"status": "ok", "deduplicated": True}, ImportOutcome.DEDUPLICATED),
        ({"status": "ok", "appended_messages": 2}, ImportOutcome.APPENDED),
        ({"status": "ok", "stale": True}, ImportOutcome.STALE),
        ({"status": "stale"}, ImportOutcome.STALE),
    ],
)
def test_classify_ingestion_result_covers_success_outcomes(
    result: dict[str, object], expected: ImportOutcome
) -> None:
    assert classify_ingestion_result(result) == expected


def test_receipts_and_counts_expose_only_safe_outcome_metadata() -> None:
    private_text = "private conversation text"
    receipts = [
        ingestion_receipt(0, {"status": "ok", "id": "one", "text": private_text}),
        ingestion_receipt(1, {"status": "ok", "id": "one", "deduplicated": True}),
        ingestion_receipt(2, {"status": "ok", "id": "one", "appended_messages": 3}),
        ingestion_receipt(3, {"status": "ok", "id": "one", "stale": True}),
        conflict_receipt(4, retryable=True),
        {"index": 5, "status": "error", "error_code": "insert_failed"},
    ]

    assert import_counts(receipts) == {
        "processed": 6,
        "inserted": 1,
        "deduplicated": 1,
        "appended": 1,
        "stale": 1,
        "conflicted": 1,
        "failed": 1,
    }
    assert receipts[2]["appended_messages"] == 3
    assert private_text not in str(receipts)


def test_conflict_detection_does_not_copy_provider_details() -> None:
    retryable = ValueError("concurrent_append_conflict: private provider detail")
    terminal = ValueError("duplicate_conflict: private imported text")

    assert is_conflict_error(retryable) is True
    assert is_retryable_conflict(retryable) is True
    assert is_conflict_error(terminal) is True
    assert is_retryable_conflict(terminal) is False
    assert "private" not in str(conflict_receipt(0, retryable=True))
