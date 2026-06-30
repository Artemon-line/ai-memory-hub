from __future__ import annotations

from typing import Any, Dict, Optional

import jsonschema

from memory.config import HubConfig
from memory.ingestion import mvp_ingestion
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.thread_models import SearchResultMode


class MVPIngestionAgent(BaseIngestionAgent):
    """Deterministic ingestion agent for schema-first MVP pipeline."""

    def __init__(
        self,
        config: HubConfig | dict[str, Any],
        *,
        runtime: mvp_ingestion.RuntimeDependencies | None = None,
    ):
        super().__init__(config)
        mvp_ingestion.configure_runtime(runtime=runtime, config=config)

    async def ingest_messages(
        self,
        conversation_json: Dict[str, Any],
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> Dict[str, Any]:
        payload = self.preprocess_messages(conversation_json)
        try:
            result = mvp_ingestion.ingest_messages(
                payload, owner_id=owner_id, project_id=project_id
            )
        except jsonschema.ValidationError:
            raise
        return self.postprocess_result(result)

    async def store_pending_review_memory(
        self,
        conversation_json: Dict[str, Any],
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> Dict[str, Any]:
        payload = self.preprocess_messages(conversation_json)
        result = mvp_ingestion.store_pending_review_memory(
            payload, owner_id=owner_id, project_id=project_id
        )
        return self.postprocess_result(result)

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        result_mode: str = SearchResultMode.CHUNKS.value,
        owner_id: str | None = None,
        project_id: str | None = None,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        thread_id: str | None = None,
    ) -> Dict[str, Any]:
        return mvp_ingestion.search(
            query=query,
            top_k=top_k,
            result_mode=result_mode,
            owner_id=owner_id,
            project_id=project_id,
            source=source,
            date_from=date_from,
            date_to=date_to,
            tags=tags,
            thread_id=thread_id,
        )

    async def retrieve(
        self, memory_id: str, *, owner_id: str | None = None, project_id: str | None = None
    ) -> Optional[Dict[str, Any]]:
        return mvp_ingestion.retrieve(memory_id, owner_id=owner_id, project_id=project_id)

    async def ask(
        self,
        question: str,
        *,
        top_k: int = 5,
        max_context_tokens: int | None = None,
        result_mode: str = SearchResultMode.CHUNKS.value,
        owner_id: str | None = None,
        project_id: str | None = None,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        thread_id: str | None = None,
    ) -> Dict[str, Any]:
        return mvp_ingestion.ask(
            question=question,
            top_k=top_k,
            max_context_tokens=max_context_tokens,
            result_mode=result_mode,
            owner_id=owner_id,
            project_id=project_id,
            source=source,
            date_from=date_from,
            date_to=date_to,
            tags=tags,
            thread_id=thread_id,
        )

    async def health(self) -> Dict[str, Any]:
        return mvp_ingestion.runtime_health()

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
        return mvp_ingestion.fact_search(
            subject=subject,
            predicate=predicate,
            include_superseded=include_superseded,
            owner_id=owner_id,
            project_id=project_id,
            source=source,
            date_from=date_from,
            date_to=date_to,
            confidence=confidence,
            status=status,
            source_quality=source_quality,
            freshness_from=freshness_from,
            freshness_to=freshness_to,
        )

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
        return mvp_ingestion.profile_get(
            subject=subject,
            owner_id=owner_id,
            project_id=project_id,
            source=source,
            predicate=predicate,
            date_from=date_from,
            date_to=date_to,
            confidence=confidence,
            status=status,
            source_quality=source_quality,
            freshness_from=freshness_from,
            freshness_to=freshness_to,
        )

    async def fact_supersede(
        self,
        *,
        fact_id: str,
        superseded_by: str,
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> Dict[str, Any]:
        return mvp_ingestion.fact_supersede(
            fact_id=fact_id,
            superseded_by=superseded_by,
            owner_id=owner_id,
            project_id=project_id,
        )

    async def approve_pending_memory(
        self, memory_id: str, *, owner_id: str | None = None, project_id: str | None = None
    ) -> Dict[str, Any]:
        return mvp_ingestion.approve_pending_memory(
            memory_id, owner_id=owner_id, project_id=project_id
        )

    async def reject_pending_memory(
        self, memory_id: str, *, owner_id: str | None = None, project_id: str | None = None
    ) -> Dict[str, Any]:
        return mvp_ingestion.reject_pending_memory(
            memory_id, owner_id=owner_id, project_id=project_id
        )

    async def authenticate_bearer_token(self, token: str) -> str | None:
        return mvp_ingestion.authenticate_bearer_token(token)

    async def authenticate_bearer_token_context(self, token: str) -> dict[str, object] | None:
        return mvp_ingestion.authenticate_bearer_token_context(token)
