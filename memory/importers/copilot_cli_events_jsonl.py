from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema  # pyright: ignore[reportMissingModuleSource]
from pydantic import BaseModel, ConfigDict, ValidationError

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schema"
    / "copilot-cli-events.schema.json"
)


def _load_record_validators() -> tuple[Any, Any, Any, Any, Any, Any]:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as schema_handle:
        schema = json.load(schema_handle)
    jsonschema.Draft202012Validator.check_schema(schema)
    definitions = schema["$defs"]
    return tuple(
        jsonschema.Draft202012Validator(definitions[name])
        for name in (
            "messageCandidate",
            "importableMessage",
            "sessionCandidate",
            "sessionMetadata",
            "titleCandidate",
            "titleEvent",
        )
    )  # type: ignore[return-value]


(
    _MESSAGE_CANDIDATE_VALIDATOR,
    _MESSAGE_VALIDATOR,
    _SESSION_CANDIDATE_VALIDATOR,
    _SESSION_VALIDATOR,
    _TITLE_CANDIDATE_VALIDATOR,
    _TITLE_VALIDATOR,
) = _load_record_validators()


class _MessageData(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    content: str
    messageId: str | None = None
    interactionId: str | None = None
    model: str | None = None
    source: str | None = None


class _MessageEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    data: _MessageData
    id: str | None = None
    timestamp: str | None = None
    agentId: str | None = None


class CopilotCliEventsJsonlImporter(ConversationImporter):
    """Parse a persisted GitHub Copilot CLI events.jsonl session."""

    name = "copilot-cli-events-jsonl"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("Copilot CLI session input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("Copilot CLI session input exceeds 20000000 bytes")

        records = _parse_records(text)
        messages: list[dict[str, str]] = []
        message_times: list[datetime] = []
        seen_message_ids: set[str] = set()
        provenance: dict[str, str] = {}
        event_title: str | None = None
        session_time: datetime | None = None

        for record, line_number in records:
            if _SESSION_CANDIDATE_VALIDATOR.is_valid(record):
                _validate_record(
                    _SESSION_VALIDATOR, record, "session event", line_number
                )
                captured_time = _capture_session_metadata(record, provenance)
                if session_time is None:
                    session_time = captured_time
                continue
            if _TITLE_CANDIDATE_VALIDATOR.is_valid(record):
                _validate_record(_TITLE_VALIDATOR, record, "title event", line_number)
                event_title = _nonempty_string(record["data"].get("title"))
                continue

            parsed_message = _parse_message(record, line_number=line_number)
            if parsed_message is None or _is_internal_message(parsed_message):
                continue
            content = parsed_message.data.content
            if not content.strip():
                continue
            message_id = (
                parsed_message.data.messageId
                or parsed_message.id
                or parsed_message.data.interactionId
            )
            if message_id is not None and message_id in seen_message_ids:
                continue
            if message_id is not None:
                seen_message_ids.add(message_id)

            role = "user" if parsed_message.type == "user.message" else "assistant"
            messages.append({"role": role, "text": content})
            timestamp = _timestamp(parsed_message.timestamp, line_number=line_number)
            if timestamp is not None:
                message_times.append(timestamp)
            if role == "assistant":
                _copy_value(parsed_message.data.model, provenance, "model")

        if not messages:
            raise ValueError("Copilot CLI session contains no conversational text")

        resolved_title = title or event_title or provenance.pop("_title", None)
        metadata: dict[str, Any] = {
            "importer": self.name,
            "platform": "copilot-cli",
            "ingestion_method": "jsonl-import",
        }
        metadata.update(provenance)
        if resolved_title is not None:
            metadata["title"] = resolved_title

        payload: dict[str, Any] = {
            "source": source or "copilot-cli",
            "messages": messages,
            "metadata": metadata,
        }
        if resolved_title is not None:
            payload["title"] = resolved_title
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
                f"Copilot CLI session line {line_number} must be valid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(
                f"Copilot CLI session line {line_number} must be an object"
            )
        records.append((record, line_number))
    if not records:
        raise ValueError("Copilot CLI session is empty")
    return records


def _parse_message(
    record: dict[str, Any], *, line_number: int
) -> _MessageEvent | None:
    if not _MESSAGE_CANDIDATE_VALIDATOR.is_valid(record):
        return None
    _validate_record(_MESSAGE_VALIDATOR, record, "message", line_number)
    try:
        return _MessageEvent.model_validate(record)
    except ValidationError as exc:
        raise ValueError(
            f"Copilot CLI message on line {line_number} is invalid"
        ) from exc


def _validate_record(
    validator: Any,
    record: dict[str, Any],
    record_name: str,
    line_number: int,
) -> None:
    error = next(validator.iter_errors(record), None)
    if error is None:
        return
    path = ".".join(str(part) for part in error.absolute_path)
    location = f" at {path}" if path else ""
    raise ValueError(
        f"Copilot CLI {record_name} on line {line_number} does not match the "
        f"importer schema{location}: {error.message}"
    )


def _is_internal_message(message: _MessageEvent) -> bool:
    if _nonempty_string(message.agentId) is not None:
        return True
    if message.type != "user.message":
        return False
    source = _nonempty_string(message.data.source)
    return source is not None and source.lower() != "user"


def _capture_session_metadata(
    record: dict[str, Any], target: dict[str, str]
) -> datetime | None:
    data = record["data"]
    _copy_value(data.get("sessionId"), target, "source_session_id")
    _copy_value(data.get("selectedModel"), target, "model")
    _copy_value(data.get("copilotVersion"), target, "cli_version")
    _copy_value(data.get("title"), target, "_title")
    context = data.get("context")
    if isinstance(context, dict):
        _copy_value(context.get("cwd"), target, "directory")
        _copy_value(context.get("gitRoot"), target, "git_root")
        _copy_value(context.get("repository"), target, "git_repository")
        _copy_value(context.get("branch"), target, "git_branch")
    start_time = data.get("startTime") or record.get("timestamp")
    return _timestamp(start_time, field="session start time")


def _copy_value(value: Any, target: dict[str, str], key: str) -> None:
    parsed = _nonempty_string(value)
    if parsed is not None and key not in target:
        target[key] = parsed


def _timestamp(
    value: Any, *, line_number: int | None = None, field: str | None = None
) -> datetime | None:
    if value is None:
        return None
    label = field or f"timestamp on line {line_number}"
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Copilot CLI session {label} is invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Copilot CLI session {label} is invalid") from exc


def _nonempty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
