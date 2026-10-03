from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema  # pyright: ignore[reportMissingModuleSource]
from pydantic import BaseModel, ConfigDict, ValidationError

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schema"
    / "deepseek-harness-session.schema.json"
)
_INTERNAL_USER_SOURCE_KINDS = {
    "compact-checkpoint",
    "plugin",
    "runtime-context",
    "subagent-settled",
    "system-prompt",
}


def _load_record_validators() -> tuple[Any, Any, Any, Any, Any]:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as schema_handle:
        schema = json.load(schema_handle)
    jsonschema.Draft202012Validator.check_schema(schema)
    definitions = schema["$defs"]
    return tuple(
        jsonschema.Draft202012Validator(
            {"$ref": f"#/$defs/{name}", "$defs": definitions}
        )
        for name in (
            "sessionHeader",
            "eventBase",
            "messageCandidate",
            "importableMessage",
            "titleEvent",
        )
    )  # type: ignore[return-value]


(
    _HEADER_VALIDATOR,
    _EVENT_VALIDATOR,
    _MESSAGE_CANDIDATE_VALIDATOR,
    _MESSAGE_VALIDATOR,
    _TITLE_VALIDATOR,
) = _load_record_validators()


class _SessionHeader(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    version: int
    id: str
    createdAt: int
    cwd: str | None = None
    parentSession: str | None = None
    origin: str | None = None
    delegationDepth: int | None = None
    agentPreset: str | None = None


class DeepSeekHarnessSessionJsonlImporter(ConversationImporter):
    """Parse an extracted DeepSeek Harness canonical session log."""

    name = "deepseek-harness-session-jsonl"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("DeepSeek Harness session input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("DeepSeek Harness session input exceeds 20000000 bytes")

        records = _parse_records(text)
        header = _parse_header(records[0])
        events = records[1:]
        _validate_event_order(events)

        messages: list[dict[str, str]] = []
        seen_message_ids: set[str] = set()
        detected_title: str | None = None
        model: str | None = None

        for record, line_number in events:
            if record.get("type") == "session":
                raise ValueError(
                    f"DeepSeek Harness session line {line_number} contains a duplicate header"
                )
            if record.get("type") == "session/title":
                _validate_record(_TITLE_VALIDATOR, record, "title event", line_number)
                detected_title = _nonempty_string(record["data"].get("title"))
                continue
            model = model or _capture_model(record)
            parsed = _parse_message(record, line_number=line_number)
            if parsed is None:
                continue
            role, content, message_id, source_kind = parsed
            if role == "user" and source_kind in _INTERNAL_USER_SOURCE_KINDS:
                continue
            message_text = _message_text(content, line_number=line_number)
            if message_text is None:
                continue
            if message_id is not None and message_id in seen_message_ids:
                continue
            if message_id is not None:
                seen_message_ids.add(message_id)
            messages.append({"role": role, "text": message_text})

        if not messages:
            raise ValueError("DeepSeek Harness session contains no conversational text")

        resolved_title = title or detected_title
        metadata: dict[str, Any] = {
            "importer": self.name,
            "platform": "deepseek-harness",
            "ingestion_method": "jsonl-import",
            "source_session_id": header.id,
            "session_format_version": header.version,
            "session_relationship": (
                "descendant" if header.parentSession is not None else "root"
            ),
        }
        _copy_nonempty(header.cwd, metadata, "directory")
        _copy_nonempty(header.parentSession, metadata, "parent_session_id")
        _copy_nonempty(header.origin, metadata, "session_origin")
        _copy_nonempty(header.agentPreset, metadata, "agent_preset")
        if header.delegationDepth is not None:
            metadata["delegation_depth"] = header.delegationDepth
        if model is not None:
            metadata["model"] = model
        if resolved_title is not None:
            metadata["title"] = resolved_title

        payload: dict[str, Any] = {
            "source": source or "deepseek-harness",
            "messages": messages,
            "metadata": metadata,
            "timestamp": _millisecond_timestamp(header.createdAt).isoformat(),
        }
        if resolved_title is not None:
            payload["title"] = resolved_title
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
                f"DeepSeek Harness session line {line_number} must be valid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(
                f"DeepSeek Harness session line {line_number} must be an object"
            )
        records.append((record, line_number))
    if not records:
        raise ValueError("DeepSeek Harness session is empty")
    return records


def _parse_header(record_with_line: tuple[dict[str, Any], int]) -> _SessionHeader:
    record, line_number = record_with_line
    if record.get("type") != "session":
        raise ValueError(
            f"DeepSeek Harness session line {line_number} must be the session header"
        )
    version = record.get("version")
    if isinstance(version, int) and version not in {0, 1, 2, 3, 4}:
        raise ValueError(
            f"DeepSeek Harness session version {version} is not supported"
        )
    _validate_record(_HEADER_VALIDATOR, record, "header", line_number)
    try:
        return _SessionHeader.model_validate(record)
    except ValidationError as exc:
        raise ValueError("DeepSeek Harness session header is invalid") from exc


def _validate_event_order(events: list[tuple[dict[str, Any], int]]) -> None:
    coordinate_mode: bool | None = None
    expected_seq = 0
    for record, line_number in events:
        _validate_record(_EVENT_VALIDATOR, record, "event", line_number)
        has_coordinates = "seq" in record
        if coordinate_mode is None:
            coordinate_mode = has_coordinates
        elif coordinate_mode != has_coordinates:
            raise ValueError(
                f"DeepSeek Harness session line {line_number} mixes coordinated and physical-order events"
            )
        if not has_coordinates:
            continue
        seq = record["seq"]
        if seq != expected_seq:
            raise ValueError(
                f"DeepSeek Harness session line {line_number} has non-monotonic sequence; "
                f"expected {expected_seq}, got {seq}"
            )
        expected_seq += 1


def _parse_message(
    record: dict[str, Any], *, line_number: int
) -> tuple[str, Any, str | None, str | None] | None:
    if not _MESSAGE_CANDIDATE_VALIDATOR.is_valid(record):
        return None
    _validate_record(_MESSAGE_VALIDATOR, record, "message", line_number)
    data = record["data"]
    envelope = data.get("message") if isinstance(data.get("message"), dict) else data
    role = "user" if record["type"] == "user/message" else "assistant"
    envelope_role = envelope.get("role")
    if envelope_role is not None and envelope_role != role:
        raise ValueError(
            f"DeepSeek Harness message on line {line_number} has mismatched role"
        )
    message_id = _nonempty_string(envelope.get("id")) or _nonempty_string(
        record.get("id")
    )
    source = envelope.get("source")
    source_kind = (
        _nonempty_string(source.get("kind")) if isinstance(source, dict) else None
    )
    return role, envelope["content"], message_id, source_kind


def _message_text(content: Any, *, line_number: int) -> str | None:
    if isinstance(content, str):
        return content if content.strip() else None
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError(
                f"DeepSeek Harness message on line {line_number} has a non-text text block"
            )
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts) if parts else None


def _capture_model(record: dict[str, Any]) -> str | None:
    data = record.get("data")
    if not isinstance(data, dict):
        return None
    if record.get("type") in {"request/context", "request/header"}:
        candidate = data
        header = data.get("header")
        if isinstance(header, dict) and isinstance(header.get("config"), dict):
            candidate = header["config"]
        model = _nonempty_string(candidate.get("model"))
        provider = _nonempty_string(candidate.get("provider"))
        if model is not None:
            return f"{provider}/{model}" if provider else model
    if record.get("type") == "assistant/message":
        message = data.get("message")
        if isinstance(message, dict) and isinstance(message.get("source"), dict):
            source = message["source"]
            model = _nonempty_string(source.get("model"))
            provider = _nonempty_string(source.get("provider"))
            if model is not None:
                return f"{provider}/{model}" if provider else model
    return None


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
        f"DeepSeek Harness {record_name} on line {line_number} does not match "
        f"the importer schema{location}: {error.message}"
    )


def _millisecond_timestamp(value: int) -> datetime:
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError("DeepSeek Harness session createdAt is invalid") from exc


def _copy_nonempty(value: Any, target: dict[str, Any], key: str) -> None:
    parsed = _nonempty_string(value)
    if parsed is not None:
        target[key] = parsed


def _nonempty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
