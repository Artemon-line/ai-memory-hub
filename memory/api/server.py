from __future__ import annotations

from typing import Any

import jsonschema
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from memory.auth import install_auth_middleware
from memory.backend.redaction import redact_content_hashes
from memory.config import HubConfig, normalize_config
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.interfaces.mcp_server import create_mcp_server


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=100)
    result_mode: str = "chunks"
    project_id: str | None = None


class RetrieveRequest(BaseModel):
    id: str
    project_id: str | None = None


class AskRequest(BaseModel):
    question: str
    top_k: int = Field(default=5, ge=1, le=100)
    max_context_tokens: int | None = Field(default=None, ge=1)
    result_mode: str = "chunks"
    project_id: str | None = None


class FactSearchRequest(BaseModel):
    subject: str | None = None
    predicate: str | None = None
    include_superseded: bool = False
    project_id: str | None = None


class ProfileGetRequest(BaseModel):
    subject: str = "user"
    project_id: str | None = None


class FactSupersedeRequest(BaseModel):
    fact_id: str
    superseded_by: str
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


def _register_api_routes(app: FastAPI, agent: BaseIngestionAgent) -> None:
    def owner_id(request: Request) -> str | None:
        auth = getattr(request.state, "auth", None)
        return getattr(auth, "owner_id", None)

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
            return redact_content_hashes(
                await agent.ingest_messages(
                    conversation_json,
                    owner_id=owner_id(request),
                    project_id=project_id,
                )
            )
        except jsonschema.ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    app.post("/memory/search")(memory_search)

    async def memory_retrieve(payload: RetrieveRequest, request: Request) -> dict[str, Any]:
        try:
            conversation = await agent.retrieve(
                payload.id, owner_id=owner_id(request), project_id=payload.project_id
            )
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
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

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
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

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
                )
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

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
    cfg = normalize_config(config)
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

    if mcp_app is not None:
        app.mount("/mcp", mcp_app)

    _register_health_routes(app, agent)

    # ⭐ Only enable API if config says so
    if cfg.interfaces.api:
        _register_api_routes(app, agent)

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


app = create_app()
