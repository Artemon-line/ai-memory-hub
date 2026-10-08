from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema  # pyright: ignore[reportMissingModuleSource]
from pydantic import BaseModel, ConfigDict, ValidationError

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "schema" / "hermes-session-export.schema.json"
)
_ROLES = {"user", "assistant"}


def _load_record_validators() -> tuple[Any, Any, Any]:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as schema_handle:
        schema = json.load(schema_handle)
    jsonschema.Draft202012Validator.check_schema(schema)
    definitions = schema["$defs"]
    return tuple(
        jsonschema.Draft202012Validator(definitions[name])
        for name in ("sessionRecord", "messageCandidate", "importableMessage")
    )  # type: ignore[return-value]


_SESSION_VALIDATOR, _MESSAGE_CANDIDATE_VALIDATOR, _MESSAGE_VALIDATOR = (
    _load_record_validators()
)


class _HermesSession(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    id: str
    messages: list[Any]
    source: str | None = None
    model: str | None = None
    title: str | None = None
    started_at: float | None = None
    cwd: str | None = None
    git_repo_root: str | None = None
    git_branch: str | None = None
    parent_session_id: str | None = None


class HermesSessionJsonlImporter(ConversationImporter):
    """Parse one or more native Hermes Agent JSONL session exports."""

    name = "hermes-session-jsonl"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("Hermes session input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("Hermes session input exceeds 20000000 bytes")

        records = _parse_records(text)
        payloads: list[dict[str, Any]] = []
        seen_session_ids: set[str] = set()
        for record, line_number in records:
            parsed = _parse_session(record, line_number=line_number)
            if parsed.id in seen_session_ids:
                raise ValueError(
                    f"Hermes session line {line_number} duplicates session id {parsed.id!r}"
                )
            seen_session_ids.add(parsed.id)
            payloads.append(
                _payload(parsed, line_number=line_number, source=source, title=title)
            )
        return payloads


def _parse_records(text: str) -> list[tuple[dict[str, Any], int]]:
    records: list[tuple[dict[str, Any], int]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Hermes session line {line_number} must be valid JSON"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(f"Hermes session line {line_number} must be an object")
        records.append((record, line_number))
    if not records:
        raise ValueError("Hermes session input is empty")
    return records


def _parse_session(record: dict[str, Any], *, line_number: int) -> _HermesSession:
    _validate_record(_SESSION_VALIDATOR, record, "record", line_number)
    try:
        return _HermesSession.model_validate(record)
    except ValidationError as exc:
        raise ValueError(f"Hermes session on line {line_number} is invalid") from exc


def _payload(
    session: _HermesSession,
    *,
    line_number: int,
    source: str | None,
    title: str | None,
) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    seen_message_identities: set[str] = set()
    for message_index, message in enumerate(session.messages, start=1):
        if not isinstance(message, dict):
            continue
        if not _MESSAGE_CANDIDATE_VALIDATOR.is_valid(message):
            continue
        _validate_record(
            _MESSAGE_VALIDATOR,
            message,
            f"message {message_index}",
            line_number,
        )
        if not _is_visible(message) or message.get("_compressed_summary") in {True, 1}:
            continue
        role = message["role"]
        if role not in _ROLES:
            continue
        message_text = _message_text(message["content"])
        if message_text is None:
            continue
        identity = _nonempty_string(message.get("message_uid"))
        if identity is not None and identity in seen_message_identities:
            continue
        if identity is not None:
            seen_message_identities.add(identity)
        messages.append({"role": role, "text": message_text})

    if not messages:
        raise ValueError(
            f"Hermes session {session.id!r} on line {line_number} contains no "
            "conversational text"
        )

    resolved_title = title or _nonempty_string(session.title)
    metadata: dict[str, Any] = {
        "importer": HermesSessionJsonlImporter.name,
        "platform": "hermes",
        "ingestion_method": "jsonl-import",
        "source_session_id": session.id,
    }
    _copy_nonempty(session.source, metadata, "hermes_source")
    _copy_nonempty(session.model, metadata, "model")
    _copy_nonempty(session.cwd, metadata, "directory")
    _copy_nonempty(session.git_repo_root, metadata, "git_repo_root")
    _copy_nonempty(session.git_branch, metadata, "git_branch")
    _copy_nonempty(session.parent_session_id, metadata, "parent_session_id")
    if resolved_title is not None:
        metadata["title"] = resolved_title

    payload: dict[str, Any] = {
        "source": source or "hermes",
        "messages": messages,
        "metadata": metadata,
    }
    if resolved_title is not None:
        payload["title"] = resolved_title
    if session.started_at is not None:
        payload["timestamp"] = _timestamp(session.started_at, session.id).isoformat()
    return payload


def _is_visible(message: dict[str, Any]) -> bool:
    return message.get("active", True) not in {False, 0} or message.get(
        "compacted", False
    ) in {True, 1}


def _message_text(content: Any) -> str | None:
    if isinstance(content, str):
        return content if content.strip() else None
    parts: list[str] = []
    for block in content:
        value: str | None = None
        if isinstance(block, str):
            value = block
        elif isinstance(block, dict) and block.get("type") in {None, "text"}:
            value = block.get("text") or block.get("content")
        if isinstance(value, str) and value.strip():
            parts.append(value)
    return "\n\n".join(parts) if parts else None


def _timestamp(value: float, session_id: str) -> datetime:
    if not math.isfinite(value):
        raise ValueError(f"Hermes session {session_id!r} started_at is invalid")
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(
            f"Hermes session {session_id!r} started_at is invalid"
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
        f"Hermes session {record_name} on line {line_number} does not match "
        f"the importer schema{location}: {error.message}"
    )


def _copy_nonempty(value: Any, target: dict[str, Any], key: str) -> None:
    parsed = _nonempty_string(value)
    if parsed is not None:
        target[key] = parsed


def _nonempty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
