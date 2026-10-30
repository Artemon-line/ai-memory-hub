from __future__ import annotations

from pathlib import Path

import pytest

from tools import import_pi_session


def test_normalize_session_path_requires_an_absolute_jsonl_path(tmp_path: Path) -> None:
    session = tmp_path / "completed.jsonl"

    assert import_pi_session.normalize_session_path(str(session)) == session

    with pytest.raises(import_pi_session.PiSessionPathError, match="absolute"):
        import_pi_session.normalize_session_path("completed.jsonl")
    with pytest.raises(import_pi_session.PiSessionPathError, match=".jsonl"):
        import_pi_session.normalize_session_path(str(tmp_path / "completed.json"))


def test_main_delegates_exact_path_to_shared_uploader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session = tmp_path / "chosen-session.jsonl"
    calls: list[list[str]] = []
    monkeypatch.setattr(
        import_pi_session.import_to_memory,
        "main",
        lambda args: calls.append(args) or 0,
    )

    assert import_pi_session.main([str(session)]) == 0
    assert calls == [["pi-session-jsonl", str(session), "pi"]]


def test_main_refuses_invalid_path_before_uploader(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    called = False

    def unexpected_call(_args: list[str]) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr(import_pi_session.import_to_memory, "main", unexpected_call)

    assert import_pi_session.main(["relative-session.jsonl"]) == 1
    assert called is False
    assert "must be absolute" in capsys.readouterr().err
