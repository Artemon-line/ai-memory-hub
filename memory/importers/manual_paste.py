from __future__ import annotations

import re
from typing import Any

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 5_000_000
_SPEAKER_LINE_RE = re.compile(
    r"^(?P<speaker>User|You|Human|Assistant|AI|Bot|Copilot|Claude|Gemini|ChatGPT):\s?(?P<text>.*)$",
    re.IGNORECASE,
)
_USER_SPEAKERS = {"user", "you", "human"}


class ManualPasteImporter(ConversationImporter):
    """Parse a pasted, speaker-labelled chat transcript."""

    name = "manual"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("manual paste input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("manual paste input exceeds 5000000 bytes")

        messages = _parse_messages(text)
        payload: dict[str, Any] = {
            "source": source or "manual-paste",
            "messages": messages,
            "metadata": {"importer": self.name},
        }
        if title:
            payload["title"] = title
        return [payload]


def _parse_messages(text: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in text.splitlines():
        match = _SPEAKER_LINE_RE.match(line)
        if match:
            speaker = match.group("speaker").lower()
            current = {
                "role": "user" if speaker in _USER_SPEAKERS else "assistant",
                "text": match.group("text"),
            }
            messages.append(current)
            continue
        if current is None:
            if line.strip():
                raise ValueError("manual paste has text before the first speaker label")
            continue
        current["text"] += "\n" + line

    if not messages:
        raise ValueError("manual paste did not contain supported speaker labels")
    if any(not message["text"].strip() for message in messages):
        raise ValueError("manual paste contains an empty message")
    return messages

