from __future__ import annotations

import re

_URI_CREDENTIALS = re.compile(r"(postgres(?:ql)?://[^:\s/]+:)([^@\s/]+)(@)", re.IGNORECASE)
_KV_SECRETS = re.compile(
    r"(?i)\b(password|pwd|token|api_key|apikey|secret)\s*=\s*([^\s;]+)"
)


def redact_secrets(text: str) -> str:
    redacted = _URI_CREDENTIALS.sub(r"\1***\3", text)
    redacted = _KV_SECRETS.sub(r"\1=***", redacted)
    return redacted

