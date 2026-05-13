from __future__ import annotations

from typing import Any, Awaitable, Callable

from memory.config import HubConfig, normalize_config
from memory.ingestion.base_agent import BaseIngestionAgent

ToolFn = Callable[..., Awaitable[dict[str, Any]]]


TOOL_DESCRIPTIONS: dict[str, str] = {
    "memory_insert": "Insert a conversation into memory.",
    "memory_search": "Search existing memory by text query.",
    "memory_retrieve": "Retrieve a stored memory item by ID.",
}


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
        "memory_insert": memory_insert,
        "memory_search": memory_search,
        "memory_retrieve": memory_retrieve,
    }


def create_mcp_server(
    *,
    config: HubConfig | dict[str, Any] | None = None,
    agent: BaseIngestionAgent
):
    cfg = normalize_config(config)
    if not cfg.interfaces.mcp:
        raise ValueError("config.interfaces.mcp must be enabled to create MCP server")

    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("fastmcp package is required to run MCP server") from exc

    mcp = FastMCP("ai-memory-hub")
    handlers = build_tool_handlers(agent)

    for tool_name, tool_fn in handlers.items():
        mcp.tool(name=tool_name, description=TOOL_DESCRIPTIONS[tool_name])(tool_fn)

    return mcp
