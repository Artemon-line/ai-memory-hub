from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, Awaitable, Callable
from urllib.parse import unquote
from uuid import UUID

import jsonschema
from fastmcp import Context as FastMCPContext
from pydantic import Field

from memory.auth import current_owner_id
from memory.backend.log_safety import redact_secrets
from memory.backend.redaction import redact_content_hashes
from memory.config import HubConfig
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.mvp_ingestion import normalize_conversation_json
from memory.ingestion.save_intent import (
    InsertDisposition,
    SaveIntentError,
    validate_insert_save_intent,
)
from memory.ingestion.thread_models import (
    SearchResultMode,
    result_mode_error_message,
    result_mode_values,
)
from memory.ingestion.validate import validate_conversation

ToolFn = Callable[..., Awaitable[dict[str, Any]]]

logger = logging.getLogger(__name__)
MCP_LOGGER_NAME = "ai-memory-hub.mcp"
MCP_LOG_NOTIFICATION_LIMIT = 100
_MCP_LOG_NOTIFICATION_COUNT_ATTR = "_amh_mcp_log_notification_count"
ConversationJsonArg = Annotated[
    Any, Field(json_schema_extra={"type": "object", "additionalProperties": True})
]

SERVER_INSTRUCTIONS = (
    "Use ai-memory-hub tools directly. For memory_validate and memory_insert, "
    "pass conversation_json as a nested JSON object, not as a JSON string. "
    "Omit id by default so the hub generates the canonical UUID; if id is supplied, "
    "it must be a valid UUID. Messages may use text or content fields. "
    "Save one complete conversation per insert; do not split one thread into bulk items. "
    "Include metadata.summary as a short factual retrieval hint when available, while "
    "still preserving all source messages. "
    "Only call memory_insert when the user asked to save, confirmed a save, or enabled auto-save; "
    "include metadata.save_intent as explicit_user_request, user_confirmed, or client_auto_save. "
    "Pass project_id when saving to or reading from a shared project; omit it for the default private project. "
    "memory_search and memory_ask support source, date_from, date_to, tags, thread_id, and memory_status filters "
    "when narrowing recall. "
    "memory_fact_search and memory_profile_get support source, predicate, date range, confidence, status, "
    "source_quality, save-intent, and freshness filters."
)


TOOL_DESCRIPTIONS: dict[str, str] = {
    "memory_validate": (
        "Validate a conversation payload against the conversation schema. "
        "Pass `conversation_json` as a nested JSON object, omit `id` unless it is a valid UUID, "
        "and use message `text` or `content` fields."
    ),
    "memory_insert": (
        "Insert a conversation into memory. Pass `conversation_json` as a nested JSON object, "
        "omit `id` unless it is a valid UUID, use message `text` or `content` fields, "
        "include the whole conversation in one object rather than splitting it into batches, "
        "optionally include a short factual `metadata.summary` retrieval hint, "
        "include `metadata.save_intent` when the user asked to save, confirmed saving, or enabled auto-save, "
        "and optionally pass `project_id` for a shared workspace."
    ),
    "memory_search": (
        "Search existing memory by text query. Optional filters: source, date_from, date_to, tags, "
        "and thread_id. Use project_id for a shared workspace. Use limit and cursor for paged "
        "results. Use result_mode=threads for thread-grouped results. Use memory_status to inspect active, pending_review, rejected, or all memories."
    ),
    "memory_retrieve": "Retrieve a stored memory item by ID, optionally within a project_id and memory_status filter.",
    "memory_ask": (
        "Answer a question using stored memory and facts. Optional filters: source, date_from, "
        "date_to, tags, thread_id, project_id, and memory_status."
    ),
    "memory_fact_search": (
        "Search normalized extracted memory facts. Optional filters: source, subject, predicate, "
        "date_from, date_to, confidence, status, source_quality, save_intent, save_intent_source, "
        "freshness_from, freshness_to, and project_id."
    ),
    "memory_profile_get": (
        "Return normalized facts plus a compact fact-based summary for a subject. Optional filters: "
        "source, predicate, date_from, date_to, confidence, status, source_quality, freshness_from, "
        "freshness_to, save_intent, save_intent_source, and project_id."
    ),
    "memory_fact_supersede": "Mark one normalized fact as superseded by another fact within a project_id.",
    "memory_pending_approve": "Approve a pending memory insert so it becomes searchable and can create facts.",
    "memory_pending_reject": "Reject a pending memory insert so it remains excluded from default reads.",
}

CONVERSATION_JSON_TOOL_META: dict[str, Any] = {
    "ai-memory-hub/input-guidance": {
        "conversation_json": {
            "type": "object",
            "instructions": [
                "Pass as a nested JSON object, not as a string.",
                "Omit id by default so the hub can generate a UUID.",
                "If id is supplied, it must be a valid UUID.",
                "Messages may use text or content; content is normalized to text.",
                "Optional metadata.summary should be a short factual retrieval hint, not a substitute for messages.",
                "Include metadata.save_intent when saving intentionally: explicit_user_request, user_confirmed, or client_auto_save.",
            ],
            "minimal_example": {
                "source": "opencode",
                "messages": [
                    {"role": "user", "content": "I own a Gibson Special."},
                    {"role": "assistant", "content": "Noted."},
                ],
                "metadata": {
                    "tags": ["guitar", "opencode"],
                    "summary": "User said they own a Gibson Special.",
                    "save_intent": "explicit_user_request",
                },
            },
        }
    }
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


async def _emit_mcp_tool_log(
    ctx: FastMCPContext | None,
    *,
    tool_name: str,
    status: str,
    error_code: str | None = None,
) -> None:
    if ctx is None:
        return
    notification_count = int(getattr(ctx, _MCP_LOG_NOTIFICATION_COUNT_ATTR, 0))
    if notification_count >= MCP_LOG_NOTIFICATION_LIMIT:
        return
    setattr(ctx, _MCP_LOG_NOTIFICATION_COUNT_ATTR, notification_count + 1)
    if notification_count == MCP_LOG_NOTIFICATION_LIMIT - 1:
        try:
            await ctx.log(
                "mcp log notifications rate limited",
                level="warning",
                logger_name=MCP_LOGGER_NAME,
                extra={
                    "event": "mcp_log_notifications_rate_limited",
                    "limit": MCP_LOG_NOTIFICATION_LIMIT,
                },
            )
        except Exception:
            logger.debug("MCP client log notification failed", exc_info=True)
        return
    data: dict[str, Any] = {"tool": tool_name, "status": status}
    if error_code:
        data["error_code"] = error_code
    level = "error" if status == "error" else "info"
    try:
        await ctx.log(
            "mcp tool completed",
            level=level,
            logger_name=MCP_LOGGER_NAME,
            extra=data,
        )
    except Exception:
        logger.debug("MCP client log notification failed", exc_info=True)


def _log_mcp_tool_failure(*, operation: str, error_code: str, exc: Exception) -> None:
    logger.exception(
        "MCP tool failed",
        extra={
            "event": "mcp_tool_failed",
            "operation": operation,
            "error_code": error_code,
        },
        exc_info=(type(exc), RuntimeError(redact_secrets(str(exc))), exc.__traceback__),
    )


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
    if result_mode not in result_mode_values():
        raise ValueError(result_mode_error_message())
    return result_mode


def _ingest_value_error_code(message: str) -> str:
    if message.startswith("duplicate_conflict"):
        return "duplicate_conflict"
    if message.startswith("unauthorized_update"):
        return "unauthorized_update"
    return "invalid_input"


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
        memory = await agent.retrieve(id, owner_id=current_owner_id())
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
        result = await agent.search(
            query=unquote(query), top_k=top_k, owner_id=current_owner_id()
        )
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
        health_state = redact_content_hashes(await agent.health())
        return {"status": "ok", "mode": health_state.get("mode"), "health": health_state}

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
        result = await agent.search(query=day, top_k=top_k, owner_id=current_owner_id())
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
            "Save the whole conversation as one `conversation_json` object; do not split this thread into multiple inserts or batch-shaped payloads.\n"
            "Add a short factual `metadata.summary` when useful for retrieval, but never use it instead of full `messages`.\n"
            "Set `metadata.save_intent` to `explicit_user_request` because this prompt is an explicit save request.\n"
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


def build_tool_handlers(
    agent: BaseIngestionAgent, *, config: HubConfig | None = None
) -> dict[str, ToolFn]:
    def owner_id() -> str | None:
        return current_owner_id()

    async def memory_validate(
        conversation_json: ConversationJsonArg, ctx: FastMCPContext | None = None
    ) -> dict[str, Any]:
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
        await _emit_mcp_tool_log(ctx, tool_name="memory_validate", status="ok")
        return _envelope(status="ok", valid=True)

    async def memory_insert(
        conversation_json: ConversationJsonArg,
        project_id: str | None = None,
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
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
            insert_policy = (
                config.memory.insert_policy if config is not None else "permissive"
            )
            disposition = validate_insert_save_intent(normalized, insert_policy=insert_policy)
            validate_conversation(normalized)
        except jsonschema.ValidationError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=_format_schema_error(exc),
            )
        except SaveIntentError as exc:
            return _envelope(
                status="error",
                error_code=exc.error_code,
                error_message=str(exc),
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        try:
            if disposition == InsertDisposition.PENDING_REVIEW:
                result = await agent.store_pending_review_memory(
                    normalized, owner_id=owner_id(), project_id=project_id
                )
            else:
                result = await agent.ingest_messages(
                    normalized, owner_id=owner_id(), project_id=project_id
                )
        except ValueError as exc:
            error_message = str(exc)
            error_code = _ingest_value_error_code(error_message)
            await _emit_mcp_tool_log(
                ctx,
                tool_name="memory_insert",
                status="error",
                error_code=error_code,
            )
            return _envelope(
                status="error",
                error_code=error_code,
                error_message=error_message,
            )
        except Exception as exc:
            _log_mcp_tool_failure(
                operation="memory_insert", error_code="insert_failed", exc=exc
            )
            await _emit_mcp_tool_log(
                ctx,
                tool_name="memory_insert",
                status="error",
                error_code="insert_failed",
            )
            return _envelope(
                status="error",
                error_code="insert_failed",
                error_message=redact_secrets(str(exc)),
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
        result_mode: str = SearchResultMode.CHUNKS.value,
        project_id: str | None = None,
        memory_status: str = "active",
        thread_id: str | None = None,
        ctx: FastMCPContext | None = None,
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
            result = await agent.search(
                query=query,
                top_k=100,
                result_mode=result_mode,
                owner_id=owner_id(),
                project_id=project_id,
                memory_status=unwrap_array(memory_status) or "active",
                source=unwrap_array(source),
                date_from=unwrap_array(date_from),
                date_to=unwrap_array(date_to),
                tags=unwrap_array(tags),
                thread_id=unwrap_array(thread_id),
            )
            matches = result.get("results", [])
            if not isinstance(matches, list):
                matches = []
            sorted_rows = _deterministic_sort([row for row in matches if isinstance(row, dict)])
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
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        except Exception as exc:
            _log_mcp_tool_failure(
                operation="memory_search", error_code="search_failed", exc=exc
            )
            await _emit_mcp_tool_log(
                ctx,
                tool_name="memory_search",
                status="error",
                error_code="search_failed",
            )
            return _envelope(
                status="error",
                error_code="search_failed",
                error_message=redact_secrets(str(exc)),
            )

        await _emit_mcp_tool_log(ctx, tool_name="memory_search", status="ok")
        await _emit_mcp_tool_log(ctx, tool_name="memory_insert", status="ok")
        return _with_envelope_defaults(result)

    async def memory_retrieve(
        id: str,
        project_id: str | None = None,
        memory_status: str = "active",
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
        if not isinstance(id, str) or not id.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="id must be a non-empty string",
            )
        try:
            memory = await agent.retrieve(
                id,
                owner_id=owner_id(),
                project_id=project_id,
                memory_status=unwrap_array(memory_status) or "active",
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        except PermissionError as exc:
            return _envelope(
                status="error",
                error_code="permission_denied",
                error_message=str(exc),
            )

        if memory is None:
            return _envelope(
                status="not_found",
                id=id,
                error_code="not_found",
                error_message="memory not found",
            )
        await _emit_mcp_tool_log(ctx, tool_name="memory_retrieve", status="ok")
        return _envelope(status="ok", id=id, memory=redact_content_hashes(memory))

    async def memory_ask(
        question: str,
        top_k: int = 5,
        max_context_tokens: int | None = None,
        result_mode: str = SearchResultMode.CHUNKS.value,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        tags: list[str] | None = None,
        project_id: str | None = None,
        memory_status: str = "active",
        thread_id: str | None = None,
        ctx: FastMCPContext | None = None,
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
                owner_id=owner_id(),
                project_id=project_id,
                memory_status=unwrap_array(memory_status) or "active",
                source=unwrap_array(source),
                date_from=unwrap_array(date_from),
                date_to=unwrap_array(date_to),
                tags=unwrap_array(tags),
                thread_id=unwrap_array(thread_id),
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        except Exception as exc:
            _log_mcp_tool_failure(
                operation="memory_ask", error_code="ask_failed", exc=exc
            )
            await _emit_mcp_tool_log(
                ctx,
                tool_name="memory_ask",
                status="error",
                error_code="ask_failed",
            )
            return _envelope(
                status="error",
                error_code="ask_failed",
                error_message=redact_secrets(str(exc)),
            )
        await _emit_mcp_tool_log(ctx, tool_name="memory_ask", status="ok")
        return _with_envelope_defaults(result)

    async def memory_fact_search(
        subject: str | None = None,
        predicate: str | None = None,
        include_superseded: bool = False,
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
        project_id: str | None = None,
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
        try:
            result = await agent.fact_search(
                subject=unwrap_array(subject),
                predicate=unwrap_array(predicate),
                include_superseded=bool(include_superseded),
                owner_id=owner_id(),
                project_id=project_id,
                source=unwrap_array(source),
                date_from=unwrap_array(date_from),
                date_to=unwrap_array(date_to),
                confidence=unwrap_array(confidence),
                status=unwrap_array(status),
                source_quality=unwrap_array(source_quality),
                save_intent=unwrap_array(save_intent),
                save_intent_source=unwrap_array(save_intent_source),
                freshness_from=unwrap_array(freshness_from),
                freshness_to=unwrap_array(freshness_to),
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        await _emit_mcp_tool_log(ctx, tool_name="memory_fact_search", status="ok")
        return _with_envelope_defaults(result)

    async def memory_profile_get(
        subject: str = "user",
        predicate: str | None = None,
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
        project_id: str | None = None,
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
        try:
            result = await agent.profile_get(
                subject=str(unwrap_array(subject) or "user"),
                owner_id=owner_id(),
                project_id=project_id,
                predicate=unwrap_array(predicate),
                source=unwrap_array(source),
                date_from=unwrap_array(date_from),
                date_to=unwrap_array(date_to),
                confidence=unwrap_array(confidence),
                status=unwrap_array(status),
                source_quality=unwrap_array(source_quality),
                save_intent=unwrap_array(save_intent),
                save_intent_source=unwrap_array(save_intent_source),
                freshness_from=unwrap_array(freshness_from),
                freshness_to=unwrap_array(freshness_to),
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        await _emit_mcp_tool_log(ctx, tool_name="memory_profile_get", status="ok")
        return _with_envelope_defaults(result)

    async def memory_fact_supersede(
        fact_id: str,
        superseded_by: str,
        project_id: str | None = None,
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
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
        result = await agent.fact_supersede(
            fact_id=fact_id,
            superseded_by=superseded_by,
            owner_id=owner_id(),
            project_id=project_id,
        )
        await _emit_mcp_tool_log(ctx, tool_name="memory_fact_supersede", status="ok")
        return _with_envelope_defaults(result)

    async def memory_pending_approve(
        id: str, project_id: str | None = None, ctx: FastMCPContext | None = None
    ) -> dict[str, Any]:
        if not isinstance(id, str) or not id.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="id must be a non-empty string",
            )
        result = await agent.approve_pending_memory(
            id, owner_id=owner_id(), project_id=project_id
        )
        await _emit_mcp_tool_log(ctx, tool_name="memory_pending_approve", status="ok")
        return _with_envelope_defaults(result)

    async def memory_pending_reject(
        id: str, project_id: str | None = None, ctx: FastMCPContext | None = None
    ) -> dict[str, Any]:
        if not isinstance(id, str) or not id.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="id must be a non-empty string",
            )
        result = await agent.reject_pending_memory(
            id, owner_id=owner_id(), project_id=project_id
        )
        await _emit_mcp_tool_log(ctx, tool_name="memory_pending_reject", status="ok")
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
        "memory_pending_approve": memory_pending_approve,
        "memory_pending_reject": memory_pending_reject,
    }


def create_mcp_server(*, config: HubConfig, agent: BaseIngestionAgent):
    if not config.interfaces.mcp:
        raise ValueError("config.interfaces.mcp must be enabled to create MCP server")

    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("fastmcp package is required to run MCP server") from exc

    mcp = FastMCP(
        "ai-memory-hub",
        instructions=SERVER_INSTRUCTIONS,
        list_page_size=config.mcp.list_page_size,
    )
    handlers = build_tool_handlers(agent, config=config)

    for tool_name, tool_fn in handlers.items():
        meta = (
            CONVERSATION_JSON_TOOL_META
            if tool_name in {"memory_validate", "memory_insert"}
            else None
        )
        mcp.tool(name=tool_name, description=TOOL_DESCRIPTIONS[tool_name], meta=meta)(
            tool_fn
        )
    _register_resources(mcp, agent)
    _register_prompts(mcp)

    return mcp
