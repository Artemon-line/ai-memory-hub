from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

from memory.backend.log_safety import install_secret_redaction_filter, redact_secrets
from memory.config import LoggingConfig

_CONTEXT_FIELDS = (
    "request_id",
    "trace_id",
    "span_id",
    "operation",
    "provider",
    "mcp_tool_call_id",
)
_RESERVED_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "taskName",
    "thread",
    "threadName",
}


class ObservabilityContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        trace_id, span_id = _active_trace_context()
        for field in _CONTEXT_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, "-")
        if getattr(record, "trace_id", "-") == "-" and trace_id is not None:
            record.trace_id = trace_id
        if getattr(record, "span_id", "-") == "-" and span_id is not None:
            record.span_id = span_id
        return True


class TextLogFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            fmt=(
                "%(asctime)s %(levelname)s %(name)s "
                "request_id=%(request_id)s trace_id=%(trace_id)s span_id=%(span_id)s "
                "operation=%(operation)s provider=%(provider)s "
                "mcp_tool_call_id=%(mcp_tool_call_id)s %(message)s"
            ),
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
        }
        for field in _CONTEXT_FIELDS:
            payload[field] = _safe_log_value(getattr(record, field, "-"))
        payload.update(_extra_record_attrs(record))
        if record.exc_info:
            payload["exception"] = redact_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def configure_logging(
    config: LoggingConfig,
    *,
    stream: TextIO | None = None,
    force: bool = False,
) -> None:
    install_secret_redaction_filter()
    _install_context_filter()
    root = logging.getLogger()
    root.setLevel(_log_level(config.level))
    if config.enabled and (force or not root.handlers):
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(_formatter(config.format))
        handler.addFilter(ObservabilityContextFilter())
        root.handlers = [handler]
    for handler in root.handlers:
        if not any(isinstance(item, ObservabilityContextFilter) for item in handler.filters):
            handler.addFilter(ObservabilityContextFilter())
    logging.getLogger("uvicorn.access").disabled = not config.access_logs
    logging.getLogger("uvicorn.access").setLevel(_log_level(config.level))
    logging.getLogger("uvicorn.error").setLevel(_log_level(config.level))


def _formatter(format_name: str) -> logging.Formatter:
    if format_name == "json":
        return JsonLogFormatter()
    return TextLogFormatter()


def _install_context_filter() -> None:
    for logger_name in ("memory", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        if not any(isinstance(item, ObservabilityContextFilter) for item in logger.filters):
            logger.addFilter(ObservabilityContextFilter())


def _log_level(level: str) -> int:
    return getattr(logging, level.upper(), logging.INFO)


def _extra_record_attrs(record: logging.LogRecord) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _RESERVED_ATTRS or key in _CONTEXT_FIELDS or key.startswith("_"):
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            extra[key] = _safe_log_value(value)
    return extra


def _safe_log_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    return value


def _active_trace_context() -> tuple[str | None, str | None]:
    try:
        from opentelemetry import trace
    except ImportError:
        return None, None
    span = trace.get_current_span()
    context = span.get_span_context()
    if not getattr(context, "is_valid", False):
        return None, None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"
