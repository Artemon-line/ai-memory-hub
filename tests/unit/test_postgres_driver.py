from __future__ import annotations

import ssl
import tomllib
from pathlib import Path

import pytest

from memory.backend.postgres_driver import TransactionConnection, connection_kwargs


def test_postgres_extra_uses_only_pg8000_driver() -> None:
    with Path("pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    dependencies = pyproject["project"]["optional-dependencies"]["postgres"]

    assert {dependency.split(">=")[0] for dependency in dependencies} == {"pg8000"}


def test_connection_kwargs_preserves_postgres_url_configuration() -> None:
    kwargs = connection_kwargs(
        "postgresql://memory%2Bagent:p%40ss@db.example:5544/memory%2Fdev"
        "?sslmode=disable&connect_timeout=2.5&application_name=ai-memory-hub"
        "&search_path=memory"
    )

    assert kwargs == {
        "user": "memory+agent",
        "password": "p@ss",
        "host": "db.example",
        "port": 5544,
        "database": "memory/dev",
        "timeout": 2.5,
        "application_name": "ai-memory-hub",
        "ssl_context": False,
        "startup_params": {"search_path": "memory"},
    }


def test_connection_kwargs_supports_required_tls_without_certificate_verification() -> None:
    kwargs = connection_kwargs("postgres://user@db.example/app?sslmode=require")

    context = kwargs["ssl_context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE


def test_connection_kwargs_rejects_unknown_sslmode() -> None:
    with pytest.raises(ValueError, match="Unsupported PostgreSQL sslmode"):
        connection_kwargs("postgres://user@db.example/app?sslmode=surprise")


def test_connection_kwargs_supports_unix_socket_directory() -> None:
    kwargs = connection_kwargs(
        "postgresql:///memory?host=%2Fvar%2Frun%2Fpostgresql&port=5544&sslmode=disable"
    )

    assert kwargs["host"] is None
    assert kwargs["unix_sock"] == "/var/run/postgresql/.s.PGSQL.5544"


class _RecordingCursor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("cursor_close")


class _RecordingConnection:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.raw_cursor = _RecordingCursor(self.events)

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.events.append("close")

    def cursor(self) -> _RecordingCursor:
        return self.raw_cursor


def test_transaction_connection_commits_and_closes_on_success() -> None:
    raw = _RecordingConnection()

    with TransactionConnection(raw) as connection:
        with connection.cursor() as cursor:
            assert cursor is raw.raw_cursor

    assert raw.events == ["cursor_close", "commit", "close"]


def test_transaction_connection_rolls_back_and_closes_on_error() -> None:
    raw = _RecordingConnection()

    with pytest.raises(RuntimeError, match="failed"):
        with TransactionConnection(raw):
            raise RuntimeError("failed")

    assert raw.events == ["rollback", "close"]
