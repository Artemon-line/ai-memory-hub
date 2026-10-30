from __future__ import annotations

import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest

from tools import import_hermes_session


def test_single_session_export_uploads_private_artifact_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_path: Path | None = None
    uploader_calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal export_path
        export_path = Path(command[3])
        export_path.write_text('{"id":"session-1"}\n', encoding="utf-8")
        assert command == [
            "hermes",
            "sessions",
            "export",
            str(export_path),
            "--session-id",
            "session-1",
            "--redact",
        ]
        return subprocess.CompletedProcess(command, 0)

    def fake_upload(arguments: list[str]) -> int:
        assert export_path is not None and export_path.is_file()
        uploader_calls.append(arguments)
        return 0

    monkeypatch.setattr(import_hermes_session.subprocess, "run", fake_run)
    monkeypatch.setattr(import_hermes_session.import_to_memory, "main", fake_upload)

    assert import_hermes_session.main(["--session-id", "session-1", "--redact"]) == 0
    assert uploader_calls == [
        ["hermes-session-jsonl", str(export_path), "--min-age-seconds", "0"]
    ]
    assert export_path is not None and not export_path.exists()
    assert not export_path.parent.exists()


def test_ended_multi_session_export_uses_upstream_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        Path(command[3]).write_text('{"id":"session-1"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(import_hermes_session.subprocess, "run", fake_run)
    monkeypatch.setattr(import_hermes_session.import_to_memory, "main", lambda _args: 0)

    assert import_hermes_session.main(["--ended-older-than", "2d"]) == 0
    assert commands[0][-2:] == ["--older-than", "2d"]


def test_export_failure_is_bounded_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    export_parent: Path | None = None
    uploader_called = False

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal export_parent
        export_parent = Path(command[3]).parent
        return subprocess.CompletedProcess(command, 1)

    def unexpected_upload(_arguments: list[str]) -> int:
        nonlocal uploader_called
        uploader_called = True
        return 0

    monkeypatch.setattr(import_hermes_session.subprocess, "run", fake_run)
    monkeypatch.setattr(import_hermes_session.import_to_memory, "main", unexpected_upload)

    assert import_hermes_session.main(["--session-id", "missing-session"]) == 1
    assert uploader_called is False
    assert export_parent is not None and not export_parent.exists()
    assert capsys.readouterr().err == "import failed: Hermes export command failed\n"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--session-id", "--all"],
        ["--session-id", "bad/session"],
        ["--ended-older-than", "0d"],
        ["--ended-older-than", "yesterday"],
    ],
)
def test_unsafe_export_selectors_are_refused_before_command(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    called = False

    def unexpected_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(import_hermes_session.subprocess, "run", unexpected_run)

    expectation = pytest.raises(SystemExit) if arguments[1] == "--all" else nullcontext()
    with expectation:
        result = import_hermes_session.main(arguments)
        assert result == 1
    assert called is False
