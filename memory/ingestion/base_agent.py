from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from memory.config import HubConfig
from memory.ingestion.thread_models import SearchResultMode


class BaseIngestionAgent(ABC):
    """Base interface for deterministic ingestion agents."""

    def __init__(self, config: HubConfig | Dict[str, Any]):
        self.config = config

    @abstractmethod
    async def ingest_messages(
        self,
        conversation_json: Dict[str, Any],
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> Dict[str, Any]:
        """Ingest a pre-formatted conversation JSON object."""

    async def store_pending_review_memory(
        self,
        conversation_json: Dict[str, Any],
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError("store_pending_review_memory is not implemented")

    def preprocess_messages(self, conversation_json: Dict[str, Any]) -> Dict[str, Any]:
        return conversation_json

    def postprocess_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return result

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        result_mode: str = SearchResultMode.CHUNKS.value,
        owner_id: str | None = None,
        project_id: str | None = None,
        memory_status: str = "active",
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        thread_id: str | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError("search is not implemented")

    async def retrieve(
        self,
        memory_id: str,
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
        memory_status: str = "active",
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError("retrieve is not implemented")

    async def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        max_context_tokens: int | None = None,
        result_mode: str = SearchResultMode.CHUNKS.value,
        owner_id: str | None = None,
        project_id: str | None = None,
        memory_status: str = "active",
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        thread_id: str | None = None,
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
        owner_id: str | None = None,
        project_id: str | None = None,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        confidence: str | None = None,
        status: str | None = None,
        source_quality: str | None = None,
        freshness_from: str | None = None,
        freshness_to: str | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError("fact_search is not implemented")

    async def profile_get(
        self,
        *,
        subject: str = "user",
        owner_id: str | None = None,
        project_id: str | None = None,
        source: str | None = None,
        predicate: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        confidence: str | None = None,
        status: str | None = None,
        source_quality: str | None = None,
        freshness_from: str | None = None,
        freshness_to: str | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError("profile_get is not implemented")

    async def fact_supersede(
        self,
        *,
        fact_id: str,
        superseded_by: str,
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError("fact_supersede is not implemented")

    async def approve_pending_memory(
        self, memory_id: str, *, owner_id: str | None = None, project_id: str | None = None
    ) -> Dict[str, Any]:
        raise NotImplementedError("approve_pending_memory is not implemented")

    async def reject_pending_memory(
        self, memory_id: str, *, owner_id: str | None = None, project_id: str | None = None
    ) -> Dict[str, Any]:
        raise NotImplementedError("reject_pending_memory is not implemented")

    async def authenticate_bearer_token(self, token: str) -> str | None:
        raise NotImplementedError("authenticate_bearer_token is not implemented")

    async def authenticate_bearer_token_context(self, token: str) -> dict[str, object] | None:
        owner_id = await self.authenticate_bearer_token(token)
        if owner_id is None:
            return None
        return {
            "owner_id": owner_id,
            "token_id": None,
            "scopes": ["memory:read", "memory:write"],
        }
