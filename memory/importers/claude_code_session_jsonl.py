from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_ROLES = {"user", "assistant"}


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
            role = record.get("type")
            if role not in _ROLES:
                continue
            message = record.get("message")
            if not isinstance(message, dict):
                raise ValueError(
                    f"Claude Code message on line {line_number} has an invalid envelope"
                )
            message_role = message.get("role")
            if message_role != role:
                raise ValueError(
                    f"Claude Code message on line {line_number} has an inconsistent role"
                )
            content = _message_text(message.get("content"), line_number=line_number)
            if content is None:
                continue

            record_uuid = _nonempty_string(record.get("uuid"))
            if record_uuid is not None:
                if record_uuid in seen_uuids:
                    continue
                seen_uuids.add(record_uuid)

            messages.append({"role": role, "text": content})
            timestamp = _timestamp(record.get("timestamp"), line_number=line_number)
            if timestamp is not None:
                message_times.append(timestamp)
            if "model" not in provenance:
                model = _nonempty_string(message.get("model"))
                if model is not None:
                    provenance["model"] = model

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


def _message_text(content: Any, *, line_number: int) -> str | None:
    if isinstance(content, str):
        return content if content.strip() else None
    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError(
                f"Claude Code message on line {line_number} has a non-text text block"
            )
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts) if parts else None


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
