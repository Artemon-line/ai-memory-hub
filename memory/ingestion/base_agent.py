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
        query: str | None = None,
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
        save_intent: str | None = None,
        save_intent_source: str | None = None,
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
        save_intent: str | None = None,
        save_intent_source: str | None = None,
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

    async def project_list(self, *, owner_id: str | None = None) -> Dict[str, Any]:
        raise NotImplementedError("project_list is not implemented")

    async def project_default_get(self, *, owner_id: str | None = None) -> Dict[str, Any]:
        raise NotImplementedError("project_default_get is not implemented")

    async def project_get(self, project_id: str, *, owner_id: str | None = None) -> Dict[str, Any]:
        raise NotImplementedError("project_get is not implemented")

    async def authenticate_bearer_token(self, token: str) -> str | None:
        raise NotImplementedError("authenticate_bearer_token is not implemented")

    async def authenticate_bearer_token_context(self, token: str) -> dict[str, object] | None:
        owner_id = await self.authenticate_bearer_token(token)
        if owner_id is None:
            return None
        return {
            "owner_id": owner_id,
            "token_id": None,
            "scopes": [],
        }

    async def find_or_create_oauth_identity(
        self,
        *,
        provider: str,
        provider_subject: str,
        email: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, object]:
        raise NotImplementedError("find_or_create_oauth_identity is not implemented")

    async def create_web_session(
        self,
        *,
        session_id_hash: str,
        user_id: str,
        csrf_token_hash: str,
        expires_at: str,
    ) -> dict[str, object]:
        raise NotImplementedError("create_web_session is not implemented")

    async def web_session_for_hash(self, session_id_hash: str) -> dict[str, object] | None:
        raise NotImplementedError("web_session_for_hash is not implemented")

    async def revoke_web_session(self, session_id_hash: str) -> bool:
        raise NotImplementedError("revoke_web_session is not implemented")

    async def create_auth_token(
        self,
        *,
        owner_id: str,
        token: str,
        token_display_name: str | None = None,
        expires_at: str | None = None,
        scopes: list[str] | None = None,
    ) -> dict[str, object]:
        raise NotImplementedError("create_auth_token is not implemented")

    async def revoke_auth_token(self, token_id: str) -> dict[str, object] | None:
        raise NotImplementedError("revoke_auth_token is not implemented")

    async def create_oauth_client(
        self,
        *,
        client_id: str,
        client_name: str,
        redirect_uris: list[str],
        expires_at: str,
        max_active_clients: int,
    ) -> dict[str, object]:
        raise NotImplementedError("create_oauth_client is not implemented")

    async def oauth_client(self, client_id: str) -> dict[str, object] | None:
        raise NotImplementedError("oauth_client is not implemented")

    async def create_oauth_authorization_code(
        self,
        *,
        code: str,
        client_id: str,
        owner_id: str,
        redirect_uri: str,
        code_challenge: str,
        resource: str,
        scope: str,
        expires_at: str,
    ) -> None:
        raise NotImplementedError("create_oauth_authorization_code is not implemented")

    async def consume_oauth_authorization_code(self, code: str) -> dict[str, object] | None:
        raise NotImplementedError("consume_oauth_authorization_code is not implemented")

    async def create_oauth_refresh_token(
        self,
        *,
        refresh_token: str,
        token_family_id: str,
        client_id: str,
        owner_id: str,
        scopes: list[str],
        resource: str,
        access_token_id: str | None,
        expires_at: str,
    ) -> dict[str, object]:
        raise NotImplementedError("create_oauth_refresh_token is not implemented")

    async def oauth_refresh_token(self, refresh_token: str) -> dict[str, object] | None:
        raise NotImplementedError("oauth_refresh_token is not implemented")

    async def consume_oauth_refresh_token(self, refresh_token: str) -> dict[str, object] | None:
        raise NotImplementedError("consume_oauth_refresh_token is not implemented")

    async def revoke_oauth_refresh_token(self, refresh_token: str) -> bool:
        raise NotImplementedError("revoke_oauth_refresh_token is not implemented")

    async def revoke_oauth_refresh_token_family(self, token_family_id: str) -> bool:
        raise NotImplementedError("revoke_oauth_refresh_token_family is not implemented")

    async def revoke_oauth_authorization_for_access_token(self, access_token: str) -> bool:
        raise NotImplementedError("revoke_oauth_authorization_for_access_token is not implemented")
