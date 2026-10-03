from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema  # pyright: ignore[reportMissingModuleSource]
from pydantic import BaseModel, ConfigDict, ValidationError

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "pi-session.schema.json"
_ROLES = {"user", "assistant"}
_VARIANTS = {"pi", "oh-my-pi", "openclaw"}
_OMP_HEADER_FIELDS = {
    "additionalDirectories",
    "providerPromptCacheKey",
    "titleSource",
}
_OMP_ENTRY_TYPES = {
    "credential_pin",
    "mode_change",
    "model_usage",
    "reset_boundary",
    "service_tier_change",
    "session_init",
    "title_change",
    "ttsr_injection",
}


def _load_record_validators() -> tuple[Any, Any, Any, Any, Any]:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as schema_handle:
        schema = json.load(schema_handle)
    jsonschema.Draft202012Validator.check_schema(schema)
    definitions = schema["$defs"]
    return (
        jsonschema.Draft202012Validator(definitions["titleRecord"]),
        jsonschema.Draft202012Validator(definitions["sessionHeader"]),
        jsonschema.Draft202012Validator(definitions["entryBase"]),
        jsonschema.Draft202012Validator(definitions["messageCandidate"]),
        jsonschema.Draft202012Validator(definitions["importableMessage"]),
    )


(
    _TITLE_VALIDATOR,
    _HEADER_VALIDATOR,
    _ENTRY_VALIDATOR,
    _MESSAGE_CANDIDATE_VALIDATOR,
    _MESSAGE_VALIDATOR,
) = _load_record_validators()


class _TitleRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    title: str


class _SessionHeader(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    version: int
    id: str
    timestamp: str
    cwd: str
    title: str | None = None


class _EntryBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    id: str
    parentId: str | None
    timestamp: str


class _MessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    role: str
    content: str | list[Any]
    provider: str | None = None
    model: str | None = None


class _MessageEntry(_EntryBase):
    message: _MessageEnvelope


class PiSessionJsonlImporter(ConversationImporter):
    """Parse Pi-family session-tree JSONL from Pi, Oh My Pi, or OpenClaw."""

    name = "pi-session-jsonl"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("Pi-family session input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("Pi-family session input exceeds 20000000 bytes")

        records = _parse_records(text)
        header, entries, physical_title = _split_header(records)
        parsed_header = _validate_header(header)
        entry_by_id = _validate_graph(entries)
        active_path = _active_path(entries, entry_by_id)

        messages: list[dict[str, str]] = []
        message_times: list[datetime] = []
        model: str | None = None
        for record, line_number in active_path:
            parsed_message = _parse_message_record(record, line_number=line_number)
            if parsed_message is None:
                continue
            role = parsed_message.message.role
            if role not in _ROLES:
                continue
            message_text = _message_text(
                parsed_message.message.content, line_number=line_number
            )
            if message_text is None:
                continue
            messages.append({"role": role, "text": message_text})
            timestamp = _timestamp(parsed_message.timestamp, line_number=line_number)
            if timestamp is not None:
                message_times.append(timestamp)
            if model is None and role == "assistant":
                model = _model(parsed_message.message)

        if not messages:
            raise ValueError("Pi-family session contains no conversational text")

        variant = _detect_variant(header, entries, source=source)
        metadata: dict[str, Any] = {
            "importer": self.name,
            "platform": variant,
            "detected_variant": variant,
            "ingestion_method": "jsonl-import",
        }
        _copy_nonempty(header, "id", metadata, "source_session_id")
        _copy_nonempty(header, "cwd", metadata, "directory")
        _copy_git_metadata(header, metadata)
        if model is not None:
            metadata["model"] = model

        resolved_title = title or parsed_header.title or physical_title
        if resolved_title is not None:
            metadata["title"] = resolved_title

        payload: dict[str, Any] = {
            "source": source or variant,
            "messages": messages,
            "metadata": metadata,
        }
        if resolved_title is not None:
            payload["title"] = resolved_title
        session_time = _timestamp(parsed_header.timestamp, field="session timestamp")
        timestamp = session_time or (message_times[0] if message_times else None)
        if timestamp is not None:
            payload["timestamp"] = timestamp.isoformat()
        return [payload]


def _parse_records(text: str) -> list[tuple[dict[str, Any], int]]:
    records: list[tuple[dict[str, Any], int]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Pi-family session line {line_number} must be valid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(f"Pi-family session line {line_number} must be an object")
        records.append((record, line_number))
    if not records:
        raise ValueError("Pi-family session is empty")
    return records


def _split_header(
    records: list[tuple[dict[str, Any], int]],
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], int]], str | None]:
    physical_title: str | None = None
    first_record, first_line = records[0]
    if first_record.get("type") == "title":
        error = next(_TITLE_VALIDATOR.iter_errors(first_record), None)
        if error is not None:
            raise _schema_error("title record", first_line, error)
        try:
            physical_title = _TitleRecord.model_validate(first_record).title
        except ValidationError as exc:
            raise ValueError(
                f"Pi-family title record on line {first_line} is invalid"
            ) from exc
        records = records[1:]
        if not records:
            raise ValueError("Pi-family session has no session header")
        first_record, first_line = records[0]
    if first_record.get("type") != "session":
        raise ValueError(
            f"Pi-family session line {first_line} must be the session header"
        )
    return first_record, records[1:], physical_title


def _validate_header(header: dict[str, Any]) -> _SessionHeader:
    version = header.get("version")
    if version != 3:
        raise ValueError("Pi-family session header must use version 3")
    error = next(_HEADER_VALIDATOR.iter_errors(header), None)
    if error is not None:
        raise _schema_error("session header", None, error)
    try:
        return _SessionHeader.model_validate(header)
    except ValidationError as exc:
        raise ValueError("Pi-family session header is invalid") from exc


def _validate_graph(
    entries: list[tuple[dict[str, Any], int]],
) -> dict[str, tuple[dict[str, Any], int]]:
    entry_by_id: dict[str, tuple[dict[str, Any], int]] = {}
    for record, line_number in entries:
        error = next(_ENTRY_VALIDATOR.iter_errors(record), None)
        if error is not None:
            raise _schema_error("entry", line_number, error)
        try:
            parsed_entry = _EntryBase.model_validate(record)
        except ValidationError as exc:
            raise ValueError(
                f"Pi-family entry on line {line_number} is invalid"
            ) from exc
        entry_id = parsed_entry.id
        if entry_id in entry_by_id:
            raise ValueError(f"Pi-family entry id {entry_id!r} is duplicated")
        entry_by_id[entry_id] = (record, line_number)

    for entry_id, (record, _) in entry_by_id.items():
        parent_id = record.get("parentId")
        if parent_id is not None and parent_id not in entry_by_id:
            raise ValueError(
                f"Pi-family entry {entry_id!r} references missing parent {parent_id!r}"
            )

    complete: set[str] = set()
    for entry_id in entry_by_id:
        path: set[str] = set()
        current_id: str | None = entry_id
        while current_id is not None and current_id not in complete:
            if current_id in path:
                raise ValueError("Pi-family session entry graph contains a cycle")
            path.add(current_id)
            current = entry_by_id[current_id][0]
            parent = current.get("parentId")
            current_id = str(parent) if parent is not None else None
        complete.update(path)
    return entry_by_id


def _parse_message_record(
    record: dict[str, Any], *, line_number: int
) -> _MessageEntry | None:
    if not _MESSAGE_CANDIDATE_VALIDATOR.is_valid(record):
        return None
    error = next(_MESSAGE_VALIDATOR.iter_errors(record), None)
    if error is not None:
        raise _schema_error("message", line_number, error)
    try:
        return _MessageEntry.model_validate(record)
    except ValidationError as exc:
        raise ValueError(
            f"Pi-family message on line {line_number} is invalid"
        ) from exc


def _active_path(
    entries: list[tuple[dict[str, Any], int]],
    entry_by_id: dict[str, tuple[dict[str, Any], int]],
) -> list[tuple[dict[str, Any], int]]:
    if not entries:
        raise ValueError("Pi-family session contains no entries")
    current_id = str(entries[-1][0]["id"])
    reversed_path: list[tuple[dict[str, Any], int]] = []
    while True:
        current = entry_by_id[current_id]
        reversed_path.append(current)
        parent_id = current[0].get("parentId")
        if parent_id is None:
            break
        current_id = str(parent_id)
    reversed_path.reverse()
    return reversed_path


def _message_text(content: Any, *, line_number: int) -> str | None:
    if isinstance(content, str):
        return content if content.strip() else None
    if not isinstance(content, list):
        raise ValueError(
            f"Pi-family message on line {line_number} has invalid content"
        )
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError(
                f"Pi-family message on line {line_number} has a non-text text block"
            )
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts) if parts else None


def _detect_variant(
    header: dict[str, Any],
    entries: list[tuple[dict[str, Any], int]],
    *,
    source: str | None,
) -> str:
    if source in _VARIANTS:
        return source
    if any(_looks_like_openclaw(record) for record, _ in entries):
        return "openclaw"
    if _OMP_HEADER_FIELDS.intersection(header) or any(
        record.get("type") in _OMP_ENTRY_TYPES for record, _ in entries
    ):
        return "oh-my-pi"
    return "pi"


def _looks_like_openclaw(record: dict[str, Any]) -> bool:
    custom_type = _nonempty_string(record.get("customType"))
    if custom_type is not None and "openclaw" in custom_type.lower():
        return True
    message = record.get("message")
    if not isinstance(message, dict):
        return False
    for field in ("provider", "source", "client"):
        value = _nonempty_string(message.get(field))
        if value is not None and "openclaw" in value.lower():
            return True
    return False


def _model(envelope: _MessageEnvelope) -> str | None:
    model = _nonempty_string(envelope.model)
    if model is None:
        return None
    provider = _nonempty_string(envelope.provider)
    return f"{provider}/{model}" if provider else model


def _schema_error(
    record_name: str, line_number: int | None, error: jsonschema.ValidationError
) -> ValueError:
    path = ".".join(str(part) for part in error.absolute_path)
    location = f" at {path}" if path else ""
    line = f" on line {line_number}" if line_number is not None else ""
    return ValueError(
        f"Pi-family {record_name}{line} does not match the importer schema"
        f"{location}: {error.message}"
    )


def _copy_git_metadata(header: dict[str, Any], target: dict[str, Any]) -> None:
    git = header.get("git")
    if not isinstance(git, dict):
        return
    _copy_nonempty(git, "branch", target, "git_branch")
    _copy_nonempty(git, "commit", target, "git_commit")
    _copy_nonempty(git, "commitHash", target, "git_commit")


def _copy_nonempty(
    source: dict[str, Any], source_key: str, target: dict[str, Any], target_key: str
) -> None:
    value = _nonempty_string(source.get(source_key))
    if value is not None and target_key not in target:
        target[target_key] = value


def _timestamp(
    value: Any,
    *,
    line_number: int | None = None,
    field: str | None = None,
) -> datetime | None:
    if value is None:
        return None
    label = field or f"timestamp on line {line_number}"
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Pi-family session {label} is invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Pi-family session {label} is invalid") from exc


def _nonempty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
