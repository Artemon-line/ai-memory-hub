from __future__ import annotations

import logging
import re
from collections.abc import Mapping

type LogArgs = tuple[object, ...] | Mapping[str, object] | None

_URI_CREDENTIALS = re.compile(
    r"([a-z][a-z0-9+.-]*://[^:\s/@]*:)([^@\s/]+)(@)", re.IGNORECASE
)
_KV_SECRETS = re.compile(
    r"(?i)(['\"]?\b(?:password|pwd|token|api_key|apikey|api-key|openai_api_key|"
    r"ollama_api_key|dsn|secret)\b['\"]?\s*[:=]\s*)([^\s;,}]+)"
)
_AUTHORIZATION_BEARER = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)"
)
_QUERY_TOKENS = re.compile(r"(?i)([?&](?:access_token|token)=)([^&#\s]+)")
_OPENAI_KEYS = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{10,}\b")
_FILTERED_LOGGERS = ("httpx", "memory", "uvicorn.access", "uvicorn.error")


def redact_secrets(text: str) -> str:
    redacted = _URI_CREDENTIALS.sub(r"\1***\3", text)
    redacted = _KV_SECRETS.sub(r"\1***", redacted)
    redacted = _AUTHORIZATION_BEARER.sub(r"\1***", redacted)
    redacted = _QUERY_TOKENS.sub(r"\1***", redacted)
    redacted = _OPENAI_KEYS.sub("sk-***", redacted)
    return redacted


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access":
            record.args = _redact_log_args(record.args)
            return True
        record.msg = redact_secrets(record.getMessage())
        record.args = ()
        return True


def _redact_log_args(args: LogArgs) -> LogArgs:
    if isinstance(args, tuple):
        return tuple(redact_secrets(item) if isinstance(item, str) else item for item in args)
    if isinstance(args, dict):
        return {
            key: redact_secrets(value) if isinstance(value, str) else value
            for key, value in args.items()
        }
    return args


def install_secret_redaction_filter() -> None:
    for logger_name in _FILTERED_LOGGERS:
        logger = logging.getLogger(logger_name)
        if not any(isinstance(item, SecretRedactionFilter) for item in logger.filters):
            logger.addFilter(SecretRedactionFilter())

