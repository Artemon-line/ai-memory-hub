from __future__ import annotations

from typing import Any

from memory.backend.log_safety import redact_secrets
from memory.config import HubConfig

_MAX_DEBUG_SNIPPET_CHARS = 240


def debug_payload_snippet(config: HubConfig, payload: Any) -> str | None:
    if not config.observability.debug_payloads:
        return None
    text = redact_secrets(str(payload))
    if len(text) <= _MAX_DEBUG_SNIPPET_CHARS:
        return text
    return text[:_MAX_DEBUG_SNIPPET_CHARS] + "..."
