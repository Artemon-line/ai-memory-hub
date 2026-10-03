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
    Path(__file__).resolve().parents[1] / "schema" / "gemini-cli-session.schema.json"
)
_STORED_ROLES = {"user": "user", "gemini": "assistant"}
_SHARED_ROLES = {"user": "user", "model": "assistant"}
_SESSION_CONTEXT_MARKER = (
    "This is the Gemini CLI. We are setting up the context for our chat."
)


def _load_document_validator() -> Any:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as schema_handle:
        schema = json.load(schema_handle)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


_DOCUMENT_VALIDATOR = _load_document_validator()


class _StoredMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", protected_namespaces=())

    type: str
    content: str | list[Any]
    timestamp: str | None = None
    model: str | None = None


class _SharedHistoryItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str
    parts: list[Any]


class GeminiCliSessionJsonImporter(ConversationImporter):
    """Parse Gemini CLI session exports and shared JSON histories."""

    name = "gemini-cli-session-json"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("Gemini CLI session input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("Gemini CLI session input exceeds 20000000 bytes")
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Gemini CLI session input must be valid JSON") from exc

        error = next(_DOCUMENT_VALIDATOR.iter_errors(document), None)
        if error is not None:
            raise ValueError("Gemini CLI session JSON must be an object or array")

        if isinstance(document, dict):
            return [
                _import_session_document(document, source=source, title=title)
            ]
        return [_import_shared_history(document, source=source, title=title)]


def _import_session_document(
    document: dict[str, Any], *, source: str | None, title: str | None
) -> dict[str, Any]:
    raw_messages = document.get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("Gemini CLI session JSON has no valid messages array")

    messages: list[dict[str, str]] = []
    message_times: list[datetime] = []
    model: str | None = None
    for index, raw_message in enumerate(raw_messages, start=1):
        if not isinstance(raw_message, dict):
            raise ValueError(f"Gemini CLI message {index} must be an object")
        raw_role = raw_message.get("type")
        role = _STORED_ROLES.get(raw_role) if isinstance(raw_role, str) else None
        if role is None:
            continue
        parsed = _validate_stored_message(raw_message, index=index)
        content = _message_text(parsed.content, index=index)
        if content is None or _is_session_context(role, content):
            continue
        messages.append({"role": role, "text": content})
        timestamp = _timestamp(parsed.timestamp, field=f"message {index} timestamp")
        if timestamp is not None:
            message_times.append(timestamp)
        if role == "assistant" and model is None:
            model = _nonempty_string(parsed.model)

    metadata: dict[str, Any] = {
        "importer": GeminiCliSessionJsonImporter.name,
        "platform": "gemini-cli",
        "ingestion_method": "json-import",
        "export_format": "session",
    }
    _copy_nonempty(document, "sessionId", metadata, "source_session_id")
    _copy_nonempty(document, "projectHash", metadata, "project_hash")
    if model:
        metadata["model"] = model
    directories = document.get("directories")
    if isinstance(directories, list):
        safe_directories = [
            value.strip()
            for value in directories
            if isinstance(value, str) and value.strip()
        ]
        if safe_directories:
            metadata["workspace_directories"] = safe_directories
    return _payload(
        messages,
        metadata,
        source=source,
        title=title,
        session_time=_timestamp(document.get("startTime"), field="start time"),
        message_times=message_times,
    )


def _import_shared_history(
    document: list[Any], *, source: str | None, title: str | None
) -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    for index, raw_item in enumerate(document, start=1):
        if not isinstance(raw_item, dict):
            raise ValueError(f"Gemini CLI shared history item {index} must be an object")
        raw_role = raw_item.get("role")
        role = _SHARED_ROLES.get(raw_role) if isinstance(raw_role, str) else None
        if role is None:
            continue
        try:
            parsed = _SharedHistoryItem.model_validate(raw_item)
        except ValidationError as exc:
            raise ValueError(
                f"Gemini CLI shared history item {index} has invalid parts"
            ) from exc
        content = _message_text(parsed.parts, index=index)
        if content is None or _is_session_context(role, content):
            continue
        messages.append({"role": role, "text": content})

    metadata: dict[str, Any] = {
        "importer": GeminiCliSessionJsonImporter.name,
        "platform": "gemini-cli",
        "ingestion_method": "json-import",
        "export_format": "shared-history",
    }
    return _payload(messages, metadata, source=source, title=title)


def _validate_stored_message(raw_message: dict[str, Any], *, index: int) -> _StoredMessage:
    try:
        return _StoredMessage.model_validate(raw_message)
    except ValidationError as exc:
        raise ValueError(f"Gemini CLI message {index} has invalid content") from exc


def _message_text(content: str | list[Any], *, index: int) -> str | None:
    if isinstance(content, str):
        return content if content.strip() else None

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or "text" not in block:
            continue
        block_text = block["text"]
        if not isinstance(block_text, str):
            raise ValueError(f"Gemini CLI message {index} has a non-text text block")
        if block_text.strip():
            parts.append(block_text)
    return "\n\n".join(parts) if parts else None


def _payload(
    messages: list[dict[str, str]],
    metadata: dict[str, Any],
    *,
    source: str | None,
    title: str | None,
    session_time: datetime | None = None,
    message_times: list[datetime] | None = None,
) -> dict[str, Any]:
    if not messages:
        raise ValueError("Gemini CLI session contains no conversational text")
    if title:
        metadata["title"] = title
    payload: dict[str, Any] = {
        "source": source or "gemini-cli",
        "messages": messages,
        "metadata": metadata,
    }
    if title:
        payload["title"] = title
    timestamp = session_time or (message_times[0] if message_times else None)
    if timestamp is not None:
        payload["timestamp"] = timestamp.isoformat()
    return payload


def _timestamp(value: Any, *, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Gemini CLI session {field} is invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Gemini CLI session {field} is invalid") from exc


def _is_session_context(role: str, content: str) -> bool:
    stripped = content.strip()
    return (
        role == "user"
        and stripped.startswith("<session_context>")
        and _SESSION_CONTEXT_MARKER in stripped
    )


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
