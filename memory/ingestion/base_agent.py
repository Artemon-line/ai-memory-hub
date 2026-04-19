from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseIngestionAgent(ABC):
    """Base interface for deterministic ingestion agents."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config

    @abstractmethod
    async def ingest_messages(self, conversation_json: Dict[str, Any]) -> Dict[str, Any]:
        """Ingest a pre-formatted conversation JSON object."""

    def preprocess_messages(self, conversation_json: Dict[str, Any]) -> Dict[str, Any]:
        return conversation_json

    def postprocess_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return result

    async def search(self, query: str, *, top_k: int = 5) -> Dict[str, Any]:
        raise NotImplementedError("search is not implemented")

    async def retrieve(self, memory_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("retrieve is not implemented")
