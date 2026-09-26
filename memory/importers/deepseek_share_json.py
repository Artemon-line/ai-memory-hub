from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_ROLES = {"USER": "user", "ASSISTANT": "assistant"}


class DeepSeekShareJsonImporter(ConversationImporter):
    """Parse the JSON returned by DeepSeek's public share-content endpoint."""

    name = "deepseek-share-json"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("DeepSeek share input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("DeepSeek share input exceeds 20000000 bytes")
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("DeepSeek share input must be valid JSON") from exc

        data = _conversation_data(document)
        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list) or not raw_messages:
            raise ValueError("DeepSeek share JSON contains no messages")

        messages: list[dict[str, str]] = []
        timestamps: list[float] = []
        for index, raw_message in enumerate(raw_messages):
            if not isinstance(raw_message, dict):
                raise ValueError(f"DeepSeek message {index + 1} must be an object")
            role = _ROLES.get(str(raw_message.get("role", "")).upper())
            if role is None:
                raise ValueError(f"DeepSeek message {index + 1} has an unknown role")
            text_content = _message_text(raw_message.get("fragments"), index=index + 1)
            messages.append({"role": role, "text": text_content})
            timestamp = raw_message.get("inserted_at")
            if isinstance(timestamp, int | float):
                timestamps.append(float(timestamp))

        metadata: dict[str, Any] = {
            "importer": self.name,
            "platform": "web",
            "ingestion_method": "json-import",
        }
        model_type = data.get("model_type")
        if isinstance(model_type, str) and model_type.strip():
            metadata["model"] = model_type

        payload: dict[str, Any] = {
            "source": source or "deepseek",
            "messages": messages,
            "metadata": metadata,
        }
        resolved_title = title or data.get("title")
        if isinstance(resolved_title, str) and resolved_title.strip():
            payload["title"] = resolved_title.strip()
        if timestamps:
            payload["timestamp"] = datetime.fromtimestamp(min(timestamps), UTC).isoformat()
        return [payload]


def _conversation_data(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("DeepSeek share JSON must be an object")
    try:
        data = document["data"]["biz_data"]
    except (KeyError, TypeError) as exc:
        raise ValueError("DeepSeek share JSON has an unsupported envelope") from exc
    if not isinstance(data, dict):
        raise ValueError("DeepSeek share JSON has an unsupported envelope")
    return data


def _message_text(fragments: Any, *, index: int) -> str:
    if not isinstance(fragments, list):
        raise ValueError(f"DeepSeek message {index} has invalid fragments")
    parts: list[str] = []
    attachments: list[str] = []
    for fragment in fragments:
        if not isinstance(fragment, dict):
            continue
        fragment_type = str(fragment.get("type", "")).upper()
        if fragment_type in {"REQUEST", "RESPONSE"}:
            content = fragment.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content)
        elif fragment_type == "FILE":
            files = fragment.get("files")
            if isinstance(files, list):
                for file in files:
                    if isinstance(file, dict):
                        name = file.get("file_name")
                        if isinstance(name, str) and name.strip():
                            attachments.append(name.strip())
    if attachments:
        parts.append("Attachments: " + ", ".join(attachments))
    text = "\n\n".join(parts)
    if not text.strip():
        raise ValueError(f"DeepSeek message {index} has no conversational content")
    return text
