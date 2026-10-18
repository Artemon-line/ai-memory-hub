from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path, PurePath
from typing import Any, Protocol

from memory.importer_compatibility import ImporterId

DEFAULT_INACTIVE_AFTER = timedelta(minutes=5)
JSONL_INSPECTION_LIMIT_BYTES = 128 * 1024


class DiscoveryCommand(StrEnum):
    LATEST = "latest"


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


class DiscoveryEnvironmentVariable(StrEnum):
    CODEX_HOME = "CODEX_HOME"
    CLAUDE_CONFIG_DIR = "CLAUDE_CONFIG_DIR"
    COPILOT_HOME = "COPILOT_HOME"
    PI_CODING_AGENT_DIR = "PI_CODING_AGENT_DIR"


class DiscoveryPathSegment(StrEnum):
    AGENT = "agent"
    ARCHIVED_SESSIONS = "archived_sessions"
    CLAUDE_HOME = ".claude"
    CODEX_HOME = ".codex"
    COPILOT_HOME = ".copilot"
    OH_MY_PI_HOME = ".omp"
    PI_HOME = ".pi"
    PROJECTS = "projects"
    SESSION_STATE = "session-state"
    SESSIONS = "sessions"


class ArtifactFileName(StrEnum):
    COPILOT_EVENTS = "events.jsonl"


class ArtifactFileSuffix(StrEnum):
    JSONL = ".jsonl"


class ArtifactRecordKey(StrEnum):
    DATA = "data"
    ID = "id"
    PAYLOAD = "payload"
    SESSION_ID = "sessionId"
    START_TIME = "startTime"
    TIMESTAMP = "timestamp"
    TITLE_SOURCE = "titleSource"
    TYPE = "type"


class ArtifactRecordType(StrEnum):
    ASSISTANT = "assistant"
    SESSION = "session"
    SESSION_META = "session_meta"
    SESSION_SHUTDOWN = "session.shutdown"
    SESSION_START = "session.start"
    SYSTEM = "system"
    TITLE = "title"
    USER = "user"


class DiscoveryResultKey(StrEnum):
    IMPORTER = "importer"
    PATH = "path"
    SAFE_REASON = "safe_reason"
    SESSION_ID = "session_id"
    SOURCE = "source"
    TIMESTAMP = "timestamp"


class DiscoveryFocusedTest(StrEnum):
    PROVIDER_MATRIX = "test_discovery_provider_matrix_finds_latest_safe_artifact"


class ArtifactSafetyReason(StrEnum):
    ARCHIVED = "Codex archived the session artifact"
    TERMINAL = "source artifact contains a terminal session record"


EXPORT_INSTRUCTIONS: Mapping[ExportOnlySource, str] = {
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
    importer: ImporterId
    stored_source: DiscoverySource
    environment_variable: DiscoveryEnvironmentVariable
    default_home_parts: tuple[DiscoveryPathSegment, ...]
    artifact_pattern: str
    focused_test: DiscoveryFocusedTest


DISCOVERY_PROVIDERS: tuple[DiscoveryProvider, ...] = (
    DiscoveryProvider(
        DiscoverySource.CODEX,
        ImporterId.CODEX_ROLLOUT_JSONL,
        DiscoverySource.CODEX,
        DiscoveryEnvironmentVariable.CODEX_HOME,
        (DiscoveryPathSegment.CODEX_HOME,),
        "sessions/YYYY/MM/DD/rollout-*.jsonl and archived_sessions/rollout-*.jsonl",
        DiscoveryFocusedTest.PROVIDER_MATRIX,
    ),
    DiscoveryProvider(
        DiscoverySource.CLAUDE_CODE,
        ImporterId.CLAUDE_CODE_SESSION_JSONL,
        DiscoverySource.CLAUDE_CODE,
        DiscoveryEnvironmentVariable.CLAUDE_CONFIG_DIR,
        (DiscoveryPathSegment.CLAUDE_HOME,),
        "projects/<project>/*.jsonl (top-level sessions only)",
        DiscoveryFocusedTest.PROVIDER_MATRIX,
    ),
    DiscoveryProvider(
        DiscoverySource.COPILOT_CLI,
        ImporterId.COPILOT_CLI_EVENTS_JSONL,
        DiscoverySource.COPILOT_CLI,
        DiscoveryEnvironmentVariable.COPILOT_HOME,
        (DiscoveryPathSegment.COPILOT_HOME,),
        "session-state/<id>/events.jsonl",
        DiscoveryFocusedTest.PROVIDER_MATRIX,
    ),
    DiscoveryProvider(
        DiscoverySource.PI,
        ImporterId.PI_SESSION_JSONL,
        DiscoverySource.PI,
        DiscoveryEnvironmentVariable.PI_CODING_AGENT_DIR,
        (DiscoveryPathSegment.PI_HOME, DiscoveryPathSegment.AGENT),
        "sessions/**/*.jsonl",
        DiscoveryFocusedTest.PROVIDER_MATRIX,
    ),
    DiscoveryProvider(
        DiscoverySource.OH_MY_PI,
        ImporterId.PI_SESSION_JSONL,
        DiscoverySource.OH_MY_PI,
        DiscoveryEnvironmentVariable.PI_CODING_AGENT_DIR,
        (DiscoveryPathSegment.OH_MY_PI_HOME, DiscoveryPathSegment.AGENT),
        "sessions/**/*.jsonl",
        DiscoveryFocusedTest.PROVIDER_MATRIX,
    ),
)


@dataclass(frozen=True)
class DiscoveredArtifact:
    source: DiscoverySource
    importer: ImporterId
    stored_source: DiscoverySource
    session_id: str
    timestamp: datetime
    path: Path
    size: int
    modified_ns: int
    safe_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            DiscoveryResultKey.SOURCE: self.source.value,
            DiscoveryResultKey.IMPORTER: self.importer.value,
            DiscoveryResultKey.SESSION_ID: self.session_id,
            DiscoveryResultKey.TIMESTAMP: self.timestamp.isoformat().replace("+00:00", "Z"),
            DiscoveryResultKey.PATH: str(self.path),
            DiscoveryResultKey.SAFE_REASON: self.safe_reason,
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
    return home.joinpath(*(part.value for part in provider.default_home_parts))


class JsonlRecordReader(Protocol):
    def read(self, path: Path) -> tuple[dict[str, Any], ...]: ...


class CandidateFileScanner(Protocol):
    def files(self, root: Path) -> Sequence[Path]: ...


class ArtifactSafetyPolicy(ABC):
    @abstractmethod
    def safe_reason(
        self, path: Path, header: _InspectedHeader, now: datetime
    ) -> str | None: ...

    @property
    @abstractmethod
    def wait_description(self) -> str: ...


class ConservativeArtifactSafetyPolicy(ArtifactSafetyPolicy):
    def __init__(self, inactive_after: timedelta = DEFAULT_INACTIVE_AFTER) -> None:
        self._inactive_after = inactive_after

    @property
    def wait_description(self) -> str:
        seconds = int(self._inactive_after.total_seconds())
        if seconds % 60 == 0:
            minutes = seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        return f"{seconds} seconds"

    def safe_reason(self, path: Path, header: _InspectedHeader, now: datetime) -> str | None:
        if header.terminal:
            return ArtifactSafetyReason.TERMINAL.value
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if now - modified >= self._inactive_after:
            return f"source artifact has been unchanged for at least {self.wait_description}"
        return None


class CodexArtifactSafetyPolicy(ConservativeArtifactSafetyPolicy):
    def safe_reason(self, path: Path, header: _InspectedHeader, now: datetime) -> str | None:
        if DiscoveryPathSegment.ARCHIVED_SESSIONS.value in path.parts:
            return ArtifactSafetyReason.ARCHIVED.value
        return super().safe_reason(path, header, now)


class BoundedJsonlRecordReader:
    def __init__(self, byte_limit: int = JSONL_INSPECTION_LIMIT_BYTES) -> None:
        self._byte_limit = byte_limit

    def read(self, path: Path) -> tuple[dict[str, Any], ...]:
        with path.open("rb") as handle:
            head = handle.read(self._byte_limit)
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            tail_start = max(0, size - self._byte_limit)
            handle.seek(tail_start)
            tail = handle.read(self._byte_limit)
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


class SafeFileScanner:
    def files(self, root: Path) -> Sequence[Path]:
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
                if resolved.is_relative_to(resolved_root):
                    files.append(resolved)
        return tuple(files)


class SourceAdapter(ABC):
    def __init__(
        self,
        provider: DiscoveryProvider,
        record_reader: JsonlRecordReader,
        safety_policy: ArtifactSafetyPolicy,
    ) -> None:
        self.provider = provider
        self._record_reader = record_reader
        self._safety_policy = safety_policy

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
        return self._safety_policy.safe_reason(path, header, now)

    @property
    def wait_description(self) -> str:
        return self._safety_policy.wait_description


class _CodexAdapter(SourceAdapter):
    def _artifact_roots(self, configured_root: Path) -> tuple[Path, ...]:
        return (
            configured_root / DiscoveryPathSegment.SESSIONS,
            configured_root / DiscoveryPathSegment.ARCHIVED_SESSIONS,
        )

    def accepts_relative_path(self, relative_path: Path) -> bool:
        return relative_path.name.startswith("rollout-") and (
            relative_path.suffix == ArtifactFileSuffix.JSONL
        )

    def inspect(self, path: Path) -> _InspectedHeader | None:
        records = self._record_reader.read(path)
        for record in records:
            if record.get(ArtifactRecordKey.TYPE) != ArtifactRecordType.SESSION_META:
                continue
            payload = record.get(ArtifactRecordKey.PAYLOAD)
            if not isinstance(payload, dict):
                return None
            return _header(
                payload.get(ArtifactRecordKey.ID),
                payload.get(ArtifactRecordKey.TIMESTAMP)
                or record.get(ArtifactRecordKey.TIMESTAMP),
            )
        return None


class _ClaudeCodeAdapter(SourceAdapter):
    def _artifact_roots(self, configured_root: Path) -> tuple[Path, ...]:
        return (configured_root / DiscoveryPathSegment.PROJECTS,)

    def accepts_relative_path(self, relative_path: Path) -> bool:
        return len(relative_path.parts) == 2 and relative_path.suffix == ArtifactFileSuffix.JSONL

    def inspect(self, path: Path) -> _InspectedHeader | None:
        message_types = {
            ArtifactRecordType.SYSTEM,
            ArtifactRecordType.USER,
            ArtifactRecordType.ASSISTANT,
        }
        for record in self._record_reader.read(path):
            if record.get(ArtifactRecordKey.TYPE) in message_types:
                header = _header(
                    record.get(ArtifactRecordKey.SESSION_ID),
                    record.get(ArtifactRecordKey.TIMESTAMP),
                )
                if header is not None:
                    return header
        return None


class _CopilotCliAdapter(SourceAdapter):
    def _artifact_roots(self, configured_root: Path) -> tuple[Path, ...]:
        return (configured_root / DiscoveryPathSegment.SESSION_STATE,)

    def accepts_relative_path(self, relative_path: Path) -> bool:
        return len(relative_path.parts) == 2 and (
            relative_path.name == ArtifactFileName.COPILOT_EVENTS
        )

    def inspect(self, path: Path) -> _InspectedHeader | None:
        records = self._record_reader.read(path)
        terminal = any(
            record.get(ArtifactRecordKey.TYPE) == ArtifactRecordType.SESSION_SHUTDOWN
            for record in records
        )
        for record in records:
            if record.get(ArtifactRecordKey.TYPE) != ArtifactRecordType.SESSION_START:
                continue
            data = record.get(ArtifactRecordKey.DATA)
            if not isinstance(data, dict):
                return None
            header = _header(
                data.get(ArtifactRecordKey.SESSION_ID),
                data.get(ArtifactRecordKey.START_TIME)
                or record.get(ArtifactRecordKey.TIMESTAMP),
            )
            if header is not None:
                return _InspectedHeader(header.session_id, header.timestamp, terminal)
        return None


class _PiFamilyAdapter(SourceAdapter):
    def __init__(
        self,
        provider: DiscoveryProvider,
        record_reader: JsonlRecordReader,
        safety_policy: ArtifactSafetyPolicy,
        *,
        oh_my_pi: bool,
    ) -> None:
        super().__init__(provider, record_reader, safety_policy)
        self._oh_my_pi = oh_my_pi

    def _artifact_roots(self, configured_root: Path) -> tuple[Path, ...]:
        return (configured_root / DiscoveryPathSegment.SESSIONS,)

    def accepts_relative_path(self, relative_path: Path) -> bool:
        return relative_path.suffix == ArtifactFileSuffix.JSONL

    def inspect(self, path: Path) -> _InspectedHeader | None:
        records = self._record_reader.read(path)
        session_index = next(
            (
                index
                for index, record in enumerate(records)
                if record.get(ArtifactRecordKey.TYPE) == ArtifactRecordType.SESSION
            ),
            None,
        )
        if session_index is None:
            return None
        session = records[session_index]
        looks_like_omp = (
            session_index > 0
            and records[0].get(ArtifactRecordKey.TYPE) == ArtifactRecordType.TITLE
        )
        looks_like_omp = looks_like_omp or ArtifactRecordKey.TITLE_SOURCE in session
        if looks_like_omp != self._oh_my_pi:
            return None
        return _header(
            session.get(ArtifactRecordKey.ID), session.get(ArtifactRecordKey.TIMESTAMP)
        )


class ImportDiscoveryService:
    def __init__(
        self,
        adapters: Mapping[DiscoverySource, SourceAdapter],
        file_scanner: CandidateFileScanner,
        *,
        environment: Mapping[str, str] | None = None,
        home: Path | None = None,
    ) -> None:
        self._adapters = dict(adapters)
        self._file_scanner = file_scanner
        self._environment = os.environ if environment is None else environment
        self._home = Path.home() if home is None else home

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted(source.value for source in self._adapters))

    def discover_latest(self, source: str, *, now: datetime | None = None) -> DiscoveredArtifact:
        try:
            export_source = ExportOnlySource(source)
        except ValueError:
            export_source = None
        if export_source is not None:
            raise DiscoveryError(
                f"{source} requires a supported export: {EXPORT_INSTRUCTIONS[export_source]}"
            )
        try:
            discovery_source = DiscoverySource(source)
        except ValueError:
            discovery_source = None
        adapter = self._adapters.get(discovery_source) if discovery_source else None
        if adapter is None:
            export_sources = sorted(item.value for item in EXPORT_INSTRUCTIONS)
            supported = ", ".join((*self.sources, *export_sources))
            raise DiscoveryError(f"unknown discovery source: {source}; supported sources: {supported}")

        checked_roots: list[Path] = []
        candidates: list[DiscoveredArtifact] = []
        unsafe_latest: list[tuple[datetime, Path]] = []
        current_time = now or datetime.now(timezone.utc)
        for root in adapter.roots(self._environment, self._home):
            checked_roots.append(root)
            for path in self._file_scanner.files(root):
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
                        source=adapter.provider.source,
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
                f"close the client and wait at least {adapter.wait_description} before retrying"
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
    record_reader = BoundedJsonlRecordReader()
    safety_policy = ConservativeArtifactSafetyPolicy()
    adapters: dict[DiscoverySource, SourceAdapter] = {
        DiscoverySource.CODEX: _CodexAdapter(
            providers[DiscoverySource.CODEX], record_reader, CodexArtifactSafetyPolicy()
        ),
        DiscoverySource.CLAUDE_CODE: _ClaudeCodeAdapter(
            providers[DiscoverySource.CLAUDE_CODE], record_reader, safety_policy
        ),
        DiscoverySource.COPILOT_CLI: _CopilotCliAdapter(
            providers[DiscoverySource.COPILOT_CLI], record_reader, safety_policy
        ),
        DiscoverySource.PI: _PiFamilyAdapter(
            providers[DiscoverySource.PI], record_reader, safety_policy, oh_my_pi=False
        ),
        DiscoverySource.OH_MY_PI: _PiFamilyAdapter(
            providers[DiscoverySource.OH_MY_PI], record_reader, safety_policy, oh_my_pi=True
        ),
    }
    return ImportDiscoveryService(
        adapters, SafeFileScanner(), environment=environment, home=home
    )


def discovery_source_names() -> tuple[str, ...]:
    values = (
        *(provider.source.value for provider in DISCOVERY_PROVIDERS),
        *(source.value for source in EXPORT_INSTRUCTIONS),
    )
    return tuple(sorted(values))


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
