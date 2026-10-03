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
    / "qwen-code-session-export.schema.json"
)


def _load_record_validators() -> tuple[Any, Any, Any, Any]:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as schema_handle:
        schema = json.load(schema_handle)
    jsonschema.Draft202012Validator.check_schema(schema)
    definitions = schema["$defs"]
    return (
        jsonschema.Draft202012Validator(definitions["sessionDocument"]),
        jsonschema.Draft202012Validator(definitions["sessionMetadata"]),
        jsonschema.Draft202012Validator(definitions["messageCandidate"]),
        jsonschema.Draft202012Validator(definitions["importableMessage"]),
    )


(
    _DOCUMENT_VALIDATOR,
    _METADATA_VALIDATOR,
    _MESSAGE_CANDIDATE_VALIDATOR,
    _MESSAGE_VALIDATOR,
) = _load_record_validators()


class _SessionDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sessionId: str
    startTime: str
    messages: list[Any]
    metadata: dict[str, Any] | None = None


class _SessionMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    type: str
    sessionId: str
    startTime: str
    exportTime: str | None = None
    cwd: str | None = None
    gitRepo: str | None = None
    gitBranch: str | None = None
    model: str | None = None
    channel: str | None = None


class _MessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str | None = None
    content: str | None = None
    parts: list[Any] | None = None


class _ExportMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    uuid: str
    parentUuid: str | None = None
    sessionId: str | None = None
    timestamp: str
    type: str
    message: _MessageEnvelope
    model: str | None = None


class QwenCodeSessionExportImporter(ConversationImporter):
    """Parse native Qwen Code JSON and JSONL session exports."""

    name = "qwen-code-session-export"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("Qwen Code session input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("Qwen Code session input exceeds 20000000 bytes")

        document = _json_document(text)
        if document is not None:
            return [_import_json_document(document, source=source, title=title)]
        return [_import_jsonl(text, source=source, title=title)]


def _json_document(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        raise ValueError("Qwen Code session JSON must be an object")
    if "messages" in value or (
        "sessionId" in value and "startTime" in value and "type" not in value
    ):
        return value
    return None


def _import_json_document(
    document: dict[str, Any], *, source: str | None, title: str | None
) -> dict[str, Any]:
    error = next(_DOCUMENT_VALIDATOR.iter_errors(document), None)
    if error is not None:
        raise _schema_error("JSON document", None, error)
    try:
        parsed = _SessionDocument.model_validate(document)
    except ValidationError as exc:
        raise ValueError("Qwen Code session JSON document is invalid") from exc

    metadata = parsed.metadata or {}
    provenance: dict[str, Any] = dict(metadata)
    provenance["sessionId"] = parsed.sessionId
    provenance["startTime"] = parsed.startTime
    records = _numbered_records(parsed.messages, label="message")
    return _payload(
        records,
        provenance,
        export_format="json",
        source=source,
        title=title,
    )


def _import_jsonl(
    text: str, *, source: str | None, title: str | None
) -> dict[str, Any]:
    records: list[tuple[dict[str, Any], str]] = []
    metadata: dict[str, Any] = {}
    saw_metadata = False
    saw_non_metadata = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Qwen Code session line {line_number} must be valid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(f"Qwen Code session line {line_number} must be an object")
        if record.get("type") == "session_metadata":
            if saw_metadata:
                raise ValueError("Qwen Code session metadata is duplicated")
            if saw_non_metadata:
                raise ValueError("Qwen Code session metadata must be the first record")
            parsed_metadata = _parse_metadata(record, line_number=line_number)
            metadata = parsed_metadata.model_dump(exclude_none=True)
            metadata.pop("type", None)
            saw_metadata = True
            continue
        saw_non_metadata = True
        records.append((record, f"line {line_number}"))

    if not records and not saw_metadata:
        raise ValueError("Qwen Code session is empty")
    return _payload(
        records,
        metadata,
        export_format="jsonl",
        source=source,
        title=title,
    )


def _parse_metadata(record: dict[str, Any], *, line_number: int) -> _SessionMetadata:
    error = next(_METADATA_VALIDATOR.iter_errors(record), None)
    if error is not None:
        raise _schema_error("metadata", f"line {line_number}", error)
    try:
        return _SessionMetadata.model_validate(record)
    except ValidationError as exc:
        raise ValueError(
            f"Qwen Code session metadata on line {line_number} is invalid"
        ) from exc


def _numbered_records(
    values: list[Any], *, label: str
) -> list[tuple[dict[str, Any], str]]:
    records: list[tuple[dict[str, Any], str]] = []
    for index, value in enumerate(values, start=1):
        if not isinstance(value, dict):
            raise ValueError(f"Qwen Code session {label} {index} must be an object")
        records.append((value, f"{label} {index}"))
    return records


def _payload(
    records: list[tuple[dict[str, Any], str]],
    provenance: dict[str, Any],
    *,
    export_format: str,
    source: str | None,
    title: str | None,
) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    message_times: list[datetime] = []
    seen_uuids: set[str] = set()
    model: str | None = _nonempty_string(provenance.get("model"))
    for record, location in records:
        parsed = _parse_message(record, location=location)
        if parsed is None or parsed.uuid in seen_uuids:
            continue
        seen_uuids.add(parsed.uuid)
        content = _message_text(parsed.message, location=location)
        if content is None:
            continue
        messages.append({"role": parsed.type, "text": content})
        timestamp = _timestamp(parsed.timestamp, field=f"{location} timestamp")
        if timestamp is not None:
            message_times.append(timestamp)
        if model is None and parsed.type == "assistant":
            model = _nonempty_string(parsed.model)

    if not messages:
        raise ValueError("Qwen Code session contains no conversational text")

    metadata: dict[str, Any] = {
        "importer": QwenCodeSessionExportImporter.name,
        "platform": "qwen-code",
        "ingestion_method": f"{export_format}-import",
        "export_format": export_format,
    }
    mappings = (
        ("sessionId", "source_session_id"),
        ("cwd", "directory"),
        ("gitRepo", "git_repository"),
        ("gitBranch", "git_branch"),
        ("channel", "channel"),
    )
    for source_key, target_key in mappings:
        _copy_nonempty(provenance, source_key, metadata, target_key)
    if model is not None:
        metadata["model"] = model
    if title:
        metadata["title"] = title

    payload: dict[str, Any] = {
        "source": source or "qwen-code",
        "messages": messages,
        "metadata": metadata,
    }
    if title:
        payload["title"] = title
    session_time = _timestamp(provenance.get("startTime"), field="start time")
    timestamp = session_time or (message_times[0] if message_times else None)
    if timestamp is not None:
        payload["timestamp"] = timestamp.isoformat()
    return payload


def _parse_message(
    record: dict[str, Any], *, location: str
) -> _ExportMessage | None:
    if not _MESSAGE_CANDIDATE_VALIDATOR.is_valid(record):
        return None
    error = next(_MESSAGE_VALIDATOR.iter_errors(record), None)
    if error is not None:
        raise _schema_error("message", location, error)
    try:
        return _ExportMessage.model_validate(record)
    except ValidationError as exc:
        raise ValueError(f"Qwen Code session {location} is invalid") from exc


def _message_text(message: _MessageEnvelope, *, location: str) -> str | None:
    content = _nonempty_string(message.content)
    if content is not None:
        return content
    if message.parts is None:
        return None
    parts: list[str] = []
    for part in message.parts:
        if not isinstance(part, dict):
            raise ValueError(f"Qwen Code session {location} has an invalid text part")
        text = part.get("text")
        if not isinstance(text, str):
            raise ValueError(
                f"Qwen Code session {location} has a non-text text part"
            )
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts) if parts else None


def _schema_error(
    record_name: str, location: str | None, error: jsonschema.ValidationError
) -> ValueError:
    path = ".".join(str(part) for part in error.absolute_path)
    field = f" at {path}" if path else ""
    where = f" {location}" if location is not None else ""
    return ValueError(
        f"Qwen Code session {record_name}{where} does not match the importer schema"
        f"{field}: {error.message}"
    )


def _timestamp(value: Any, *, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Qwen Code session {field} is invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Qwen Code session {field} is invalid") from exc


def _copy_nonempty(
    source: dict[str, Any], source_key: str, target: dict[str, Any], target_key: str
) -> None:
    value = _nonempty_string(source.get(source_key))
    if value is not None:
        target[target_key] = value


def _nonempty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
