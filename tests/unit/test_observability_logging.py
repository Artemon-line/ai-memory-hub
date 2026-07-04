from __future__ import annotations

import io
import json
import logging

from memory.config import LoggingConfig, parse_config
from memory.observability.logging import configure_logging


def test_logging_config_defaults() -> None:
    config = parse_config({})

    assert config.observability.logging.enabled is True
    assert config.observability.logging.format == "text"
    assert config.observability.logging.level == "INFO"
    assert config.observability.logging.access_logs is True
    assert config.observability.debug_payloads is False


def test_logging_config_accepts_json_format_and_debug_level() -> None:
    config = parse_config(
        {
            "observability": {
                "logging": {
                    "format": "json",
                    "level": "debug",
                    "access_logs": False,
                    "request_id_header": "X-Request-ID",
                }
            }
        }
    )

    assert config.observability.logging.format == "json"
    assert config.observability.logging.level == "DEBUG"
    assert config.observability.logging.access_logs is False
    assert config.observability.logging.request_id_header == "x-request-id"


def test_json_logging_includes_context_fields_and_redacts_extras() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        configure_logging(LoggingConfig(format="json"), stream=stream, force=True)
        logger = logging.getLogger("memory.tests.observability")
        logger.info(
            "provider failed password=secret",
            extra={
                "request_id": "req-123",
                "operation": "memory_insert",
                "provider": "pgvector",
                "dsn": "postgresql://user:secret@example.com/db",
            },
        )

        payload = json.loads(stream.getvalue())
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)

    assert payload["message"] == "provider failed password=***"
    assert payload["request_id"] == "req-123"
    assert payload["trace_id"] == "-"
    assert payload["span_id"] == "-"
    assert payload["operation"] == "memory_insert"
    assert payload["provider"] == "pgvector"
    assert payload["dsn"] == "postgresql://user:***@example.com/db"


def test_text_logging_includes_context_fields() -> None:
    stream = io.StringIO()
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    try:
        configure_logging(LoggingConfig(format="text"), stream=stream, force=True)
        logging.getLogger("memory.tests.observability").warning(
            "fallback active",
            extra={"request_id": "req-456", "operation": "startup", "provider": "lancedb"},
        )
        text = stream.getvalue()
    finally:
        root.handlers = original_handlers
        root.setLevel(original_level)

    assert "request_id=req-456" in text
    assert "trace_id=-" in text
    assert "span_id=-" in text
    assert "operation=startup" in text
    assert "provider=lancedb" in text


def test_access_logs_can_be_disabled() -> None:
    configure_logging(LoggingConfig(access_logs=False))

    assert logging.getLogger("uvicorn.access").disabled is True

    configure_logging(LoggingConfig(access_logs=True))
