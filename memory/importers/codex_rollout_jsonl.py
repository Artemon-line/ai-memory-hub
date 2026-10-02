from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_ROLES = {"user", "assistant"}
_TEXT_TYPES_BY_ROLE = {
    "user": {"input_text"},
    "assistant": {"output_text"},
}


class CodexRolloutJsonlImporter(ConversationImporter):
    """Parse a persisted Codex CLI or app rollout JSONL session."""

    name = "codex-rollout-jsonl"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("Codex rollout input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("Codex rollout input exceeds 20000000 bytes")

        session_meta: dict[str, Any] | None = None
        candidates: list[tuple[dict[str, str], datetime | None, str | None]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Codex rollout line {line_number} must be valid JSON"
                ) from exc
            if not isinstance(record, dict):
                raise ValueError(f"Codex rollout line {line_number} must be an object")

            record_type = record.get("type")
            record_payload = record.get("payload")
            if record_type == "session_meta" and session_meta is None:
                if not isinstance(record_payload, dict):
                    raise ValueError(
                        f"Codex rollout line {line_number} has invalid session metadata"
                    )
                session_meta = dict(record_payload)
                continue
            if record_type != "response_item" or not isinstance(record_payload, dict):
                continue
            if (
                record_payload.get("type") != "message"
                or record_payload.get("role") not in _ROLES
            ):
                continue

            role = str(record_payload["role"])
            content = record_payload.get("content")
            if not isinstance(content, list):
                raise ValueError(
                    f"Codex rollout message on line {line_number} has invalid content"
                )
            message_text = _message_text(content, role=role, line_number=line_number)
            if message_text is None:
                continue
            timestamp = _timestamp(record.get("timestamp"), line_number=line_number)
            candidates.append(
                (
                    {"role": role, "text": message_text},
                    timestamp,
                    _turn_id(record_payload) if role == "user" else None,
                )
            )

        last_user_by_turn = {
            turn_id: index
            for index, (message, _, turn_id) in enumerate(candidates)
            if message["role"] == "user" and turn_id is not None
        }
        retained = [
            (message, timestamp)
            for index, (message, timestamp, turn_id) in enumerate(candidates)
            if turn_id is None or last_user_by_turn[turn_id] == index
        ]
        messages = [message for message, _ in retained]
        message_times = [timestamp for _, timestamp in retained if timestamp is not None]

        roles = {message["role"] for message in messages}
        if roles != _ROLES:
            raise ValueError(
                "Codex rollout must contain user and assistant conversational text"
            )

        metadata: dict[str, Any] = {
            "importer": self.name,
            "platform": "codex",
            "ingestion_method": "jsonl-import",
        }
        if session_meta is not None:
            _copy_nonempty(session_meta, "id", metadata, "source_session_id")
            _copy_nonempty(session_meta, "cwd", metadata, "directory")
            _copy_nonempty(session_meta, "source", metadata, "session_source")
            _copy_nonempty(session_meta, "originator", metadata, "originator")
            _copy_nonempty(session_meta, "cli_version", metadata, "cli_version")
            _copy_nonempty(session_meta, "model_provider", metadata, "model_provider")
            git = session_meta.get("git")
            if isinstance(git, dict):
                _copy_nonempty(git, "branch", metadata, "git_branch")
                _copy_nonempty(git, "commit_hash", metadata, "git_commit")
        if title:
            metadata["title"] = title

        payload: dict[str, Any] = {
            "source": source or "codex",
            "messages": messages,
            "metadata": metadata,
        }
        if title:
            payload["title"] = title

        session_time = (
            _timestamp(session_meta.get("timestamp"), field="session timestamp")
            if session_meta is not None
            else None
        )
        timestamp = session_time or (message_times[0] if message_times else None)
        if timestamp is not None:
            payload["timestamp"] = timestamp.isoformat()
        return [payload]


def _message_text(content: list[Any], *, role: str, line_number: int) -> str | None:
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in _TEXT_TYPES_BY_ROLE[role]:
            continue
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError(
                f"Codex rollout message on line {line_number} has a non-text text block"
            )
        if text.strip():
            parts.append(text)
    if not parts:
        return None
    return "\n\n".join(parts)


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
        raise ValueError(f"Codex rollout {label} is invalid")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Codex rollout {label} is invalid") from exc


def _copy_nonempty(
    source: dict[str, Any], source_key: str, target: dict[str, Any], target_key: str
) -> None:
    value = source.get(source_key)
    if isinstance(value, str) and value.strip():
        target[target_key] = value.strip()


def _turn_id(payload: dict[str, Any]) -> str | None:
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("turn_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
