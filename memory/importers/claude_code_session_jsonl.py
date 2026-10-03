from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import jsonschema  # pyright: ignore[reportMissingModuleSource]

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "schema" / "claude-code-session.schema.json"
)


def _load_record_validators() -> tuple[Any, Any]:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as schema_handle:
        schema = json.load(schema_handle)
    jsonschema.Draft202012Validator.check_schema(schema)
    definitions = schema["$defs"]
    return (
        jsonschema.Draft202012Validator(definitions["messageCandidate"]),
        jsonschema.Draft202012Validator(definitions["importableMessage"]),
    )


_MESSAGE_CANDIDATE_VALIDATOR, _IMPORTABLE_MESSAGE_VALIDATOR = (
    _load_record_validators()
)


@dataclass(frozen=True, slots=True)
class _ParsedMessage:
    role: str
    content: str | list[Any]
    uuid: str | None
    timestamp: str | None
    model: str | None


class ClaudeCodeSessionJsonlImporter(ConversationImporter):
    """Parse a persisted Claude Code session transcript."""

    name = "claude-code-session-jsonl"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("Claude Code session input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("Claude Code session input exceeds 20000000 bytes")

        messages: list[dict[str, str]] = []
        message_times: list[datetime] = []
        seen_uuids: set[str] = set()
        provenance: dict[str, str] = {}

        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Claude Code session line {line_number} must be valid JSON"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(
                    f"Claude Code session line {line_number} must be an object"
                )

            _capture_provenance(record, provenance)
            parsed_message = _parse_message_record(record, line_number=line_number)
            if parsed_message is None:
                continue
            content = _message_text(parsed_message.content)
            if content is None:
                continue
            if _is_duplicate(parsed_message.uuid, seen_uuids):
                continue

            messages.append({"role": parsed_message.role, "text": content})
            timestamp = _timestamp(parsed_message.timestamp, line_number=line_number)
            if timestamp is not None:
                message_times.append(timestamp)
            _capture_model(parsed_message.model, provenance)

        if not messages:
            raise ValueError("Claude Code session contains no conversational text")

        metadata: dict[str, Any] = {
            "importer": self.name,
            "platform": "claude-code",
            "ingestion_method": "jsonl-import",
        }
        metadata.update(provenance)
        if title:
            metadata["title"] = title

        payload: dict[str, Any] = {
            "source": source or "claude-code",
            "messages": messages,
            "metadata": metadata,
        }
        if title:
            payload["title"] = title
        if message_times:
            payload["timestamp"] = message_times[0].isoformat()
        return [payload]


def _parse_message_record(
    record: dict[str, Any], *, line_number: int
) -> _ParsedMessage | None:
    if not _MESSAGE_CANDIDATE_VALIDATOR.is_valid(record):
        return None
    error = next(_IMPORTABLE_MESSAGE_VALIDATOR.iter_errors(record), None)
    if error is not None:
        path = ".".join(str(part) for part in error.absolute_path)
        location = f" at {path}" if path else ""
        raise ValueError(
            f"Claude Code message on line {line_number} does not match the importer schema"
            f"{location}: {error.message}"
        )
    message = cast(dict[str, Any], record["message"])
    return _ParsedMessage(
        role=cast(str, record["type"]),
        content=cast(str | list[Any], message["content"]),
        uuid=_nonempty_string(record.get("uuid")),
        timestamp=cast(str | None, record.get("timestamp")),
        model=_nonempty_string(message.get("model")),
    )


def _message_text(content: str | list[Any]) -> str | None:
    if isinstance(content, str):
        return content if content.strip() else None

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = cast(str, block["text"])
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts) if parts else None


def _is_duplicate(record_uuid: str | None, seen_uuids: set[str]) -> bool:
    if record_uuid is None:
        return False
    if record_uuid in seen_uuids:
        return True
    seen_uuids.add(record_uuid)
    return False


def _capture_model(model: str | None, target: dict[str, str]) -> None:
    if model is not None and "model" not in target:
        target["model"] = model


def _capture_provenance(record: dict[str, Any], target: dict[str, str]) -> None:
    mappings = (
        ("sessionId", "source_session_id"),
        ("cwd", "directory"),
        ("gitBranch", "git_branch"),
        ("model", "model"),
    )
    for source_key, target_key in mappings:
        if target_key in target:
            continue
        value = _nonempty_string(record.get(source_key))
        if value is not None:
            target[target_key] = value


def _timestamp(value: Any, *, line_number: int) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"Claude Code session timestamp on line {line_number} is invalid"
        )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"Claude Code session timestamp on line {line_number} is invalid"
        ) from exc


def _nonempty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
