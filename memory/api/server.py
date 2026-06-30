from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

import jsonschema
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from memory.auth import install_auth_middleware, protected_resource_metadata
from memory.backend.log_safety import install_secret_redaction_filter, redact_secrets
from memory.backend.redaction import redact_content_hashes
from memory.config import HubConfig, ensure_token_hash_secret, normalize_config
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.ingestion.save_intent import (
    InsertDisposition,
    SaveIntentError,
    validate_insert_save_intent,
)
from memory.ingestion.thread_models import SearchResultMode
from memory.interfaces.mcp_server import create_mcp_server

logger = logging.getLogger(__name__)
REQUEST_ID_HEADER = "x-request-id"


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=100)
    result_mode: str = SearchResultMode.CHUNKS.value
    project_id: str | None = None
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    tags: list[str] | None = None
    thread_id: str | None = None


class RetrieveRequest(BaseModel):
    id: str
    project_id: str | None = None


class AskRequest(BaseModel):
    question: str
    top_k: int = Field(default=5, ge=1, le=100)
    max_context_tokens: int | None = Field(default=None, ge=1)
    result_mode: str = SearchResultMode.CHUNKS.value
    project_id: str | None = None
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    tags: list[str] | None = None
    thread_id: str | None = None


class FactSearchRequest(BaseModel):
    subject: str | None = None
    predicate: str | None = None
    include_superseded: bool = False
    project_id: str | None = None
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    confidence: str | None = None
    status: str | None = None
    source_quality: str | None = None
    freshness_from: str | None = None
    freshness_to: str | None = None


class ProfileGetRequest(BaseModel):
    subject: str = "user"
    project_id: str | None = None
    source: str | None = None
    predicate: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    confidence: str | None = None
    status: str | None = None
    source_quality: str | None = None
    freshness_from: str | None = None
    freshness_to: str | None = None


class FactSupersedeRequest(BaseModel):
    fact_id: str
    superseded_by: str
    project_id: str | None = None


class MemoryReviewRequest(BaseModel):
    id: str
    project_id: str | None = None


def _register_health_routes(app: FastAPI, agent: BaseIngestionAgent) -> None:
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "health": redact_content_hashes(await agent.health()),
        }

    async def ready() -> dict[str, Any]:
        health_state = redact_content_hashes(await agent.health())
        return {
            "status": "ok",
            "mode": health_state.get("mode"),
            "health": health_state,
        }

    app.get("/health")(health)
    app.get("/ready")(ready)


def _register_protected_resource_metadata_routes(app: FastAPI, config: HubConfig) -> None:
    async def root_metadata() -> dict[str, object]:
        return protected_resource_metadata(config, resource_path="/mcp")

    async def mcp_metadata() -> dict[str, object]:
        return protected_resource_metadata(config, resource_path="/mcp")

    app.get("/.well-known/oauth-protected-resource")(root_metadata)
    app.get("/.well-known/oauth-protected-resource/mcp")(mcp_metadata)


def _register_request_failure_logging(app: FastAPI, config: HubConfig) -> None:
    @app.middleware("http")
    async def log_request_failures(request: Request, call_next: Any) -> Any:
        request_id = _request_id(request)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "memory request failed",
                extra={
                    "event": "http_request_failed",
                    "method": request.method,
                    "path": request.url.path,
                    "auth_mode": config.api.auth,
                    "status_code": 500,
                    "request_id": request_id,
                },
            )
            raise
        response.headers[REQUEST_ID_HEADER] = request_id
        if response.status_code >= 400:
            logger.info(
                "memory request returned error",
                extra={
                    "event": "http_request_error",
                    "method": request.method,
                    "path": request.url.path,
                    "auth_mode": config.api.auth,
                    "status_code": response.status_code,
                    "request_id": request_id,
                },
            )
        return response


def _request_id(request: Request) -> str:
    value = request.headers.get(REQUEST_ID_HEADER, "").strip()
    if value and "\r" not in value and "\n" not in value and len(value) <= 128:
        return value
    return str(uuid4())


def _register_api_routes(
    app: FastAPI, agent: BaseIngestionAgent, config: HubConfig
) -> None:
    def owner_id(request: Request) -> str | None:
        auth = getattr(request.state, "auth", None)
        return getattr(auth, "owner_id", None)

    def provider_failure(operation: str, error_code: str, exc: Exception) -> HTTPException:
        logger.exception(
            "memory operation failed",
            extra={
                "event": "memory_operation_failed",
                "operation": operation,
                "error_code": error_code,
            },
        )
        return HTTPException(
            status_code=503,
            detail={
                "error_code": error_code,
                "error_message": redact_secrets(str(exc)),
            },
        )

    async def memory_insert(
        conversation_json: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        try:
            conversation_json = dict(conversation_json)
            project_id = _pop_insert_project_id(conversation_json)
            metadata = conversation_json.get("metadata")
            if isinstance(metadata, dict):
                value = metadata.get("project_id")
                project_id = str(value) if value is not None else project_id
            disposition = validate_insert_save_intent(
                conversation_json,
                insert_policy=config.memory.insert_policy,
            )
            if disposition == InsertDisposition.PENDING_REVIEW:
                return redact_content_hashes(
                    await agent.store_pending_review_memory(
                        conversation_json,
                        owner_id=owner_id(request),
                        project_id=project_id,
                    )
                )
            return redact_content_hashes(
                await agent.ingest_messages(
                    conversation_json,
                    owner_id=owner_id(request),
                    project_id=project_id,
                )
            )
        except jsonschema.ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        except SaveIntentError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": exc.error_code,
                    "error_message": str(exc),
                },
            ) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise provider_failure("insert", "insert_failed", exc) from exc

    app.post("/memory/insert")(memory_insert)

    async def memory_search(payload: SearchRequest, request: Request) -> dict[str, Any]:
        try:
            return redact_content_hashes(
                await agent.search(
                    payload.query,
                    top_k=payload.top_k,
                    result_mode=payload.result_mode,
                    owner_id=owner_id(request),
                    project_id=payload.project_id,
                    source=payload.source,
                    date_from=payload.date_from,
                    date_to=payload.date_to,
                    tags=payload.tags,
                    thread_id=payload.thread_id,
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise provider_failure("search", "search_failed", exc) from exc

    app.post("/memory/search")(memory_search)

    async def memory_retrieve(payload: RetrieveRequest, request: Request) -> dict[str, Any]:
        try:
            conversation = await agent.retrieve(
                payload.id, owner_id=owner_id(request), project_id=payload.project_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if conversation is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return {"status": "ok", "memory": redact_content_hashes(conversation)}

    app.post("/memory/retrieve")(memory_retrieve)

    async def memory_ask(payload: AskRequest, request: Request) -> dict[str, Any]:
        try:
            return redact_content_hashes(
                await agent.ask(
                    payload.question,
                    top_k=payload.top_k,
                    max_context_tokens=payload.max_context_tokens,
                    result_mode=payload.result_mode,
                    owner_id=owner_id(request),
                    project_id=payload.project_id,
                    source=payload.source,
                    date_from=payload.date_from,
                    date_to=payload.date_to,
                    tags=payload.tags,
                    thread_id=payload.thread_id,
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise provider_failure("ask", "ask_failed", exc) from exc

    app.post("/memory/ask")(memory_ask)

    async def memory_fact_search(
        payload: FactSearchRequest, request: Request
    ) -> dict[str, Any]:
        try:
            return redact_content_hashes(
                await agent.fact_search(
                    subject=payload.subject,
                    predicate=payload.predicate,
                    include_superseded=payload.include_superseded,
                    owner_id=owner_id(request),
                    project_id=payload.project_id,
                    source=payload.source,
                    date_from=payload.date_from,
                    date_to=payload.date_to,
                    confidence=payload.confidence,
                    status=payload.status,
                    source_quality=payload.source_quality,
                    freshness_from=payload.freshness_from,
                    freshness_to=payload.freshness_to,
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    app.post("/memory/facts/search")(memory_fact_search)

    async def memory_profile_get(
        payload: ProfileGetRequest, request: Request
    ) -> dict[str, Any]:
        try:
            return redact_content_hashes(
                await agent.profile_get(
                    subject=payload.subject,
                    owner_id=owner_id(request),
                    project_id=payload.project_id,
                    source=payload.source,
                    predicate=payload.predicate,
                    date_from=payload.date_from,
                    date_to=payload.date_to,
                    confidence=payload.confidence,
                    status=payload.status,
                    source_quality=payload.source_quality,
                    freshness_from=payload.freshness_from,
                    freshness_to=payload.freshness_to,
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    app.post("/memory/profile/get")(memory_profile_get)

    async def memory_fact_supersede(
        payload: FactSupersedeRequest, request: Request
    ) -> dict[str, Any]:
        try:
            return redact_content_hashes(
                await agent.fact_supersede(
                    fact_id=payload.fact_id,
                    superseded_by=payload.superseded_by,
                    owner_id=owner_id(request),
                    project_id=payload.project_id,
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    app.post("/memory/facts/supersede")(memory_fact_supersede)

    async def memory_pending_approve(
        payload: MemoryReviewRequest, request: Request
    ) -> dict[str, Any]:
        try:
            return redact_content_hashes(
                await agent.approve_pending_memory(
                    payload.id,
                    owner_id=owner_id(request),
                    project_id=payload.project_id,
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def memory_pending_reject(
        payload: MemoryReviewRequest, request: Request
    ) -> dict[str, Any]:
        try:
            return redact_content_hashes(
                await agent.reject_pending_memory(
                    payload.id,
                    owner_id=owner_id(request),
                    project_id=payload.project_id,
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    app.post("/memory/pending/approve")(memory_pending_approve)
    app.post("/memory/pending/reject")(memory_pending_reject)


def _pop_insert_project_id(conversation_json: dict[str, Any]) -> str | None:
    value = conversation_json.pop("project_id", None)
    if value is None:
        return None
    metadata = conversation_json.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        conversation_json["metadata"] = metadata
    metadata["project_id"] = str(value)
    return str(value)


def create_app(
    *,
    config: HubConfig | dict[str, Any] | None = None,
    ingestion_agent: BaseIngestionAgent | None = None,
) -> FastAPI:
    install_secret_redaction_filter()
    cfg = normalize_config(config)
    ensure_token_hash_secret(cfg)
    mcp_app = None
    agent = ingestion_agent or MVPIngestionAgent(
        config=cfg
    )

    # ⭐ Only enable MCP if config says so
    if cfg.interfaces.mcp:
        mcp = create_mcp_server(config=cfg, agent=agent)
        mcp_app = mcp.http_app(path="/")

    app = FastAPI(
        title="ai-memory-hub",
        version="0.1.0",
        lifespan=mcp_app.lifespan if mcp_app is not None else None,
    )

    install_auth_middleware(app, config=cfg, agent=agent)
    _register_request_failure_logging(app, cfg)

    if mcp_app is not None:
        app.mount("/mcp", mcp_app)

    _register_health_routes(app, agent)
    _register_protected_resource_metadata_routes(app, cfg)

    # ⭐ Only enable API if config says so
    if cfg.interfaces.api:
        _register_api_routes(app, agent, cfg)

    print(
        "\n"
        + "==============================\n"
        + "  ai-memory-hub  |  RUNNING  \n"
        + "==============================\n"
        + f"  version: {app.version}\n"
        + f"  embeddings: {cfg.providers.embeddings}\n"
        + f"  embedding_model: {cfg.providers.embedding_model}\n"
        + f"  openai_base_url: {cfg.openai.base_url}\n"
        + f"  vector_db: {cfg.providers.vector_db}\n"
        + f"  mcp enabled: {cfg.interfaces.mcp}\n"
        + f"  api enabled: {cfg.interfaces.api}\n"
        + f"  data_dir: {cfg.paths.data_dir}\n"
    )

    return app
