from __future__ import annotations

from typing import Any


def save_receipt(data: dict[str, Any]) -> dict[str, Any]:
    """Build the compact receipt shared by the canonical save adapters."""
    appended_messages = int(data.get("appended_messages", 0))
    deduplicated = bool(data.get("deduplicated", False))
    review_state = str(data.get("memory_status") or "active")
    if review_state != "active":
        outcome = review_state
    elif data.get("stale"):
        outcome = "stale"
    elif deduplicated:
        outcome = "deduplicated"
    elif appended_messages:
        outcome = "appended"
    else:
        outcome = "created"
    return {
        "status": str(data.get("status", "ok")),
        "id": str(data["id"]),
        "outcome": outcome,
        "deduplicated": deduplicated,
        "appended_messages": appended_messages,
        "review_state": review_state,
    }
