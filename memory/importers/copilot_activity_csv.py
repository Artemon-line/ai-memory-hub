from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime
from typing import Any

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_HEADERS = ("Conversation", "Time", "Author", "Message")
_ROLES = {"Human": "user", "AI": "assistant"}


class CopilotActivityCsvImporter(ConversationImporter):
    """Parse the CSV emitted by Microsoft Copilot activity history export."""

    name = "copilot-activity-csv"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("Copilot activity input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("Copilot activity input exceeds 20000000 bytes")

        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff"), newline=""))
        if tuple(reader.fieldnames or ()) != _HEADERS:
            raise ValueError(
                "Copilot activity CSV must have headers: " + ", ".join(_HEADERS)
            )

        grouped: dict[str, list[tuple[datetime, int, dict[str, str]]]] = defaultdict(list)
        order: list[str] = []
        for index, row in enumerate(reader):
            conversation = (row.get("Conversation") or "").strip()
            author = (row.get("Author") or "").strip()
            message = row.get("Message") or ""
            if not conversation:
                raise ValueError(f"Copilot activity row {index + 2} has no conversation")
            if author not in _ROLES:
                raise ValueError(f"Copilot activity row {index + 2} has unknown author: {author}")
            if not message.strip():
                raise ValueError(f"Copilot activity row {index + 2} has an empty message")
            timestamp = _parse_timestamp(row.get("Time") or "", row=index + 2)
            if conversation not in grouped:
                order.append(conversation)
            grouped[conversation].append(
                (timestamp, index, {"role": _ROLES[author], "text": message})
            )

        if not grouped:
            raise ValueError("Copilot activity CSV contains no messages")

        payloads: list[dict[str, Any]] = []
        for conversation in order:
            rows = grouped[conversation]
            rows.sort(
                key=lambda item: (
                    item[0],
                    0 if item[2]["role"] == "user" else 1,
                    -item[1],
                )
            )
            payloads.append(
                {
                    "source": source or "microsoft-copilot",
                    "title": title or conversation,
                    "timestamp": rows[0][0].isoformat(),
                    "messages": [item[2] for item in rows],
                    "metadata": {
                        "importer": self.name,
                        "platform": "web",
                        "ingestion_method": "csv-import",
                    },
                }
            )
        return payloads


def _parse_timestamp(value: str, *, row: int) -> datetime:
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Copilot activity row {row} has an invalid time") from exc
