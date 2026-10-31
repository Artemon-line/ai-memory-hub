from __future__ import annotations

import os
import stat
import time
import warnings
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from tools import import_deepseek_harness_export as deepseek_export

_SESSION = b'{"type":"session","version":4,"id":"root","createdAt":1}\n'


def _archive(
    tmp_path: Path, members: Sequence[tuple[str | zipfile.ZipInfo, bytes]]
) -> Path:
    path = tmp_path / "dsh-session.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in members:
                archive.writestr(name, content)
    old = time.time() - 10
    os.utime(path, (old, old))
    return path


def test_main_extracts_root_uploads_it_and_removes_temporary_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = _archive(
        tmp_path,
        [
            ("dsh-session-root/session.v4.jsonl", _SESSION),
            ("dsh-session-root/subagents/child/session.v4.jsonl", _SESSION),
            ("dsh-session-root/files/note.txt", b"attachment"),
        ],
    )
    upload_path: Path | None = None
    calls: list[list[str]] = []

    def fake_upload(arguments: list[str]) -> int:
        nonlocal upload_path
        upload_path = Path(arguments[1])
        assert upload_path.read_bytes() == _SESSION
        calls.append(arguments)
        return 0

    monkeypatch.setattr(deepseek_export.import_to_memory, "main", fake_upload)

    assert deepseek_export.main([str(archive)]) == 0
    assert calls == [
        [
            "deepseek-harness-session-jsonl",
            str(upload_path),
            "--min-age-seconds",
            "0",
        ]
    ]
    assert upload_path is not None and not upload_path.exists()
    assert not upload_path.parent.exists()


def test_descendant_requires_an_explicit_member(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = _archive(
        tmp_path,
        [
            ("session.v4.jsonl", _SESSION),
            ("subagents/child/session.v3.jsonl", b"child"),
        ],
    )
    uploaded: list[bytes] = []

    def fake_upload(arguments: list[str]) -> int:
        uploaded.append(Path(arguments[1]).read_bytes())
        return 0

    monkeypatch.setattr(deepseek_export.import_to_memory, "main", fake_upload)

    assert deepseek_export.main([str(archive)]) == 0
    assert uploaded == [_SESSION]
    assert (
        deepseek_export.main(
            [str(archive), "--member", "subagents/child/session.v3.jsonl"]
        )
        == 0
    )
    assert uploaded == [_SESSION, b"child"]


@pytest.mark.parametrize(
    "members, message",
    [
        ([('../session.jsonl', _SESSION)], "unsafe member path"),
        ([('./session.jsonl', _SESSION)], "unsafe member path"),
        ([('folder//session.jsonl', _SESSION)], "unsafe member path"),
        ([('/session.jsonl', _SESSION)], "absolute member path"),
        ([('C:\\session.jsonl', _SESSION)], "absolute member path"),
        (
            [('session.jsonl', _SESSION), ('SESSION.JSONL', _SESSION)],
            "duplicate member names",
        ),
        (
            [('session.jsonl', _SESSION), ('session.v4.jsonl', _SESSION)],
            "exactly one unambiguous root",
        ),
        ([('nested/deeper/session.jsonl', _SESSION)], "exactly one unambiguous root"),
        ([('notes.txt', b"none")], "exactly one unambiguous root"),
    ],
)
def test_unsafe_or_ambiguous_archives_are_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    members: list[tuple[str, bytes]],
    message: str,
) -> None:
    archive = _archive(tmp_path, members)

    assert deepseek_export.main([str(archive)]) == 1
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFCHR, stat.S_IFIFO])
def test_links_and_special_files_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: int
) -> None:
    member = zipfile.ZipInfo("session.jsonl")
    member.create_system = 3
    member.external_attr = (kind | 0o600) << 16
    archive = _archive(tmp_path, [(member, _SESSION)])

    assert deepseek_export.main([str(archive)]) == 1
    assert "link or special file" in capsys.readouterr().err


def test_archive_entry_and_uncompressed_limits_are_enforced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = _archive(tmp_path, [("session.jsonl", _SESSION), ("note", b"x")])
    monkeypatch.setattr(deepseek_export, "MAX_ENTRIES", 1)
    assert deepseek_export.main([str(archive)]) == 1
    assert "more than 1 entries" in capsys.readouterr().err

    monkeypatch.setattr(deepseek_export, "MAX_ENTRIES", 10)
    monkeypatch.setattr(deepseek_export, "MAX_UNCOMPRESSED_BYTES", len(_SESSION))
    assert deepseek_export.main([str(archive)]) == 1
    assert "uncompressed size limit" in capsys.readouterr().err


def test_recent_oversized_malformed_and_symlink_archives_are_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    recent = _archive(tmp_path, [("session.jsonl", _SESSION)])
    os.utime(recent, None)
    assert deepseek_export.main([str(recent)]) == 1
    assert "too recent" in capsys.readouterr().err

    old = time.time() - 10
    os.utime(recent, (old, old))
    monkeypatch.setattr(deepseek_export, "MAX_ARCHIVE_BYTES", recent.stat().st_size - 1)
    assert deepseek_export.main([str(recent)]) == 1
    assert "compressed size limit" in capsys.readouterr().err

    malformed = tmp_path / "malformed.zip"
    malformed.write_bytes(b"not a zip")
    os.utime(malformed, (old, old))
    monkeypatch.setattr(deepseek_export, "MAX_ARCHIVE_BYTES", 25_000_000)
    assert deepseek_export.main([str(malformed)]) == 1
    assert "not a valid ZIP" in capsys.readouterr().err

    link = tmp_path / "linked.zip"
    try:
        link.symlink_to(recent)
    except OSError:
        pytest.skip("symlinks are unavailable")
    assert deepseek_export.main([str(link)]) == 1
    assert "symbolic link" in capsys.readouterr().err


def test_selected_session_size_limit_is_enforced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = _archive(tmp_path, [("session.jsonl", _SESSION)])
    monkeypatch.setattr(deepseek_export, "MAX_SESSION_BYTES", len(_SESSION) - 1)

    assert deepseek_export.main([str(archive)]) == 1
    assert "importer size limit" in capsys.readouterr().err
