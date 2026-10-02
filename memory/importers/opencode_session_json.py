from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_ROLES = {"user", "assistant"}


class OpenCodeSessionJsonImporter(ConversationImporter):
    """Parse the JSON emitted by ``opencode export <sessionID>``."""

    name = "opencode-session-json"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("OpenCode session input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("OpenCode session input exceeds 20000000 bytes")
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("OpenCode session input must be valid JSON") from exc

        if not isinstance(document, dict):
            raise ValueError("OpenCode session JSON must be an object")
        info = document.get("info")
        if not isinstance(info, dict):
            raise ValueError("OpenCode session JSON has no valid info object")
        raw_messages = document.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ValueError("OpenCode session JSON contains no messages")

        messages: list[dict[str, str]] = []
        message_times: list[datetime] = []
        model: str | None = _model_name(info.get("model"))
        for index, raw_message in enumerate(raw_messages, start=1):
            if not isinstance(raw_message, dict):
                raise ValueError(f"OpenCode message {index} must be an object")
            message_info = raw_message.get("info")
            if not isinstance(message_info, dict):
                raise ValueError(f"OpenCode message {index} has no valid info object")
            role = message_info.get("role")
            if role not in _ROLES:
                raise ValueError(f"OpenCode message {index} has an unknown role")
            parts = raw_message.get("parts")
            if not isinstance(parts, list):
                raise ValueError(f"OpenCode message {index} has invalid parts")

            content = _message_text(parts, index=index)
            if content is None:
                continue
            messages.append({"role": role, "text": content})

            created = _created_at(message_info.get("time"), field=f"message {index} time")
            if created is not None:
                message_times.append(created)
            if model is None and role == "assistant":
                model = _model_name(message_info)

        if not messages:
            raise ValueError("OpenCode session JSON contains no conversational text")

        metadata: dict[str, Any] = {
            "importer": self.name,
            "platform": "cli",
            "ingestion_method": "json-import",
        }
        session_id = info.get("id")
        if isinstance(session_id, str) and session_id.strip():
            metadata["source_session_id"] = session_id.strip()
        resolved_title = title or _nonempty_string(info.get("title"))
        if resolved_title:
            metadata["title"] = resolved_title
        directory = _nonempty_string(info.get("directory")) or _nonempty_string(
            info.get("path")
        )
        if directory:
            metadata["directory"] = directory
        if model:
            metadata["model"] = model

        payload: dict[str, Any] = {
            "source": source or "opencode",
            "messages": messages,
            "metadata": metadata,
        }
        if resolved_title:
            payload["title"] = resolved_title

        session_time = _created_at(info.get("time"), field="session time")
        timestamp = session_time or (min(message_times) if message_times else None)
        if timestamp is not None:
            payload["timestamp"] = timestamp.isoformat()
        return [payload]


def _message_text(parts: list[Any], *, index: int) -> str | None:
    text_parts: list[str] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("type") != "text":
            continue
        if part.get("ignored") is True or part.get("synthetic") is True:
            continue
        content = part.get("text")
        if not isinstance(content, str):
            raise ValueError(f"OpenCode message {index} has a non-text text part")
        if content.strip():
            text_parts.append(content)
    if not text_parts:
        return None
    return "\n\n".join(text_parts)


def _created_at(value: Any, *, field: str) -> datetime | None:
    if not isinstance(value, dict) or "created" not in value:
        return None
    created = value["created"]
    if isinstance(created, bool):
        raise ValueError(f"OpenCode {field} has an invalid created timestamp")
    if isinstance(created, int | float):
        try:
            return datetime.fromtimestamp(created / 1000, UTC)
        except (OSError, OverflowError, ValueError) as exc:
            raise ValueError(f"OpenCode {field} has an invalid created timestamp") from exc
    if isinstance(created, str):
        try:
            return datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"OpenCode {field} has an invalid created timestamp") from exc
    raise ValueError(f"OpenCode {field} has an invalid created timestamp")


def _model_name(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if not isinstance(value, dict):
        return None
    model = _nonempty_string(value.get("id")) or _nonempty_string(value.get("modelID"))
    if model is None:
        return None
    provider = _nonempty_string(value.get("providerID"))
    return f"{provider}/{model}" if provider else model


def _nonempty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
