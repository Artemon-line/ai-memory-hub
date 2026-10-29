from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 20_000_000
MAX_RESPONSE_BYTES = 64_000
MAX_HOOK_INPUT_BYTES = 64_000
DEFAULT_MIN_AGE_SECONDS = 5.0
_PARSER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


class UploadError(RuntimeError):
    pass


def snapshot_closed_file(
    source: Path,
    *,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    now: float | None = None,
) -> Path:
    try:
        before = source.stat()
    except FileNotFoundError as exc:
        raise UploadError("conversation file does not exist") from exc
    if not stat.S_ISREG(before.st_mode):
        raise UploadError("conversation path must be a regular file")
    if before.st_size == 0:
        raise UploadError("conversation file is empty")
    if before.st_size > MAX_FILE_BYTES:
        raise UploadError(f"conversation file exceeds {MAX_FILE_BYTES} bytes")
    current_time = time.time() if now is None else now
    if current_time - before.st_mtime < min_age_seconds:
        raise UploadError("conversation file is too recent; close or export it, then retry")

    suffix = source.suffix[:32]
    handle, snapshot_name = tempfile.mkstemp(prefix="ai-memory-import-", suffix=suffix)
    snapshot = Path(snapshot_name)
    try:
        with os.fdopen(handle, "wb") as target, source.open("rb") as original:
            shutil.copyfileobj(original, target, length=1024 * 1024)
        after = source.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise UploadError("conversation file changed while it was copied; upload refused")
        if snapshot.stat().st_size != before.st_size:
            raise UploadError("safe snapshot is incomplete; upload refused")
        return snapshot
    except Exception:
        snapshot.unlink(missing_ok=True)
        raise


def build_multipart(
    *, parser: str, source: str | None, filename: str, content: bytes
) -> tuple[bytes, str]:
    boundary = f"ai-memory-hub-{secrets.token_hex(16)}"
    fields = [("parser", parser)]
    if source:
        fields.append(("source", source))
    body = bytearray()
    for name, value in fields:
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name) or "conversation"
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        (
            f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
    )
    body.extend(content)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(body), boundary


def upload_snapshot(
    snapshot: Path,
    *,
    filename: str,
    parser: str,
    source: str | None,
    hub_url: str,
    token: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    content = snapshot.read_bytes()
    body, boundary = build_multipart(
        parser=parser, source=source, filename=filename, content=content
    )
    request = urllib.request.Request(
        f"{hub_url.rstrip('/')}/memory/import",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        response_body = exc.read(MAX_RESPONSE_BYTES + 1)
        detail = _safe_error_detail(response_body)
        raise UploadError(f"hub rejected import with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise UploadError("could not reach the memory hub") from exc
    if len(response_body) > MAX_RESPONSE_BYTES:
        raise UploadError("hub response exceeded the safe receipt limit")
    try:
        result = json.loads(response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UploadError("hub returned an invalid JSON receipt") from exc
    if not isinstance(result, dict):
        raise UploadError("hub returned an invalid import receipt")
    return result


def bounded_receipt(result: dict[str, Any]) -> dict[str, Any]:
    receipts: list[dict[str, Any]] = []
    raw_results = result.get("results")
    if isinstance(raw_results, list):
        for item in raw_results[:100]:
            if not isinstance(item, dict):
                continue
            receipts.append(
                {
                    key: item[key]
                    for key in ("index", "status", "id", "deduplicated", "error_code")
                    if key in item
                }
            )
    return {
        key: value
        for key, value in {
            "status": result.get("status"),
            "parser": result.get("parser"),
            "conversation_count": result.get("conversation_count"),
            "results": receipts,
        }.items()
        if value is not None
    }


def _safe_error_detail(body: bytes) -> str:
    if len(body) > MAX_RESPONSE_BYTES:
        return "response too large"
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "request failed"
    detail = parsed.get("detail") if isinstance(parsed, dict) else None
    if not isinstance(detail, str):
        return "request failed"
    return detail[:500].replace("\r", " ").replace("\n", " ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely snapshot and upload a closed conversation artifact."
    )
    parser.add_argument("parser", help="Registered ai-memory-hub parser name.")
    parser.add_argument("file", type=Path, nargs="?", help="Closed session or completed export file.")
    parser.add_argument("source", nargs="?", help="Optional import source label.")
    parser.add_argument(
        "--hook-json-field",
        help="Read a bounded JSON hook event from stdin and use this field as the file path.",
    )
    parser.add_argument(
        "--min-age-seconds",
        type=float,
        default=DEFAULT_MIN_AGE_SECONDS,
        help="Refuse files modified more recently than this (default: 5).",
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    hub_url = os.environ.get("MEMORY_HUB_URL", "")
    token = os.environ.get("MEMORY_API_TOKEN", "")
    try:
        if not hub_url:
            raise UploadError("MEMORY_HUB_URL is required")
        if not token:
            raise UploadError("MEMORY_API_TOKEN is required")
        if not _PARSER_PATTERN.fullmatch(args.parser):
            raise UploadError("parser name is invalid")
        if args.source and (len(args.source) > 128 or any(ord(char) < 32 for char in args.source)):
            raise UploadError("source label is invalid")
        if args.min_age_seconds < 0 or args.timeout_seconds <= 0:
            raise UploadError("age and timeout values must be positive")
        upload_file = _resolve_upload_file(args.file, args.hook_json_field)
        snapshot = snapshot_closed_file(upload_file, min_age_seconds=args.min_age_seconds)
        try:
            result = upload_snapshot(
                snapshot,
                filename=upload_file.name,
                parser=args.parser,
                source=args.source,
                hub_url=hub_url,
                token=token,
                timeout_seconds=args.timeout_seconds,
            )
        finally:
            snapshot.unlink(missing_ok=True)
        print(json.dumps(bounded_receipt(result), separators=(",", ":")))
        return 0 if result.get("status") == "ok" else 1
    except UploadError as exc:
        print(f"import failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("import failed: local file or network operation failed", file=sys.stderr)
        return 1


def _resolve_upload_file(file: Path | None, hook_json_field: str | None) -> Path:
    if file is not None and hook_json_field is not None:
        raise UploadError("pass a file path or --hook-json-field, not both")
    if file is not None:
        return file
    if not hook_json_field:
        raise UploadError("a conversation file or --hook-json-field is required")
    raw = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
    if len(raw) > MAX_HOOK_INPUT_BYTES:
        raise UploadError("hook input exceeds the safe limit")
    try:
        event = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UploadError("hook input must be valid JSON") from exc
    value = event.get(hook_json_field) if isinstance(event, dict) else None
    if not isinstance(value, str) or not value:
        raise UploadError(f"hook input has no valid {hook_json_field} path")
    return Path(value)


if __name__ == "__main__":
    raise SystemExit(main())
