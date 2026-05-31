from __future__ import annotations

from typing import Any


_REDACTED_HASH_KEYS = {
    "chunk_id",
    "conversation_hash",
    "hash",
    "message_hash",
    "message_hashes",
}


def redact_content_hashes(value: Any) -> Any:
    """Remove deterministic content hashes from public response payloads."""
    if isinstance(value, dict):
        return {
            key: redact_content_hashes(item)
            for key, item in value.items()
            if key not in _REDACTED_HASH_KEYS
        }
    if isinstance(value, list):
        return [redact_content_hashes(item) for item in value]
    return value
