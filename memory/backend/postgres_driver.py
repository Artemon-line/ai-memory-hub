from __future__ import annotations

import getpass
import os
import posixpath
import ssl
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

_SUPPORTED_SCHEMES = {"postgres", "postgresql"}
_SUPPORTED_SSL_MODES = {
    "allow",
    "disable",
    "prefer",
    "require",
    "verify-ca",
    "verify-full",
}


def connect(dsn: str) -> Any:
    """Open a synchronous DB-API connection from a libpq-style PostgreSQL URL."""
    try:
        from pg8000 import dbapi
    except ImportError as exc:
        raise RuntimeError(
            "pg8000 package is required for PostgreSQL and PGVector providers"
        ) from exc
    return TransactionConnection(dbapi.connect(**connection_kwargs(dsn)))


class TransactionConnection:
    """Provide the commit/rollback semantics expected by storage provider contexts."""

    def __init__(self, connection: Any):
        self._connection = connection

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __enter__(self) -> TransactionConnection:
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.close()
        return False


def connection_kwargs(dsn: str) -> dict[str, Any]:
    """Translate the supported PostgreSQL URL options to pg8000 arguments."""
    parsed = urlsplit(dsn)
    if parsed.scheme.lower() not in _SUPPORTED_SCHEMES:
        raise ValueError("PostgreSQL DSN must use the postgres or postgresql scheme")

    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
    query_host = query.pop("host", [None])[-1]
    host = unquote(parsed.hostname) if parsed.hostname is not None else query_host
    host = host or os.getenv("PGHOST") or "localhost"
    port_text = query.pop(
        "port", [str(parsed.port or os.getenv("PGPORT") or 5432)]
    )[-1]
    user = unquote(parsed.username) if parsed.username is not None else _default_user()
    password = (
        unquote(parsed.password)
        if parsed.password is not None
        else os.getenv("PGPASSWORD")
    )
    database = (
        unquote(parsed.path.lstrip("/")) or os.getenv("PGDATABASE") or user
    )

    kwargs: dict[str, Any] = {
        "user": user,
        "port": int(port_text),
        "database": database,
    }
    if host.startswith("/"):
        kwargs["host"] = None
        kwargs["unix_sock"] = _unix_socket_path(host, int(port_text))
    else:
        kwargs["host"] = host
    if password is not None:
        kwargs["password"] = password

    timeout = query.pop("connect_timeout", [None])[-1]
    if timeout not in (None, ""):
        kwargs["timeout"] = float(timeout)
    application_name = query.pop("application_name", [None])[-1]
    if application_name not in (None, ""):
        kwargs["application_name"] = application_name

    sslmode = query.pop("sslmode", ["prefer"])[-1].lower()
    kwargs["ssl_context"] = _ssl_context(sslmode, query, host=host)

    startup_params = {
        key: values[-1]
        for key, values in query.items()
        if values and values[-1] != ""
    }
    if startup_params:
        kwargs["startup_params"] = startup_params
    return kwargs


def _default_user() -> str:
    return os.getenv("PGUSER") or getpass.getuser()


def _unix_socket_path(host: str, port: int) -> str:
    if host.endswith(f".s.PGSQL.{port}"):
        return host
    return posixpath.join(host, f".s.PGSQL.{port}")


def _ssl_context(
    sslmode: str, query: dict[str, list[str]], *, host: str
) -> ssl.SSLContext | bool | None:
    if sslmode not in _SUPPORTED_SSL_MODES:
        supported = ", ".join(sorted(_SUPPORTED_SSL_MODES))
        raise ValueError(
            f"Unsupported PostgreSQL sslmode {sslmode!r}; expected one of: {supported}"
        )
    root_cert = query.pop("sslrootcert", [None])[-1]
    cert_file = query.pop("sslcert", [None])[-1]
    key_file = query.pop("sslkey", [None])[-1]
    if sslmode == "disable":
        return False
    if sslmode in {"allow", "prefer"}:
        return None

    context = ssl.create_default_context(cafile=root_cert or None)
    if cert_file:
        context.load_cert_chain(certfile=cert_file, keyfile=key_file or None)

    if sslmode == "require" and root_cert is None:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    elif sslmode == "verify-ca":
        context.check_hostname = False
    else:
        context.check_hostname = bool(host)
    return context
