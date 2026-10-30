from __future__ import annotations

from memory.backend.redaction import redact_content_hashes
from memory.ingestion.imported_session import (
    IMPORTED_SESSION_IDENTITY_KEY,
    derive_imported_session_identity,
    stamp_imported_session_identity,
)


def _identity(
    *,
    project_id: str = "project-a",
    source: str = "opencode",
    parser: str = "opencode-session-json",
    source_session_id: str = "session-1",
) -> str | None:
    return derive_imported_session_identity(
        source=source,
        project_id=project_id,
        metadata={
            "import_parser": parser,
            "source_session_id": source_session_id,
        },
    )


def test_identity_is_stable_and_normalizes_source_and_parser() -> None:
    expected = _identity()

    assert expected is not None
    assert expected.startswith("sha256:")
    assert _identity(source=" OpenCode ", parser=" OPENCODE-SESSION-JSON ") == expected


def test_identity_is_scoped_by_project_source_and_parser() -> None:
    baseline = _identity()

    assert _identity(project_id="project-b") != baseline
    assert _identity(source="codex") != baseline
    assert _identity(parser="manual") != baseline


def test_missing_session_or_parser_has_no_stable_identity() -> None:
    assert (
        derive_imported_session_identity(
            source="opencode",
            project_id="project-a",
            metadata={"import_parser": "opencode-session-json"},
        )
        is None
    )
    assert (
        derive_imported_session_identity(
            source="opencode",
            project_id="project-a",
            metadata={"source_session_id": "session-1"},
        )
        is None
    )


def test_stamp_replaces_caller_supplied_identity() -> None:
    conversation = {
        "source": "opencode",
        "metadata": {
            "project_id": "project-a",
            "import_parser": "opencode-session-json",
            "source_session_id": "session-1",
            IMPORTED_SESSION_IDENTITY_KEY: "caller-controlled",
        },
    }

    identity = stamp_imported_session_identity(conversation)

    assert identity == _identity()
    assert conversation["metadata"][IMPORTED_SESSION_IDENTITY_KEY] == identity


def test_stamp_removes_identity_when_upstream_id_is_unavailable() -> None:
    conversation = {
        "source": "manual",
        "metadata": {
            "project_id": "project-a",
            "import_parser": "manual",
            IMPORTED_SESSION_IDENTITY_KEY: "caller-controlled",
        },
    }

    assert stamp_imported_session_identity(conversation) is None
    assert IMPORTED_SESSION_IDENTITY_KEY not in conversation["metadata"]


def test_public_response_redaction_removes_imported_session_identity() -> None:
    value = {
        "metadata": {
            IMPORTED_SESSION_IDENTITY_KEY: _identity(),
            "source_session_id": "session-1",
        }
    }

    redacted = redact_content_hashes(value)

    assert IMPORTED_SESSION_IDENTITY_KEY not in redacted["metadata"]
    assert redacted["metadata"]["source_session_id"] == "session-1"
