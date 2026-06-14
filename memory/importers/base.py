from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ConversationImporter(ABC):
    """Convert an external conversation format into unified payloads."""

    name: str

    @abstractmethod
    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        """Parse source text into payloads accepted by the ingestion normalizer."""

