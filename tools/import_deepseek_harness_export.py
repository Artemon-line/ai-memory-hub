from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

if __package__:
    from tools import import_to_memory
else:
    import import_to_memory


MAX_ARCHIVE_BYTES = 25_000_000
MAX_ENTRIES = 1_000
MAX_UNCOMPRESSED_BYTES = 50_000_000
MAX_SESSION_BYTES = 20_000_000
MIN_ARCHIVE_AGE_SECONDS = 5.0
_SESSION_NAME = re.compile(r"^session(?:\.v[0-9]+)?\.jsonl$")


class ArchiveError(ValueError):
    pass


def _normalized_member_name(raw_name: str) -> str:
    if not raw_name or "\x00" in raw_name:
        raise ArchiveError("archive contains an invalid member name")
    portable_name = raw_name.replace("\\", "/")
    if portable_name.startswith("/") or re.match(r"^[A-Za-z]:", portable_name):
        raise ArchiveError("archive contains an absolute member path")
    segments = portable_name.split("/")
    if segments[-1] == "":
        segments = segments[:-1]
    if not segments or any(part in {"", ".", ".."} for part in segments):
        raise ArchiveError("archive contains an unsafe member path")
    path = PurePosixPath(portable_name)
    return path.as_posix()


def _validate_member_type(info: zipfile.ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise ArchiveError("archive contains an encrypted member")
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    allowed = {0, stat.S_IFREG, stat.S_IFDIR}
    if kind not in allowed:
        raise ArchiveError("archive contains a link or special file")


def _inspect_archive(
    archive: zipfile.ZipFile, *, requested_member: str | None
) -> tuple[zipfile.ZipInfo, str]:
    infos = archive.infolist()
    if not infos:
        raise ArchiveError("archive is empty")
    if len(infos) > MAX_ENTRIES:
        raise ArchiveError(f"archive contains more than {MAX_ENTRIES} entries")

    normalized: dict[str, tuple[zipfile.ZipInfo, str]] = {}
    total_size = 0
    for info in infos:
        name = _normalized_member_name(info.filename)
        key = name.casefold()
        if key in normalized:
            raise ArchiveError("archive contains duplicate member names")
        _validate_member_type(info)
        total_size += info.file_size
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise ArchiveError("archive exceeds the uncompressed size limit")
        normalized[key] = (info, name)

    if requested_member is not None:
        requested = _normalized_member_name(requested_member)
        selected = normalized.get(requested.casefold())
        if selected is None or not _SESSION_NAME.fullmatch(PurePosixPath(selected[1]).name):
            raise ArchiveError("requested session member is missing or unsupported")
        return selected

    candidates: list[tuple[zipfile.ZipInfo, str]] = []
    for info, name in normalized.values():
        path = PurePosixPath(name)
        if info.is_dir() or not _SESSION_NAME.fullmatch(path.name):
            continue
        if "subagents" in {part.casefold() for part in path.parts[:-1]}:
            continue
        if len(path.parts) in {1, 2}:
            candidates.append((info, name))
    if len(candidates) != 1:
        raise ArchiveError("archive must contain exactly one unambiguous root session JSONL")
    return candidates[0]


def _checked_archive_stat(path: Path, *, now: float | None = None) -> os.stat_result:
    try:
        if path.is_symlink():
            raise ArchiveError("archive path must not be a symbolic link")
        details = path.stat()
    except FileNotFoundError as exc:
        raise ArchiveError("archive does not exist") from exc
    if not stat.S_ISREG(details.st_mode):
        raise ArchiveError("archive path must be a regular file")
    if details.st_size == 0:
        raise ArchiveError("archive is empty")
    if details.st_size > MAX_ARCHIVE_BYTES:
        raise ArchiveError("archive exceeds the compressed size limit")
    current_time = time.time() if now is None else now
    if current_time - details.st_mtime < MIN_ARCHIVE_AGE_SECONDS:
        raise ArchiveError("archive is too recent; wait for the download to finish")
    return details


def _archive_identity(details: os.stat_result) -> tuple[int, int, int, int]:
    return (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns)


def prepare_session(
    archive_path: Path, destination: Path, *, requested_member: str | None = None
) -> str:
    before = _checked_archive_stat(archive_path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            info, member_name = _inspect_archive(
                archive, requested_member=requested_member
            )
            if info.file_size == 0:
                raise ArchiveError("selected session member is empty")
            if info.file_size > MAX_SESSION_BYTES:
                raise ArchiveError("selected session member exceeds the importer size limit")
            written = 0
            with archive.open(info) as source, destination.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    if written > MAX_SESSION_BYTES or written > info.file_size:
                        raise ArchiveError("selected session member exceeds its declared size")
                    target.write(chunk)
            if written != info.file_size:
                raise ArchiveError("selected session member is incomplete")
    except zipfile.BadZipFile as exc:
        raise ArchiveError("archive is not a valid ZIP file") from exc
    after = archive_path.stat()
    if _archive_identity(before) != _archive_identity(after):
        raise ArchiveError("archive changed during extraction")
    return member_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely prepare and upload a completed DeepSeek Harness session ZIP."
    )
    parser.add_argument("archive", type=Path, help="Completed dsh-session-*.zip download.")
    parser.add_argument(
        "--member",
        help="Explicit descendant session path in the ZIP; descendants are never merged automatically.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="ai-memory-deepseek-") as directory:
            session_path = Path(directory) / "session.jsonl"
            prepare_session(args.archive, session_path, requested_member=args.member)
            return import_to_memory.main(
                [
                    "deepseek-harness-session-jsonl",
                    str(session_path),
                    "--min-age-seconds",
                    "0",
                ]
            )
    except (ArchiveError, OSError) as exc:
        message = str(exc) if isinstance(exc, ArchiveError) else "local archive operation failed"
        print(f"import failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
