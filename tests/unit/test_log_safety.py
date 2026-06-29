from __future__ import annotations

import logging

from uvicorn.logging import AccessFormatter

from memory.backend.log_safety import SecretRedactionFilter


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
