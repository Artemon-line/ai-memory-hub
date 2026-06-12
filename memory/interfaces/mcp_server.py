from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import unquote
from typing import Any, Awaitable, Callable
from uuid import UUID

import jsonschema
from memory.backend.redaction import redact_content_hashes
from memory.config import HubConfig
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.mvp_ingestion import normalize_conversation_json
from memory.ingestion.validate import validate_conversation

ToolFn = Callable[..., Awaitable[dict[str, Any]]]

logger = logging.getLogger(__name__)


TOOL_DESCRIPTIONS: dict[str, str] = {
    "memory_validate": "Validate a conversation payload against the conversation schema.",
    "memory_insert": "Insert a conversation into memory.",
    "memory_search": "Search existing memory by text query.",
    "memory_retrieve": "Retrieve a stored memory item by ID.",
    "memory_ask": "Answer a question using stored memory search results.",
    "memory_fact_search": "Search normalized extracted memory facts.",
    "memory_profile_get": "Return active normalized facts for a subject.",
    "memory_fact_supersede": "Mark one normalized fact as superseded by another fact.",
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

    # Copy safe top-level fields
    for key, value in data.items():
        if key not in ("conversation", "memory"):
            payload[key] = value

    # Redact memory/conversation
    if "memory" in data:
        payload["memory"] = redact_content_hashes(data["memory"])
    if "conversation" in data:
        payload["conversation"] = redact_content_hashes(data["conversation"])

    # NOW redact results (after merge)
    if "results" in payload:
        payload["results"] = redact_content_hashes(payload["results"])

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


def unwrap_array(value):
    if isinstance(value, dict) and "item" in value:
        return value["item"]
    return value


def _validate_optional_uuid(value: Any, *, field_name: str = "id") -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        UUID(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} must be a valid UUID") from exc
    return text


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
    tags = unwrap_array(tags)
    source = unwrap_array(source)
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
            float(row.get("conversation_score", row.get("score", 0.0))),
            str(row.get("id", "")),
            float(row.get("score", 0.0)),
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


def _validate_result_mode(result_mode: str) -> str:
    if result_mode not in {"chunks", "compact", "conversations"}:
        raise ValueError("result_mode must be one of: chunks, compact, conversations")
    return result_mode


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
            "memory": redact_content_hashes(memory),
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
            "memory": redact_content_hashes(memory),
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
            "results": redact_content_hashes(matches),
            "cursor": None,
            "metadata": {"query": query, "top_k": top_k, "source": source},
        }

    @mcp.resource("memory://health")
    async def health():
        return {"status": "ok"}

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
            "results": redact_content_hashes(filtered),
            "cursor": None,
            "metadata": {"day": day, "top_k": top_k, "source": source},
        }


def _register_prompts(mcp: Any) -> None:
    @mcp.prompt(name="save_conversation")
    def save_conversation_prompt() -> str:
        return (
            "Save this chat to ai-memory-hub using MCP tools directly.\n"
            "You MUST include ALL user and assistant messages, including code blocks, SQL, Python, multi-line text, and long responses. Do NOT filter or summarize any messages.\n"
            "Before insert, call `memory_validate` with argument `conversation_json`.\n"
            "Only call `memory_insert` after validation passes (do not use curl or config-file reads).\n"
            "Never include the save command itself in messages.\n"
            "Never include MCP instructions in messages.\n"
            "Never include tool output in messages.\n"
            "Build `conversation_json.messages` from the full prior dialog turns in this chat (include both user and assistant turns).\n"
            "Do not store only the save command; exclude meta instructions like 'save this conversation' unless explicitly requested.\n"
            "Apply filters before insert: include semantic conversation content, exclude tool/debug chatter.\n"
            "Filter out: MCP/tool instructions, operational planning text, raw JSON tool outputs, and command-like test prompts.\n"
            "If needed, summarize excluded operational content in `metadata.notes` instead of `messages`.\n"
            "Construct `conversation_json` to conform to `memory/schema/conversation.schema.json`.\n"
            "Default behavior: omit `id` unless the backend requires a caller-supplied identifier; when IDs are backend-generated, let the server assign the canonical UUID.\n"
            "If validation fails, fix payload first and re-validate before insert; do not call insert with invalid payload.\n"
            'Required shape when caller-supplied IDs are required: {"id":"...","source":"...","timestamp":"...","messages":[{"role":"user","text":"..."}],"metadata":{"imported_at":"..."}}.\n'
            "Example: if the user asked about Europe and the assistant answered, those turns must be in `messages`.\n"
            "After insert succeeds, call `memory_retrieve` with the inserted id and confirm it was stored."
        )

    @mcp.prompt(name="search_memory")
    def search_memory_prompt(query: str) -> str:
        return (
            "Use ai-memory-hub MCP tools directly.\n"
            f'Call `memory_search` with `query="{query}"` and optional '
            """`limit`, `cursor`, `source`, `date_from`, `date_to`, `tags`.\n
            You MUST NOT pass null or None for any argument.
            Always pass limit=5 unless the user specifies otherwise.
            Always pass top_k=5 unless the user specifies otherwise.
            Never omit these fields.
            """
            "Return results and the next `cursor` when present."
        )

    @mcp.prompt(name="ask_memory")
    def ask_memory_prompt(question: str, top_k: str = "5") -> str:
        return (
            "Use ai-memory-hub MCP tools directly.\n"
            "You MUST always pass a valid integer for `top_k`.\n"
            "If the user does not specify a value, ALWAYS use top_k=5.\n"
            "Never pass null, None, omit the field, or pass a string.\n"
            f'Call `memory_ask` with `question="{question}"` and `top_k={top_k}`.\n'
            "Return only the answer and citations."
        )

    @mcp.prompt(name="summarize_conversation")
    def summarize_conversation_prompt(id: str) -> str:
        return (
            "Use ai-memory-hub MCP tools directly.\n"
            f'Call `memory_retrieve` with `id="{id}"`, then summarize the returned '
            "`memory.messages` in chronological order."
        )


def build_tool_handlers(agent: BaseIngestionAgent) -> dict[str, ToolFn]:
    async def memory_validate(conversation_json: Any) -> dict[str, Any]:
        if not isinstance(conversation_json, dict):
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="conversation_json must be an object",
                valid=False,
            )
        try:
            _validate_optional_uuid(conversation_json.get("id"))
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
                valid=False,
            )

        # Support ChatGPT-style payloads using "conversation"
        if "messages" not in conversation_json:
            if "conversation" in conversation_json:
                conversation_json["messages"] = conversation_json.pop("conversation")
            else:
                return _envelope(
                    status="error",
                    error_code="invalid_input",
                    error_message="messages must be an array",
                    valid=False,
                )

        msgs = conversation_json.get("messages")
        if msgs is not None:
            conversation_json["messages"] = unwrap_array(msgs)

        try:
            normalized = normalize_conversation_json(conversation_json, source="mcp")
            validate_conversation(normalized)
        except jsonschema.ValidationError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=_format_schema_error(exc),
                valid=False,
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
                valid=False,
            )
        return _envelope(status="ok", valid=True)

    async def memory_insert(conversation_json: Any) -> dict[str, Any]:
        if not isinstance(conversation_json, dict):
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="conversation_json must be an object",
            )
        try:
            _validate_optional_uuid(conversation_json.get("id"))
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )

        # Support ChatGPT-style payloads using "conversation"
        if "messages" not in conversation_json:
            if "conversation" in conversation_json:
                conversation_json["messages"] = conversation_json.pop("conversation")
            else:
                return _envelope(
                    status="error",
                    error_code="invalid_input",
                    error_message="messages must be an array",
                )
        msgs = conversation_json.get("messages")
        if msgs is not None:
            conversation_json["messages"] = unwrap_array(msgs)

        try:
            normalized = normalize_conversation_json(conversation_json, source="mcp")
            validate_conversation(normalized)
        except jsonschema.ValidationError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=_format_schema_error(exc),
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        try:
            result = await agent.ingest_messages(normalized)
        except Exception as exc:
            logger.exception("MCP memory_insert failed")
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
        result_mode: str = "chunks",
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
            result_mode = _validate_result_mode(str(result_mode))
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )

        try:
            result = await agent.search(query=query, top_k=100, result_mode=result_mode)
            matches = result.get("results", [])
            if not isinstance(matches, list):
                matches = []
            filtered = _apply_search_filters(
                [row for row in matches if isinstance(row, dict)],
                source=source,
                date_from=date_from,
                date_to=date_to,
                tags=unwrap_array(tags),
            )
            sorted_rows = _deterministic_sort(filtered)
            try:
                paged_rows, next_cursor = _paginate(
                    sorted_rows, limit=limit, cursor=cursor
                )
            except ValueError as exc:
                return _envelope(
                    status="error",
                    error_code="invalid_input",
                    error_message=str(exc),
                )
            result["results"] = paged_rows
            result["results"] = redact_content_hashes(result["results"])
            result["cursor"] = next_cursor
        except Exception as exc:
            logger.exception("MCP memory_search failed")
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
        return _envelope(status="ok", id=id, memory=redact_content_hashes(memory))

    async def memory_ask(
        question: str,
        top_k: int = 5,
        max_context_tokens: int | None = None,
        result_mode: str = "chunks",
    ) -> dict[str, Any]:
        if not isinstance(question, str) or not question.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="question must be a non-empty string",
            )

        question = unwrap_array(question)

        if top_k is None:
            top_k = 5

        if isinstance(top_k, str):
            try:
                top_k = int(top_k)
            except ValueError:
                return _envelope(
                    status="error",
                    error_code="invalid_input",
                    error_message="top_k must be an integer",
                )

        if not isinstance(top_k, int) or top_k < 1 or top_k > 100:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="top_k must be an integer between 1 and 100",
            )

        if max_context_tokens is not None:
            if isinstance(max_context_tokens, str):
                try:
                    max_context_tokens = int(max_context_tokens)
                except ValueError:
                    return _envelope(
                        status="error",
                        error_code="invalid_input",
                        error_message="max_context_tokens must be an integer",
                    )
            if (
                not isinstance(max_context_tokens, int)
                or max_context_tokens < 1
            ):
                return _envelope(
                    status="error",
                    error_code="invalid_input",
                    error_message="max_context_tokens must be a positive integer",
                )
        try:
            result_mode = _validate_result_mode(str(result_mode))
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )

        try:
            result = await agent.ask(
                question=question,
                top_k=top_k,
                max_context_tokens=max_context_tokens,
                result_mode=result_mode,
            )
        except Exception as exc:
            logger.exception("MCP memory_ask failed")
            return _envelope(
                status="error",
                error_code="ask_failed",
                error_message=str(exc),
            )
        return _with_envelope_defaults(result)

    async def memory_fact_search(
        subject: str | None = None,
        predicate: str | None = None,
        include_superseded: bool = False,
    ) -> dict[str, Any]:
        result = await agent.fact_search(
            subject=unwrap_array(subject),
            predicate=unwrap_array(predicate),
            include_superseded=bool(include_superseded),
        )
        return _with_envelope_defaults(result)

    async def memory_profile_get(subject: str = "user") -> dict[str, Any]:
        result = await agent.profile_get(subject=str(unwrap_array(subject) or "user"))
        return _with_envelope_defaults(result)

    async def memory_fact_supersede(fact_id: str, superseded_by: str) -> dict[str, Any]:
        if not isinstance(fact_id, str) or not fact_id.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="fact_id must be a non-empty string",
            )
        if not isinstance(superseded_by, str) or not superseded_by.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="superseded_by must be a non-empty string",
            )
        result = await agent.fact_supersede(fact_id=fact_id, superseded_by=superseded_by)
        return _with_envelope_defaults(result)

    return {
        "memory_validate": memory_validate,
        "memory_insert": memory_insert,
        "memory_search": memory_search,
        "memory_retrieve": memory_retrieve,
        "memory_ask": memory_ask,
        "memory_fact_search": memory_fact_search,
        "memory_profile_get": memory_profile_get,
        "memory_fact_supersede": memory_fact_supersede,
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
