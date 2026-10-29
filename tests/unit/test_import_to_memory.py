from __future__ import annotations

import json
import os
import time
import urllib.error
from io import BytesIO
from pathlib import Path

import pytest

from tools import import_to_memory


def _closed_file(tmp_path: Path, content: bytes = b'{"messages": []}') -> Path:
    path = tmp_path / "session.json"
    path.write_bytes(content)
    old = time.time() - 30
    os.utime(path, (old, old))
    return path


def test_snapshot_closed_file_copies_without_exposing_original(tmp_path: Path) -> None:
    original = _closed_file(tmp_path, b"private conversation")

    snapshot = import_to_memory.snapshot_closed_file(original)

    try:
        assert snapshot.read_bytes() == b"private conversation"
        assert snapshot != original
    finally:
        snapshot.unlink()


def test_snapshot_refuses_missing_recent_empty_and_oversized_files(tmp_path: Path) -> None:
    with pytest.raises(import_to_memory.UploadError, match="does not exist"):
        import_to_memory.snapshot_closed_file(tmp_path / "missing.json")

    recent = tmp_path / "recent.json"
    recent.write_text("live", encoding="utf-8")
    with pytest.raises(import_to_memory.UploadError, match="too recent"):
        import_to_memory.snapshot_closed_file(recent)

    empty = _closed_file(tmp_path, b"")
    with pytest.raises(import_to_memory.UploadError, match="empty"):
        import_to_memory.snapshot_closed_file(empty)

    oversized = _closed_file(tmp_path, b"x")
    oversized.write_bytes(b"x" * (import_to_memory.MAX_FILE_BYTES + 1))
    old = time.time() - 30
    os.utime(oversized, (old, old))
    with pytest.raises(import_to_memory.UploadError, match="exceeds"):
        import_to_memory.snapshot_closed_file(oversized)


def test_snapshot_refuses_file_that_changes_during_copy(tmp_path: Path, monkeypatch) -> None:
    original = _closed_file(tmp_path, b"before")
    real_copy = import_to_memory.shutil.copyfileobj

    def changing_copy(source, target, *, length):  # type: ignore[no-untyped-def]
        real_copy(source, target, length=length)
        original.write_bytes(b"changed")

    monkeypatch.setattr(import_to_memory.shutil, "copyfileobj", changing_copy)

    with pytest.raises(import_to_memory.UploadError, match="changed while"):
        import_to_memory.snapshot_closed_file(original)


def test_upload_uses_bearer_multipart_and_returns_bounded_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    snapshot = _closed_file(tmp_path, b"private conversation")
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _limit: int) -> bytes:
            return json.dumps(
                {
                    "status": "ok",
                    "parser": "manual",
                    "conversation_count": 1,
                    "results": [
                        {
                            "index": 0,
                            "status": "stored",
                            "id": "memory-1",
                            "deduplicated": True,
                            "private": "not printed",
                        }
                    ],
                }
            ).encode()

    def fake_urlopen(request, *, timeout):  # type: ignore[no-untyped-def]
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(import_to_memory.urllib.request, "urlopen", fake_urlopen)

    result = import_to_memory.upload_snapshot(
        snapshot,
        filename="session.json",
        parser="manual",
        source="test-client",
        hub_url="http://127.0.0.1:8000/",
        token="secret-token",
        timeout_seconds=12,
    )
    receipt = import_to_memory.bounded_receipt(result)

    request = captured["request"]
    assert request.full_url == "http://127.0.0.1:8000/memory/import"
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert b'filename="session.json"' in request.data
    assert b'name="parser"\r\n\r\nmanual' in request.data
    assert b'name="source"\r\n\r\ntest-client' in request.data
    assert b"private conversation" in request.data
    assert captured["timeout"] == 12
    assert receipt == {
        "status": "ok",
        "parser": "manual",
        "conversation_count": 1,
        "results": [
            {"index": 0, "status": "stored", "id": "memory-1", "deduplicated": True}
        ],
    }


def test_http_failure_is_bounded_and_does_not_print_token_or_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source = _closed_file(tmp_path, b"private conversation")

    def fake_urlopen(_request, *, timeout):  # type: ignore[no-untyped-def]
        del timeout
        raise urllib.error.HTTPError(
            "http://hub/memory/import",
            400,
            "bad request",
            {},
            _BytesResponse(b'{"detail":"invalid import"}'),
        )

    monkeypatch.setattr(import_to_memory.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("MEMORY_HUB_URL", "http://hub")
    monkeypatch.setenv("MEMORY_API_TOKEN", "secret-token")

    assert import_to_memory.main(["manual", str(source)]) == 1

    output = capsys.readouterr()
    assert "HTTP 400: invalid import" in output.err
    assert "secret-token" not in output.err
    assert "private conversation" not in output.err
    assert output.out == ""


def test_hook_json_field_resolves_transcript_without_printing_event(
    tmp_path: Path, monkeypatch
) -> None:
    source = _closed_file(tmp_path)
    event = json.dumps({"transcript_path": str(source), "private": "do not print"}).encode()
    monkeypatch.setattr(import_to_memory.sys, "stdin", _BinaryInput(event))

    assert import_to_memory._resolve_upload_file(None, "transcript_path") == source


def test_hook_json_field_rejects_oversized_or_missing_input(monkeypatch) -> None:
    monkeypatch.setattr(
        import_to_memory.sys,
        "stdin",
        _BinaryInput(b"x" * (import_to_memory.MAX_HOOK_INPUT_BYTES + 1)),
    )
    with pytest.raises(import_to_memory.UploadError, match="safe limit"):
        import_to_memory._resolve_upload_file(None, "transcript_path")

    monkeypatch.setattr(import_to_memory.sys, "stdin", _BinaryInput(b'{"reason":"exit"}'))
    with pytest.raises(import_to_memory.UploadError, match="no valid transcript_path"):
        import_to_memory._resolve_upload_file(None, "transcript_path")


def test_main_returns_failure_for_bounded_partial_receipt(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    source = _closed_file(tmp_path)
    monkeypatch.setenv("MEMORY_HUB_URL", "http://hub")
    monkeypatch.setenv("MEMORY_API_TOKEN", "secret-token")
    monkeypatch.setattr(
        import_to_memory,
        "upload_snapshot",
        lambda *_args, **_kwargs: {
            "status": "partial",
            "parser": "manual",
            "conversation_count": 2,
            "results": [{"index": 1, "status": "failed", "error_code": "insert_failed"}],
        },
    )

    assert import_to_memory.main(["manual", str(source)]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "partial",
        "parser": "manual",
        "conversation_count": 2,
        "results": [{"index": 1, "status": "failed", "error_code": "insert_failed"}],
    }


class _BytesResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def read(self, limit: int) -> bytes:
        return self.content[:limit]

    def close(self) -> None:
        pass


class _BinaryInput:
    def __init__(self, content: bytes) -> None:
        self.buffer = BytesIO(content)
