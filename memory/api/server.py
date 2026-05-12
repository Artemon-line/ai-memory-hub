from __future__ import annotations

from typing import Any

import jsonschema
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from memory.config import HubConfig, load_config, parse_config
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.mcp_server import create_mcp_server


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=100)


class RetrieveRequest(BaseModel):
    id: str


def _normalize_config(config: HubConfig | dict[str, Any] | None) -> HubConfig:
    if isinstance(config, HubConfig):
        return config
    if isinstance(config, dict):
        return parse_config(config)
    return load_config()


def _register_api_routes(app: FastAPI, agent: BaseIngestionAgent) -> None:
    async def memory_insert(conversation_json: dict[str, Any]) -> dict[str, Any]:
        try:
            return await agent.ingest_messages(conversation_json)
        except jsonschema.ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc

    app.post("/memory/insert")(memory_insert)

    async def memory_search(request: SearchRequest) -> dict[str, Any]:
        return await agent.search(request.query, top_k=request.top_k)

    app.post("/memory/search")(memory_search)

    async def memory_retrieve(request: RetrieveRequest) -> dict[str, Any]:
        conversation = await agent.retrieve(request.id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="memory not found")
        return {"status": "ok", "memory": conversation}

    app.post("/memory/retrieve")(memory_retrieve)


def create_app(
    *,
    config: HubConfig | dict[str, Any] | None = None,
    ingestion_agent: BaseIngestionAgent | None = None,
) -> FastAPI:
    cfg = _normalize_config(config)
    app = FastAPI(title="ai-memory-hub", version="0.1.0")
    agent = ingestion_agent or MVPIngestionAgent(
        config={
            "providers": {
                "embeddings": cfg.providers.embeddings,
                "vector_db": cfg.providers.vector_db,
                "agent": "mvp",
            },
            "interfaces": {"mcp": cfg.interfaces.mcp, "api": cfg.interfaces.api},
            "paths": {"data_dir": cfg.paths.data_dir},
        }
    )

    # ⭐ Only enable MCP if config says so
    if cfg.interfaces.mcp:
        mcp = create_mcp_server(config=cfg, agent=agent)
        mcp_app = mcp.http_app(path=None)
        app.mount('/mcp', mcp_app)
    
    # ⭐ Only enable API if config says so
    if cfg.interfaces.api:
        _register_api_routes(app, agent)

    return app


app = create_app()
