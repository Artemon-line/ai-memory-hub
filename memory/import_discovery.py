from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path, PurePath
from typing import Any


class DiscoverySource(StrEnum):
    CODEX = "codex"
    CLAUDE_CODE = "claude-code"
    COPILOT_CLI = "copilot-cli"
    PI = "pi"
    OH_MY_PI = "oh-my-pi"


class ExportOnlySource(StrEnum):
    HERMES = "hermes"
    OPENCODE = "opencode"
    GEMINI_CLI = "gemini-cli"
    QWEN_CODE = "qwen-code"
    OPENCLAW = "openclaw"


EXPORT_INSTRUCTIONS: Mapping[str, str] = {
    ExportOnlySource.HERMES: "run `hermes sessions export <FILE> [--redact]`, then import the export",
    ExportOnlySource.OPENCODE: (
        "run `opencode session export <ID> --sanitize > <FILE>`, then import the export"
    ),
    ExportOnlySource.GEMINI_CLI: "run `/chat share <FILE>` in Gemini CLI, then import the export",
    ExportOnlySource.QWEN_CODE: "run `/export json` or `/export jsonl`, then import the export",
    ExportOnlySource.OPENCLAW: (
        "create a coordinated JSONL export or backup; live OpenClaw SQLite is not supported"
    ),
}


@dataclass(frozen=True)
class DiscoveryProvider:
    source: DiscoverySource
    importer: str
    stored_source: str
    environment_variable: str
    default_home_parts: tuple[str, ...]
    artifact_pattern: str
    focused_test: str


DISCOVERY_PROVIDERS: tuple[DiscoveryProvider, ...] = (
    DiscoveryProvider(
        DiscoverySource.CODEX,
        "codex-rollout-jsonl",
        "codex",
        "CODEX_HOME",
        (".codex",),
        "sessions/YYYY/MM/DD/rollout-*.jsonl and archived_sessions/rollout-*.jsonl",
        "test_discovery_provider_matrix_finds_latest_safe_artifact",
    ),
    DiscoveryProvider(
        DiscoverySource.CLAUDE_CODE,
        "claude-code-session-jsonl",
        "claude-code",
        "CLAUDE_CONFIG_DIR",
        (".claude",),
        "projects/<project>/*.jsonl (top-level sessions only)",
        "test_discovery_provider_matrix_finds_latest_safe_artifact",
    ),
    DiscoveryProvider(
        DiscoverySource.COPILOT_CLI,
        "copilot-cli-events-jsonl",
        "copilot-cli",
        "COPILOT_HOME",
        (".copilot",),
        "session-state/<id>/events.jsonl",
        "test_discovery_provider_matrix_finds_latest_safe_artifact",
    ),
    DiscoveryProvider(
        DiscoverySource.PI,
        "pi-session-jsonl",
        "pi",
        "PI_CODING_AGENT_DIR",
        (".pi", "agent"),
        "sessions/**/*.jsonl",
        "test_discovery_provider_matrix_finds_latest_safe_artifact",
    ),
    DiscoveryProvider(
        DiscoverySource.OH_MY_PI,
        "pi-session-jsonl",
        "oh-my-pi",
        "PI_CODING_AGENT_DIR",
        (".omp", "agent"),
        "sessions/**/*.jsonl",
        "test_discovery_provider_matrix_finds_latest_safe_artifact",
    ),
)


@dataclass(frozen=True)
class DiscoveredArtifact:
    source: str
    importer: str
    stored_source: str
    session_id: str
    timestamp: datetime
    path: Path
    size: int
    modified_ns: int
    safe_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "importer": self.importer,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "path": str(self.path),
            "safe_reason": self.safe_reason,
        }


class DiscoveryError(ValueError):
    """A safe, actionable local discovery failure."""


@dataclass(frozen=True)
class _InspectedHeader:
    session_id: str
    timestamp: datetime
    terminal: bool = False


def default_root_for(provider: DiscoveryProvider, home: PurePath) -> PurePath:
    """Build a platform-native default without hard-coding an operating system prefix."""
    return home.joinpath(*provider.default_home_parts)


class SourceAdapter(ABC):
    def __init__(self, provider: DiscoveryProvider) -> None:
        self.provider = provider

    def roots(self, environment: Mapping[str, str], home: Path) -> tuple[Path, ...]:
        override = environment.get(self.provider.environment_variable)
        if override:
            root = Path(override).expanduser()
            if not root.is_absolute():
                raise DiscoveryError(
                    f"{self.provider.environment_variable} must be an absolute path"
                )
        else:
            root = Path(default_root_for(self.provider, home))
        if root.exists() and root.is_symlink():
            raise DiscoveryError(f"configured discovery root must not be a link: {root}")
        return self._artifact_roots(root)

    @abstractmethod
    def _artifact_roots(self, configured_root: Path) -> tuple[Path, ...]: ...

    @abstractmethod
    def accepts_relative_path(self, relative_path: Path) -> bool: ...

    @abstractmethod
    def inspect(self, path: Path) -> _InspectedHeader | None: ...

    def safe_reason(self, path: Path, header: _InspectedHeader, now: datetime) -> str | None:
        if header.terminal:
            return "source artifact contains a terminal session record"
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if now - modified >= timedelta(minutes=5):
            return "source artifact has been unchanged for at least 5 minutes"
        return None


class _CodexAdapter(SourceAdapter):
    def _artifact_roots(self, configured_root: Path) -> tuple[Path, ...]:
        return configured_root / "sessions", configured_root / "archived_sessions"

    def accepts_relative_path(self, relative_path: Path) -> bool:
        return relative_path.name.startswith("rollout-") and relative_path.suffix == ".jsonl"

    def inspect(self, path: Path) -> _InspectedHeader | None:
        records = _bounded_json_records(path)
        for record in records:
            if record.get("type") != "session_meta":
                continue
            payload = record.get("payload")
            if not isinstance(payload, dict):
                return None
            return _header(payload.get("id"), payload.get("timestamp") or record.get("timestamp"))
        return None

    def safe_reason(self, path: Path, header: _InspectedHeader, now: datetime) -> str | None:
        if path.parent.name == "archived_sessions" or "archived_sessions" in path.parts:
            return "Codex archived the session artifact"
        return super().safe_reason(path, header, now)


class _ClaudeCodeAdapter(SourceAdapter):
    def _artifact_roots(self, configured_root: Path) -> tuple[Path, ...]:
        return (configured_root / "projects",)

    def accepts_relative_path(self, relative_path: Path) -> bool:
        return len(relative_path.parts) == 2 and relative_path.suffix == ".jsonl"

    def inspect(self, path: Path) -> _InspectedHeader | None:
        for record in _bounded_json_records(path):
            if record.get("type") in {"system", "user", "assistant"}:
                header = _header(record.get("sessionId"), record.get("timestamp"))
                if header is not None:
                    return header
        return None


class _CopilotCliAdapter(SourceAdapter):
    def _artifact_roots(self, configured_root: Path) -> tuple[Path, ...]:
        return (configured_root / "session-state",)

    def accepts_relative_path(self, relative_path: Path) -> bool:
        return len(relative_path.parts) == 2 and relative_path.name == "events.jsonl"

    def inspect(self, path: Path) -> _InspectedHeader | None:
        records = _bounded_json_records(path)
        terminal = any(record.get("type") == "session.shutdown" for record in records)
        for record in records:
            if record.get("type") != "session.start":
                continue
            data = record.get("data")
            if not isinstance(data, dict):
                return None
            header = _header(
                data.get("sessionId"), data.get("startTime") or record.get("timestamp")
            )
            if header is not None:
                return _InspectedHeader(header.session_id, header.timestamp, terminal)
        return None


class _PiFamilyAdapter(SourceAdapter):
    def __init__(self, provider: DiscoveryProvider, *, oh_my_pi: bool) -> None:
        super().__init__(provider)
        self._oh_my_pi = oh_my_pi

    def _artifact_roots(self, configured_root: Path) -> tuple[Path, ...]:
        return (configured_root / "sessions",)

    def accepts_relative_path(self, relative_path: Path) -> bool:
        return relative_path.suffix == ".jsonl"

    def inspect(self, path: Path) -> _InspectedHeader | None:
        records = _bounded_json_records(path)
        session_index = next(
            (index for index, record in enumerate(records) if record.get("type") == "session"),
            None,
        )
        if session_index is None:
            return None
        session = records[session_index]
        looks_like_omp = session_index > 0 and records[0].get("type") == "title"
        looks_like_omp = looks_like_omp or "titleSource" in session
        if looks_like_omp != self._oh_my_pi:
            return None
        return _header(session.get("id"), session.get("timestamp"))


class ImportDiscoveryService:
    def __init__(
        self,
        adapters: Mapping[str, SourceAdapter],
        *,
        environment: Mapping[str, str] | None = None,
        home: Path | None = None,
    ) -> None:
        self._adapters = dict(adapters)
        self._environment = os.environ if environment is None else environment
        self._home = Path.home() if home is None else home

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))

    def discover_latest(self, source: str, *, now: datetime | None = None) -> DiscoveredArtifact:
        instruction = EXPORT_INSTRUCTIONS.get(source)
        if instruction is not None:
            raise DiscoveryError(f"{source} requires a supported export: {instruction}")
        adapter = self._adapters.get(source)
        if adapter is None:
            supported = ", ".join((*self.sources, *sorted(EXPORT_INSTRUCTIONS)))
            raise DiscoveryError(f"unknown discovery source: {source}; supported sources: {supported}")

        checked_roots: list[Path] = []
        candidates: list[DiscoveredArtifact] = []
        unsafe_latest: list[tuple[datetime, Path]] = []
        current_time = now or datetime.now(timezone.utc)
        for root in adapter.roots(self._environment, self._home):
            checked_roots.append(root)
            for path in _safe_files(root):
                relative_path = path.relative_to(root)
                if not adapter.accepts_relative_path(relative_path):
                    continue
                header = adapter.inspect(path)
                if header is None:
                    continue
                reason = adapter.safe_reason(path, header, current_time)
                if reason is None:
                    unsafe_latest.append((header.timestamp, path))
                    continue
                stat = path.stat()
                candidates.append(
                    DiscoveredArtifact(
                        source=source,
                        importer=adapter.provider.importer,
                        stored_source=adapter.provider.stored_source,
                        session_id=header.session_id,
                        timestamp=header.timestamp,
                        path=path,
                        size=stat.st_size,
                        modified_ns=stat.st_mtime_ns,
                        safe_reason=reason,
                    )
                )

        newest_unsafe = (
            max(unsafe_latest, key=lambda item: (item[0], str(item[1])))
            if unsafe_latest
            else None
        )
        newest_safe_timestamp = (
            max(candidate.timestamp for candidate in candidates) if candidates else None
        )
        if newest_unsafe is not None and (
            newest_safe_timestamp is None or newest_unsafe[0] >= newest_safe_timestamp
        ):
            raise DiscoveryError(
                f"latest {source} artifact may still be active: {newest_unsafe[1]}; "
                "close the client and wait at least 5 minutes before retrying"
            )
        if not candidates:
            roots = ", ".join(str(root) for root in checked_roots)
            raise DiscoveryError(
                f"no supported {source} session artifact found under expected roots: {roots}"
            )

        newest_timestamp = max(candidate.timestamp for candidate in candidates)
        newest = [candidate for candidate in candidates if candidate.timestamp == newest_timestamp]
        if len(newest) != 1:
            paths = ", ".join(str(candidate.path) for candidate in sorted(newest, key=lambda c: str(c.path)))
            raise DiscoveryError(
                f"ambiguous latest {source} session at {newest_timestamp.isoformat()}: {paths}"
            )
        return newest[0]

    @staticmethod
    def verify_unchanged(artifact: DiscoveredArtifact) -> None:
        stat = artifact.path.stat()
        if stat.st_size != artifact.size or stat.st_mtime_ns != artifact.modified_ns:
            raise DiscoveryError(
                f"discovered artifact changed before import: {artifact.path}; retry after closing the client"
            )


def build_discovery_service(
    *, environment: Mapping[str, str] | None = None, home: Path | None = None
) -> ImportDiscoveryService:
    providers = {provider.source: provider for provider in DISCOVERY_PROVIDERS}
    adapters: dict[str, SourceAdapter] = {
        DiscoverySource.CODEX: _CodexAdapter(providers[DiscoverySource.CODEX]),
        DiscoverySource.CLAUDE_CODE: _ClaudeCodeAdapter(
            providers[DiscoverySource.CLAUDE_CODE]
        ),
        DiscoverySource.COPILOT_CLI: _CopilotCliAdapter(
            providers[DiscoverySource.COPILOT_CLI]
        ),
        DiscoverySource.PI: _PiFamilyAdapter(providers[DiscoverySource.PI], oh_my_pi=False),
        DiscoverySource.OH_MY_PI: _PiFamilyAdapter(
            providers[DiscoverySource.OH_MY_PI], oh_my_pi=True
        ),
    }
    return ImportDiscoveryService(adapters, environment=environment, home=home)


def discovery_source_names() -> tuple[str, ...]:
    return tuple(
        sorted((*[provider.source.value for provider in DISCOVERY_PROVIDERS], *EXPORT_INSTRUCTIONS))
    )


def _safe_files(root: Path) -> Sequence[Path]:
    if not root.exists():
        return ()
    if root.is_symlink() or not root.is_dir():
        raise DiscoveryError(f"discovery root must be a real directory, not a link: {root}")
    resolved_root = root.resolve(strict=True)
    files: list[Path] = []
    for directory, directories, filenames in os.walk(resolved_root, followlinks=False):
        directory_path = Path(directory)
        directories[:] = [
            name for name in directories if not (directory_path / name).is_symlink()
        ]
        for filename in filenames:
            path = directory_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(resolved_root):
                continue
            files.append(resolved)
    return tuple(files)


def _bounded_json_records(path: Path) -> tuple[dict[str, Any], ...]:
    limit = 128 * 1024
    with path.open("rb") as handle:
        head = handle.read(limit)
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        tail_start = max(0, size - limit)
        handle.seek(tail_start)
        tail = handle.read(limit)
    chunks = (head,) if tail_start == 0 else (head, tail)
    records: list[dict[str, Any]] = []
    for chunk in chunks:
        for raw_line in chunk.splitlines():
            try:
                value = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                records.append(value)
    return tuple(records)


def _header(session_id: Any, timestamp: Any) -> _InspectedHeader | None:
    if not isinstance(session_id, str) or not session_id:
        return None
    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return None
    return _InspectedHeader(session_id=session_id, timestamp=parsed)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
