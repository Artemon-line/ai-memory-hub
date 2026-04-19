from __future__ import annotations

from typing import Any, Awaitable, Callable

from memory.config import HubConfig, load_config, parse_config
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent

ToolFn = Callable[..., Awaitable[dict[str, Any]]]


def _normalize_config(config: HubConfig | dict[str, Any] | None) -> HubConfig:
    if isinstance(config, HubConfig):
        return config
    if isinstance(config, dict):
        return parse_config(config)
    return load_config()


def build_tool_handlers(agent: BaseIngestionAgent) -> dict[str, ToolFn]:
    async def memory_insert(conversation_json: dict[str, Any]) -> dict[str, Any]:
        return await agent.ingest_messages(conversation_json)

    async def memory_search(query: str, top_k: int = 5) -> dict[str, Any]:
        return await agent.search(query=query, top_k=top_k)

    async def memory_retrieve(id: str) -> dict[str, Any]:
        memory = await agent.retrieve(id)
        if memory is None:
            return {"status": "not_found", "id": id}
        return {"status": "ok", "memory": memory}

    return {
        "memory.insert": memory_insert,
        "memory.search": memory_search,
        "memory.retrieve": memory_retrieve,
    }


def create_mcp_server(
    *,
    config: HubConfig | dict[str, Any] | None = None,
    ingestion_agent: BaseIngestionAgent | None = None,
):
    cfg = _normalize_config(config)
    agent = ingestion_agent or MVPIngestionAgent(config={
        "providers": {
            "embeddings": cfg.providers.embeddings,
            "vector_db": cfg.providers.vector_db,
            "agent": "mvp",
        },
        "interfaces": {"mcp": cfg.interfaces.mcp, "api": cfg.interfaces.api},
        "paths": {"data_dir": cfg.paths.data_dir},
    })

    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("fastmcp package is required to run MCP server") from exc

    mcp = FastMCP("ai-memory-hub")
    handlers = build_tool_handlers(agent)

    @mcp.tool(name="memory.insert")
    async def memory_insert(conversation_json: dict[str, Any]) -> dict[str, Any]:
        return await handlers["memory.insert"](conversation_json)

    @mcp.tool(name="memory.search")
    async def memory_search(query: str, top_k: int = 5) -> dict[str, Any]:
        return await handlers["memory.search"](query, top_k)

    @mcp.tool(name="memory.retrieve")
    async def memory_retrieve(id: str) -> dict[str, Any]:
        return await handlers["memory.retrieve"](id)

    return mcp
