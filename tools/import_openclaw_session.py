from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__:
    from tools import import_to_memory
else:
    import import_to_memory


MAX_HEADER_LINE_BYTES = 64_000
_DATABASE_ENDINGS = (
    ".db",
    ".db-journal",
    ".db-shm",
    ".db-wal",
    ".sqlite",
    ".sqlite-journal",
    ".sqlite-shm",
    ".sqlite-wal",
)


class OpenClawArtifactError(ValueError):
    pass


def normalize_artifact_path(raw_path: str) -> Path:
    artifact = Path(raw_path).expanduser()
    if not artifact.is_absolute():
        raise OpenClawArtifactError("OpenClaw artifact path must be absolute")
    lowered = artifact.name.lower()
    if lowered.endswith(_DATABASE_ENDINGS):
        raise OpenClawArtifactError("live OpenClaw database, WAL, and SHM files are refused")
    if artifact.suffix.lower() != ".jsonl":
        raise OpenClawArtifactError("OpenClaw artifact must be a compatible .jsonl file")
    if artifact.is_symlink():
        raise OpenClawArtifactError("OpenClaw artifact path must not be a symbolic link")
    return artifact


def _read_header_record(handle: Any) -> dict[str, Any]:
    for _ in range(2):
        raw_line = handle.readline(MAX_HEADER_LINE_BYTES + 1)
        if len(raw_line) > MAX_HEADER_LINE_BYTES:
            raise OpenClawArtifactError("OpenClaw artifact header line is too large")
        if not raw_line:
            break
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenClawArtifactError("OpenClaw artifact header is not valid JSON") from exc
        if not isinstance(record, dict):
            raise OpenClawArtifactError("OpenClaw artifact header must be an object")
        if record.get("type") == "title":
            continue
        return record
    raise OpenClawArtifactError("OpenClaw artifact has no session header")


def verify_legacy_header(artifact: Path) -> None:
    try:
        with artifact.open("rb") as handle:
            header = _read_header_record(handle)
    except FileNotFoundError as exc:
        raise OpenClawArtifactError("OpenClaw artifact does not exist") from exc
    if header.get("type") != "session" or header.get("version") != 3:
        raise OpenClawArtifactError(
            "OpenClaw artifact is not a compatible legacy version 3 session tree"
        )
    if not isinstance(header.get("id"), str) or not header["id"]:
        raise OpenClawArtifactError("OpenClaw artifact session header is invalid")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload one explicit compatible OpenClaw legacy/exported JSONL artifact."
    )
    parser.add_argument(
        "artifact",
        help="Absolute path to a closed version 3 JSONL tree; databases are refused.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact = normalize_artifact_path(args.artifact)
        verify_legacy_header(artifact)
    except (OpenClawArtifactError, OSError) as exc:
        message = (
            str(exc)
            if isinstance(exc, OpenClawArtifactError)
            else "local artifact operation failed"
        )
        print(f"import failed: {message}", file=sys.stderr)
        return 1
    return import_to_memory.main(["pi-session-jsonl", str(artifact), "openclaw"])


if __name__ == "__main__":
    raise SystemExit(main())
