from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ConversationImporter(ABC):
    """Convert an external conversation format into unified payloads."""

    name: str
    schema_override_schema: dict[str, Any] | None = None

    @abstractmethod
    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        """Parse source text into payloads accepted by the ingestion normalizer."""

    def import_text_with_schema(
        self,
        text: str,
        *,
        schema: dict[str, Any] | None = None,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        """Parse text with an optional bounded, parser-owned extraction override."""
        if schema is not None:
            raise ValueError(f"{self.name} does not support schema overrides")
        return self.import_text(text, source=source, title=title)
