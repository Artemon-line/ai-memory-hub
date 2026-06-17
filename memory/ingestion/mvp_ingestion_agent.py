from __future__ import annotations

from typing import Any, Dict, Optional

import jsonschema

from memory.config import HubConfig
from memory.ingestion import mvp_ingestion
from memory.ingestion.base_agent import BaseIngestionAgent


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

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        result_mode: str = "chunks",
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> Dict[str, Any]:
        return mvp_ingestion.search(
            query=query,
            top_k=top_k,
            result_mode=result_mode,
            owner_id=owner_id,
            project_id=project_id,
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
        result_mode: str = "chunks",
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> Dict[str, Any]:
        return mvp_ingestion.ask(
            question=question,
            top_k=top_k,
            max_context_tokens=max_context_tokens,
            result_mode=result_mode,
            owner_id=owner_id,
            project_id=project_id,
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
    ) -> Dict[str, Any]:
        return mvp_ingestion.fact_search(
            subject=subject,
            predicate=predicate,
            include_superseded=include_superseded,
            owner_id=owner_id,
            project_id=project_id,
        )

    async def profile_get(
        self,
        *,
        subject: str = "user",
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> Dict[str, Any]:
        return mvp_ingestion.profile_get(
            subject=subject, owner_id=owner_id, project_id=project_id
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

    async def authenticate_bearer_token(self, token: str) -> str | None:
        return mvp_ingestion.authenticate_bearer_token(token)
