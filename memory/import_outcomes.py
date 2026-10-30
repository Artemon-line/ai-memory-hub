from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable, Mapping


class ImportOutcome(StrEnum):
    INSERTED = "inserted"
    DEDUPLICATED = "deduplicated"
    APPENDED = "appended"
    STALE = "stale"
    CONFLICT = "conflict"
    ERROR = "error"


def classify_ingestion_result(result: Mapping[str, Any]) -> str:
    status = str(result.get("status") or "ok")
    if status != "ok":
        return status
    if bool(result.get("stale", False)):
        return ImportOutcome.STALE
    if _nonnegative_int(result.get("appended_messages")) > 0:
        return ImportOutcome.APPENDED
    if bool(result.get("deduplicated", False)):
        return ImportOutcome.DEDUPLICATED
    return ImportOutcome.INSERTED


def ingestion_receipt(index: int, result: Mapping[str, Any]) -> dict[str, Any]:
    outcome = classify_ingestion_result(result)
    receipt: dict[str, Any] = {
        "index": index,
        "id": result.get("id"),
        "status": outcome,
        "deduplicated": outcome == ImportOutcome.DEDUPLICATED,
    }
    appended_messages = _nonnegative_int(result.get("appended_messages"))
    if outcome == ImportOutcome.APPENDED:
        receipt["appended_messages"] = appended_messages
    return receipt


def conflict_receipt(index: int, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "index": index,
        "status": ImportOutcome.CONFLICT,
        "error_code": (
            "concurrent_append_conflict" if retryable else "duplicate_conflict"
        ),
        "retryable": retryable,
    }


def import_counts(receipts: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = {
        "processed": 0,
        "inserted": 0,
        "deduplicated": 0,
        "appended": 0,
        "stale": 0,
        "conflicted": 0,
        "failed": 0,
    }
    for receipt in receipts:
        counts["processed"] += 1
        status = str(receipt.get("status") or "")
        if status == ImportOutcome.INSERTED:
            counts["inserted"] += 1
        elif status == ImportOutcome.DEDUPLICATED:
            counts["deduplicated"] += 1
        elif status == ImportOutcome.APPENDED:
            counts["appended"] += 1
        elif status == ImportOutcome.STALE:
            counts["stale"] += 1
        elif status == ImportOutcome.CONFLICT:
            counts["conflicted"] += 1
        elif status == ImportOutcome.ERROR:
            counts["failed"] += 1
    return counts


def is_conflict_error(exc: Exception) -> bool:
    message = str(exc)
    return message.startswith(("duplicate_conflict", "concurrent_append_conflict"))


def is_retryable_conflict(exc: Exception) -> bool:
    return str(exc).startswith("concurrent_append_conflict")


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
