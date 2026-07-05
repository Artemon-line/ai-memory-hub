from __future__ import annotations

import logging

from uvicorn.logging import AccessFormatter

from memory.backend.log_safety import SecretRedactionFilter, redact_secrets


def test_secret_redaction_filter_preserves_uvicorn_access_log_args() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=(
            "127.0.0.1:12345",
            "GET",
            "/mcp?access_token=secret-token",
            "1.1",
            404,
        ),
        exc_info=None,
    )

    assert SecretRedactionFilter().filter(record)

    formatted = AccessFormatter("%(client_addr)s - \"%(request_line)s\" %(status_code)s").format(record)
    assert "secret-token" not in formatted
    assert "access_token=***" in formatted


def test_redact_secrets_covers_provider_keys_and_dsns() -> None:
    message = (
        "OPENAI_API_KEY: sk-proj-secretvalue123456 "
        "ollama_api_key=ollama-secret "
        "dsn: postgresql://user:password@example.com/db "
        "Authorization: Bearer bearer-secret"
    )

    redacted = redact_secrets(message)

    assert "sk-proj-secretvalue123456" not in redacted
    assert "ollama-secret" not in redacted
    assert "password@example.com" not in redacted
    assert "bearer-secret" not in redacted
    assert "OPENAI_API_KEY: ***" in redacted
    assert "ollama_api_key=***" in redacted
    assert "Authorization: Bearer ***" in redacted
