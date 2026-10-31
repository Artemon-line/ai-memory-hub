from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tools import import_openclaw_session

_HEADER = '{"type":"session","version":3,"id":"openclaw-session"}\n'


def test_compatible_legacy_artifact_delegates_to_shared_uploader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = tmp_path / "selected-session.jsonl"
    artifact.write_text(_HEADER + '{"type":"message"}\n', encoding="utf-8")
    old = time.time() - 10
    os.utime(artifact, (old, old))
    calls: list[list[str]] = []
    monkeypatch.setattr(
        import_openclaw_session.import_to_memory,
        "main",
        lambda arguments: calls.append(arguments) or 0,
    )

    assert import_openclaw_session.main([str(artifact)]) == 0
    assert calls == [["pi-session-jsonl", str(artifact), "openclaw"]]


def test_optional_title_record_before_header_is_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = tmp_path / "selected-session.jsonl"
    artifact.write_text('{"type":"title","title":"Example"}\n' + _HEADER, encoding="utf-8")
    monkeypatch.setattr(import_openclaw_session.import_to_memory, "main", lambda _args: 0)

    assert import_openclaw_session.main([str(artifact)]) == 0


@pytest.mark.parametrize(
    "name",
    [
        "openclaw-agent.sqlite",
        "openclaw-agent.sqlite-wal",
        "openclaw-agent.sqlite-shm",
        "state.db",
        "state.db-wal",
        "state.db-shm",
        "state.db-journal",
    ],
)
def test_database_and_sidecar_paths_are_refused_before_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    called = False

    def unexpected_upload(_arguments: list[str]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(import_openclaw_session.import_to_memory, "main", unexpected_upload)

    assert import_openclaw_session.main([str(tmp_path / name)]) == 1
    assert called is False


@pytest.mark.parametrize(
    "content",
    [
        '{"type":"session","version":2,"id":"old"}\n',
        '{"type":"transcript","messages":[]}\n',
        '{"type":"session","version":3,"id":""}\n',
        "not json\n",
        "",
    ],
)
def test_unsupported_or_invalid_jsonl_is_refused_before_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str
) -> None:
    artifact = tmp_path / "current-export.jsonl"
    artifact.write_text(content, encoding="utf-8")
    called = False

    def unexpected_upload(_arguments: list[str]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(import_openclaw_session.import_to_memory, "main", unexpected_upload)

    assert import_openclaw_session.main([str(artifact)]) == 1
    assert called is False


def test_relative_non_jsonl_and_symlink_paths_are_refused(tmp_path: Path) -> None:
    with pytest.raises(import_openclaw_session.OpenClawArtifactError, match="absolute"):
        import_openclaw_session.normalize_artifact_path("session.jsonl")
    with pytest.raises(import_openclaw_session.OpenClawArtifactError, match=".jsonl"):
        import_openclaw_session.normalize_artifact_path(str(tmp_path / "session.json"))

    target = tmp_path / "session.jsonl"
    target.write_text(_HEADER, encoding="utf-8")
    link = tmp_path / "linked.jsonl"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(import_openclaw_session.OpenClawArtifactError, match="symbolic"):
        import_openclaw_session.normalize_artifact_path(str(link))


def test_oversized_header_line_is_refused(tmp_path: Path) -> None:
    artifact = tmp_path / "large-header.jsonl"
    artifact.write_bytes(b"{" + b"x" * import_openclaw_session.MAX_HEADER_LINE_BYTES + b"}\n")

    with pytest.raises(import_openclaw_session.OpenClawArtifactError, match="too large"):
        import_openclaw_session.verify_legacy_header(artifact)
