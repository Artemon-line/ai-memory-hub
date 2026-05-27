from __future__ import annotations

from datetime import datetime
from urllib.parse import unquote
from typing import Any, Awaitable, Callable

import jsonschema
from memory.config import HubConfig
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.mvp_ingestion import normalize_conversation_json
from memory.ingestion.validate import validate_conversation

ToolFn = Callable[..., Awaitable[dict[str, Any]]]


TOOL_DESCRIPTIONS: dict[str, str] = {
    "memory_validate": "Validate a conversation payload against the conversation schema.",
    "memory_insert": "Insert a conversation into memory.",
    "memory_search": "Search existing memory by text query.",
    "memory_retrieve": "Retrieve a stored memory item by ID.",
    "memory_ask": "Answer a question using stored memory search results.",
    "memory_parse_raw": "Parse raw unstructured text into structured conversation JSON using LLM.",
    "memory_insert_raw": "Parse and insert raw unstructured text into memory using LLM.",
}


def _envelope(
    *,
    status: str,
    id: str | None = None,
    results: list[Any] | None = None,
    cursor: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "id": id,
        "results": results if results is not None else [],
        "cursor": cursor,
        "error_code": error_code,
        "error_message": error_message,
    }
    payload.update(extra)
    return payload


def _with_envelope_defaults(data: dict[str, Any]) -> dict[str, Any]:
    payload = _envelope(status=str(data.get("status", "ok")))
    payload.update(data)
    return payload


def _format_schema_error(exc: jsonschema.ValidationError) -> str:
    path = ".".join(str(part) for part in exc.path)
    if path:
        return f"schema validation failed at `{path}`: {exc.message}"
    return f"schema validation failed: {exc.message}"


def _resource_metadata(conversation: dict[str, Any] | None) -> dict[str, Any]:
    item = conversation or {}
    metadata = item.get("metadata", {})
    tags = metadata.get("tags", []) if isinstance(metadata, dict) else []
    updated_at = metadata.get("updated_at") if isinstance(metadata, dict) else None
    return {
        "id": item.get("id"),
        "source": item.get("source"),
        "timestamp": item.get("timestamp"),
        "tags": tags if isinstance(tags, list) else [],
        "updated_at": updated_at or item.get("timestamp"),
    }


def _parse_date(value: str, *, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc


def _coerce_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, list) and all(isinstance(tag, str) for tag in tags):
        return tags
    raise ValueError("tags must be a list of strings")


def _apply_search_filters(
    rows: list[dict[str, Any]],
    *,
    source: str | None,
    date_from: str | None,
    date_to: str | None,
    tags: list[str] | None,
) -> list[dict[str, Any]]:
    dt_from = _parse_date(date_from or "", field_name="date_from")
    dt_to = _parse_date(date_to or "", field_name="date_to")
    required_tags = set(_coerce_tags(tags))
    filtered: list[dict[str, Any]] = []
    for row in rows:
        conversation = row.get("conversation")
        if not isinstance(conversation, dict):
            continue
        if source and conversation.get("source") != source:
            continue
        timestamp_raw = str(conversation.get("timestamp", ""))
        timestamp = _parse_date(timestamp_raw, field_name="conversation.timestamp")
        if timestamp is None:
            continue
        if dt_from and timestamp < dt_from:
            continue
        if dt_to and timestamp > dt_to:
            continue
        metadata = conversation.get("metadata", {})
        conversation_tags = (
            metadata.get("tags", []) if isinstance(metadata, dict) else []
        )
        if not isinstance(conversation_tags, list):
            conversation_tags = []
        tag_set = {tag for tag in conversation_tags if isinstance(tag, str)}
        if required_tags and not required_tags.issubset(tag_set):
            continue
        filtered.append(row)
    return filtered


def _deterministic_sort(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            float(row.get("score", 0.0)),
            str(row.get("id", "")),
            int(row.get("chunk_index", 0)),
            str(row.get("text", "")),
        ),
    )


def _paginate(
    rows: list[dict[str, Any]], *, limit: int, cursor: str | None
) -> tuple[list[dict[str, Any]], str | None]:
    if cursor is None:
        offset = 0
    else:
        if not cursor.isdigit():
            raise ValueError("cursor must be a numeric offset string")
        offset = int(cursor)
    page = rows[offset : offset + limit]
    next_offset = offset + len(page)
    next_cursor = str(next_offset) if next_offset < len(rows) else None
    return page, next_cursor


def _register_resources(mcp: Any, agent: BaseIngestionAgent) -> None:
    @mcp.resource("memory://conversation/example")
    async def conversation_example_resource() -> dict[str, Any]:
        memory = {
            "id": "example",
            "source": "example",
            "timestamp": "1970-01-01T00:00:00Z",
            "messages": [
                {
                    "role": "system",
                    "text": "Use memory://conversation/{id} to read a stored conversation.",
                }
            ],
            "metadata": {"tags": ["example"], "updated_at": "1970-01-01T00:00:00Z"},
        }
        return {
            "status": "ok",
            "id": "example",
            "memory": memory,
            "metadata": _resource_metadata(memory),
        }

    @mcp.resource("memory://conversation/{id}")
    async def conversation_resource(id: str) -> dict[str, Any]:
        if not id.strip():
            return {
                "status": "error",
                "error_code": "invalid_input",
                "error_message": "id must be non-empty",
            }
        memory = await agent.retrieve(id)
        if memory is None:
            return {
                "status": "not_found",
                "id": id,
                "error_code": "not_found",
                "error_message": "memory not found",
            }
        return {
            "status": "ok",
            "id": id,
            "memory": memory,
            "metadata": _resource_metadata(memory),
        }

    @mcp.resource("memory://search/{query}")
    async def search_resource(
        query: str, top_k: int = 5, source: str | None = None
    ) -> dict[str, Any]:
        if not query.strip():
            return {
                "status": "error",
                "error_code": "invalid_input",
                "error_message": "query must be non-empty",
            }
        result = await agent.search(query=unquote(query), top_k=top_k)
        matches = result.get("results", [])
        if source:
            matches = [
                row
                for row in matches
                if isinstance(row, dict)
                and isinstance(row.get("conversation"), dict)
                and row["conversation"].get("source") == source
            ]
        return {
            "status": "ok",
            "id": f"search:{query}",
            "results": matches,
            "cursor": None,
            "metadata": {"query": query, "top_k": top_k, "source": source},
        }

    @mcp.resource("memory://timeline/{day}")
    async def timeline_resource(
        day: str, top_k: int = 20, source: str | None = None
    ) -> dict[str, Any]:
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return {
                "status": "error",
                "error_code": "invalid_input",
                "error_message": "day must be YYYY-MM-DD",
            }
        result = await agent.search(query=day, top_k=top_k)
        matches = result.get("results", [])
        filtered: list[dict[str, Any]] = []
        for row in matches:
            if not isinstance(row, dict):
                continue
            conversation = row.get("conversation")
            if not isinstance(conversation, dict):
                continue
            timestamp = str(conversation.get("timestamp", ""))
            if not timestamp.startswith(day):
                continue
            if source and conversation.get("source") != source:
                continue
            filtered.append(row)
        return {
            "status": "ok",
            "id": f"timeline:{day}",
            "results": filtered,
            "cursor": None,
            "metadata": {"day": day, "top_k": top_k, "source": source},
        }


def _register_prompts(mcp: Any) -> None:
    @mcp.prompt(name="save_conversation")
    def save_conversation_prompt() -> str:
        return (
            "Save this chat to ai-memory-hub using MCP tools directly.\n"
            "Before insert, call `memory_validate` with argument `conversation_json`.\n"
            "Only call `memory_insert` after validation passes (do not use curl or config-file reads).\n"
            "Build `conversation_json.messages` from the full prior dialog turns in this chat (include both user and assistant turns).\n"
            "Do not store only the save command; exclude meta instructions like 'save this conversation' unless explicitly requested.\n"
            "Apply filters before insert: include semantic conversation content, exclude tool/debug chatter.\n"
            "Filter out: MCP/tool instructions, operational planning text, raw JSON tool outputs, and command-like test prompts.\n"
            "If needed, summarize excluded operational content in `metadata.notes` instead of `messages`.\n"
            "Construct `conversation_json` to conform to `memory/schema/conversation.schema.json`.\n"
            "If validation fails, fix payload first and re-validate before insert; do not call insert with invalid payload.\n"
            'Required shape: {"id":"...","source":"...","timestamp":"...","messages":[{"role":"user","text":"..."}],"metadata":{"imported_at":"..."}}.\n'
            "Example: if the user asked about Europe and the assistant answered, those turns must be in `messages`.\n"
            "After insert succeeds, call `memory_retrieve` with the inserted id and confirm it was stored."
        )

    @mcp.prompt(name="search_memory")
    def search_memory_prompt(query: str) -> str:
        return (
            "Use ai-memory-hub MCP tools directly.\n"
            f'Call `memory_search` with `query="{query}"` and optional '
            "`limit`, `cursor`, `source`, `date_from`, `date_to`, `tags`.\n"
            "Return results and the next `cursor` when present."
        )

    @mcp.prompt(name="ask_memory")
    def ask_memory_prompt(question: str, top_k: str = "5") -> str:
        return (
            "Use ai-memory-hub MCP tools directly.\n"
            f'Call `memory_ask` with `question="{question}"` and optional `top_k={top_k}`.\n'
            "Return `answer` and `citations`."
        )

    @mcp.prompt(name="summarize_conversation")
    def summarize_conversation_prompt(id: str) -> str:
        return (
            "Use ai-memory-hub MCP tools directly.\n"
            f'Call `memory_retrieve` with `id="{id}"`, then summarize the returned '
            "`memory.messages` in chronological order."
        )


def build_tool_handlers(agent: BaseIngestionAgent) -> dict[str, ToolFn]:
    async def memory_validate(conversation_json: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(conversation_json, dict):
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="conversation_json must be an object",
                valid=False,
            )
        normalized = normalize_conversation_json(conversation_json, source="mcp")
        try:
            validate_conversation(normalized)
        except jsonschema.ValidationError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=_format_schema_error(exc),
                valid=False,
            )
        return _envelope(status="ok", valid=True)

    async def memory_insert(conversation_json: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(conversation_json, dict):
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="conversation_json must be an object",
            )
        normalized = normalize_conversation_json(conversation_json, source="mcp")
        try:
            validate_conversation(normalized)
        except jsonschema.ValidationError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=_format_schema_error(exc),
            )
        try:
            result = await agent.ingest_messages(normalized)
        except Exception as exc:
            return _envelope(
                status="error",
                error_code="insert_failed",
                error_message=str(exc),
            )

        return _with_envelope_defaults(result)

    async def memory_search(
        query: str,
        top_k: int = 5,
        limit: int | None = None,
        cursor: str | None = None,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="query must be a non-empty string",
            )
        if not isinstance(top_k, int) or top_k < 1 or top_k > 100:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="top_k must be an integer between 1 and 100",
            )
        if limit is None:
            limit = top_k
        if not isinstance(limit, int) or limit < 1 or limit > 100:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="limit must be an integer between 1 and 100",
            )
        try:
            result = await agent.search(query=query, top_k=100)
            matches = result.get("results", [])
            if not isinstance(matches, list):
                matches = []
            filtered = _apply_search_filters(
                [row for row in matches if isinstance(row, dict)],
                source=source,
                date_from=date_from,
                date_to=date_to,
                tags=tags,
            )
            sorted_rows = _deterministic_sort(filtered)
            paged_rows, next_cursor = _paginate(sorted_rows, limit=limit, cursor=cursor)
            result["results"] = paged_rows
            result["cursor"] = next_cursor
        except Exception as exc:
            return _envelope(
                status="error",
                error_code="search_failed",
                error_message=str(exc),
            )

        return _with_envelope_defaults(result)

    async def memory_retrieve(id: str) -> dict[str, Any]:
        if not isinstance(id, str) or not id.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="id must be a non-empty string",
            )
        memory = await agent.retrieve(id)
        if memory is None:
            return _envelope(
                status="not_found",
                id=id,
                error_code="not_found",
                error_message="memory not found",
            )
        return _envelope(status="ok", id=id, memory=memory)

    async def memory_ask(question: str, top_k: int = 5) -> dict[str, Any]:
        if not isinstance(question, str) or not question.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="question must be a non-empty string",
            )
        if not isinstance(top_k, int) or top_k < 1 or top_k > 100:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="top_k must be an integer between 1 and 100",
            )
        try:
            result = await agent.ask(question=question, top_k=top_k)
        except Exception as exc:
            return _envelope(
                status="error",
                error_code="ask_failed",
                error_message=str(exc),
            )
        return _with_envelope_defaults(result)

    async def memory_parse_raw(text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="text must be a non-empty string",
            )
        try:
            # We cast to MVPIngestionAgent because we know it implements it
            # In a larger system we'd check capability or use a more refined interface
            if hasattr(agent, "parse_raw"):
                result = await agent.parse_raw(text) # type: ignore
                return _envelope(status="ok", conversation_json=result)
            return _envelope(
                status="error",
                error_code="not_supported",
                error_message="Agent does not support raw parsing",
            )
        except Exception as exc:
            return _envelope(
                status="error",
                error_code="parse_failed",
                error_message=str(exc),
            )

    async def memory_insert_raw(text: str) -> dict[str, Any]:
        if not isinstance(text, str) or not text.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="text must be a non-empty string",
            )
        try:
            if hasattr(agent, "ingest_raw"):
                result = await agent.ingest_raw(text)
                return _with_envelope_defaults(result)
            return _envelope(
                status="error",
                error_code="not_supported",
                error_message="Agent does not support raw ingestion",
            )
        except Exception as exc:
            return _envelope(
                status="error",
                error_code="insert_failed",
                error_message=str(exc),
            )

    return {
        "memory_validate": memory_validate,
        "memory_insert": memory_insert,
        "memory_search": memory_search,
        "memory_retrieve": memory_retrieve,
        "memory_ask": memory_ask,
        "memory_parse_raw": memory_parse_raw,
        "memory_insert_raw": memory_insert_raw,
    }


def create_mcp_server(*, config: HubConfig, agent: BaseIngestionAgent):
    if not config.interfaces.mcp:
        raise ValueError("config.interfaces.mcp must be enabled to create MCP server")

    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("fastmcp package is required to run MCP server") from exc

    mcp = FastMCP("ai-memory-hub")
    handlers = build_tool_handlers(agent)

    for tool_name, tool_fn in handlers.items():
        mcp.tool(name=tool_name, description=TOOL_DESCRIPTIONS[tool_name])(tool_fn)
    _register_resources(mcp, agent)
    _register_prompts(mcp)

    return mcp
