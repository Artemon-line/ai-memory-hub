from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from memory.config import HubConfig


class BaseIngestionAgent(ABC):
    """Base interface for deterministic ingestion agents."""

    def __init__(self, config: HubConfig | Dict[str, Any]):
        self.config = config

    @abstractmethod
    async def ingest_messages(
        self, conversation_json: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Ingest a pre-formatted conversation JSON object."""

    def preprocess_messages(self, conversation_json: Dict[str, Any]) -> Dict[str, Any]:
        return conversation_json

    def postprocess_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return result

    async def search(
        self, query: str, *, top_k: int = 5, result_mode: str = "chunks"
    ) -> Dict[str, Any]:
        raise NotImplementedError("search is not implemented")

    async def retrieve(self, memory_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("retrieve is not implemented")

    async def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        max_context_tokens: int | None = None,
        result_mode: str = "chunks",
    ) -> Dict[str, Any]:
        raise NotImplementedError("ask is not implemented")

    async def health(self) -> Dict[str, Any]:
        raise NotImplementedError("health is not implemented")

    async def fact_search(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        include_superseded: bool = False,
    ) -> Dict[str, Any]:
        raise NotImplementedError("fact_search is not implemented")

    async def profile_get(self, *, subject: str = "user") -> Dict[str, Any]:
        raise NotImplementedError("profile_get is not implemented")

    async def fact_supersede(
        self, *, fact_id: str, superseded_by: str
    ) -> Dict[str, Any]:
        raise NotImplementedError("fact_supersede is not implemented")
