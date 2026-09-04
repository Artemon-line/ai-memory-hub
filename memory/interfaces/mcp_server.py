from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Annotated, Any, Awaitable, Callable
from urllib.parse import unquote
from uuid import UUID

import jsonschema
from fastmcp import Context as FastMCPContext
from pydantic import Field

from memory.auth import (
    AUTH_CONFIG_PATH,
    AUTH_RECONNECT_HINT,
    MCP_CLIENT_APPROVAL_HINT,
    READ_SCOPE,
    WRITE_SCOPE,
    current_auth_context,
    current_owner_id,
)
from memory.backend.log_safety import redact_secrets
from memory.backend.redaction import redact_content_hashes
from memory.config import HubConfig
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.mvp_ingestion import (
    normalize_conversation_json,
    reset_audit_context,
    set_audit_context,
    validate_json,
)
from memory.ingestion.save_intent import (
    InsertDisposition,
    SaveIntentError,
    validate_insert_save_intent,
)
from memory.ingestion.thread_models import (
    MCPResponseFormat,
    SearchResultMode,
    response_format_error_message,
    response_format_values,
    result_mode_error_message,
    result_mode_values,
)
from memory.interfaces.mcp_response_format import (
    format_ask_response,
    format_fact_search_response,
    format_profile_response,
    format_retrieve_response,
    format_search_response,
)
from memory.observability.metrics import metrics
from memory.observability.tracing import start_observability_span

ToolFn = Callable[..., Awaitable[dict[str, Any]]]

logger = logging.getLogger(__name__)
MCP_LOGGER_NAME = "ai-memory-hub.mcp"
MCP_LOG_NOTIFICATION_LIMIT = 100
_MCP_LOG_NOTIFICATION_COUNT_ATTR = "_amh_mcp_log_notification_count"
ConversationJsonArg = Annotated[
    Any, Field(json_schema_extra={"type": "object", "additionalProperties": True})
]
ResponseFormatArg = Annotated[
    str,
    Field(
        description=(
            "Payload detail for MCP read tools: concise returns agent-facing facts, "
            "summaries, and citations without full conversations; detailed preserves "
            "the audit-friendly record shape."
        ),
        json_schema_extra={"enum": [mode.value for mode in MCPResponseFormat]},
    ),
]
FactLimitArg = Annotated[
    int | None,
    Field(
        description=(
            "Maximum fact rows returned by concise fact/profile reads. Omit for "
            "the concise default; detailed remains unbounded."
        ),
        json_schema_extra={"minimum": 1, "maximum": 100},
    ),
]
FactQueryArg = Annotated[
    str | None,
    Field(
        description=(
            "Optional free-text query over normalized fact text. Use this when "
            "the client does not know the subject or predicate yet."
        )
    ),
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
    "when narrowing recall. memory_retrieve supports response_format for id-based reads. "
    "Use response_format=concise for normal agent recall; "
    "use response_format=detailed only when auditing full stored records. "
    "memory_fact_search supports free-text query plus subject and predicate filters. "
    "memory_fact_search and memory_profile_get support source, date range, confidence, status, "
    "source_quality, save-intent, and freshness filters, plus the same response_format option."
)


TOOL_DESCRIPTIONS: dict[str, str] = {
    "memory_validate": (
        "Read-only validation for a conversation payload against the conversation schema. "
        "Pass `conversation_json` as a nested JSON object, omit `id` unless it is a valid UUID, "
        "and use message `text` or `content` fields."
    ),
    "memory_insert": (
        "Write a conversation into local memory. Requires the `memory:write` auth scope when MCP auth is enabled. "
        "Pass `conversation_json` as a nested JSON object, "
        "omit `id` unless it is a valid UUID, use message `text` or `content` fields, "
        "include the whole conversation in one object rather than splitting it into batches, "
        "optionally include a short factual `metadata.summary` retrieval hint, "
        "include `metadata.save_intent` when the user asked to save, confirmed saving, or enabled auto-save, "
        "and optionally pass `project_id` for a shared workspace."
    ),
    "memory_search": (
        "Read-only search of existing memory by text query. Optional filters: source, date_from, date_to, tags, "
        "and thread_id. Use project_id for a shared workspace. Use limit and cursor for paged "
        "results. Use result_mode=threads for thread-grouped results. Use response_format=concise "
        "for normal recall or detailed for full conversation payloads. Use memory_status to inspect active, pending_review, quarantined, rejected, or all memories."
    ),
    "memory_retrieve": (
        "Read-only retrieval of a stored memory item by ID, optionally within "
        "a project_id and memory_status filter. Use response_format=concise "
        "for normal recall or detailed for the full stored record."
    ),
    "memory_ask": (
        "Read-only question answering using stored memory and facts. Optional filters: source, date_from, "
        "date_to, tags, thread_id, project_id, and memory_status. Use response_format=concise "
        "for normal recall or detailed for full search rows."
    ),
    "memory_fact_search": (
        "Read-only search of normalized extracted memory facts. Optional filters: query, source, subject, predicate, "
        "date_from, date_to, confidence, status, source_quality, save_intent, save_intent_source, "
        "freshness_from, freshness_to, limit, and project_id. Use response_format=concise for "
        "deduplicated, limited facts or detailed for full fact provenance."
    ),
    "memory_profile_get": (
        "Read-only profile retrieval with normalized facts plus a compact fact-based summary for a subject. Optional filters: "
        "source, predicate, date_from, date_to, confidence, status, source_quality, freshness_from, "
        "freshness_to, save_intent, save_intent_source, limit, and project_id. Use "
        "response_format=concise for deduplicated, limited facts and summary counts or detailed "
        "for full fact provenance."
    ),
    "memory_fact_supersede": "Write fact state by marking one normalized fact as superseded by another fact within a project_id. Requires the `memory:write` auth scope when MCP auth is enabled.",
    "memory_pending_approve": "Write reviewable memory state by approving a pending or quarantined insert so it becomes searchable and can create facts. Requires the `memory:write` auth scope when MCP auth is enabled.",
    "memory_pending_reject": "Write reviewable memory state by rejecting a pending or quarantined insert so it remains excluded from default reads. Requires the `memory:write` auth scope when MCP auth is enabled.",
    "memory_project_list": "Read-only list of project workspaces visible to the authenticated user.",
    "memory_project_default_get": "Read-only retrieval of the authenticated user's default private project workspace.",
    "memory_project_get": "Read-only retrieval of one visible project workspace by project_id.",
}

@dataclass(frozen=True, slots=True)
class MCPToolPolicy:
    read_only: bool
    required_scopes: tuple[str, ...] = (READ_SCOPE,)
    destructive: bool = False
    idempotent: bool = True
    open_world: bool = False
    memory_effect: str = "reads local memory state"
    internal_side_effects: str | None = None
    retry_behavior: str | None = None

    def annotations(self) -> dict[str, bool]:
        return {
            "readOnlyHint": self.read_only,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }

    def auth_meta(self, *, auth_mode: str | None) -> dict[str, Any]:
        authenticated = auth_mode is not None and auth_mode != "none"
        meta: dict[str, Any] = {
            "auth_mode": auth_mode or "unknown",
            "required_scopes": list(self.required_scopes) if authenticated else [],
            "scopes_when_authenticated": list(self.required_scopes),
            "memory_effect": self.memory_effect,
            "config_path": AUTH_CONFIG_PATH,
            "configuration_hint": AUTH_RECONNECT_HINT,
            "approval_hint": MCP_CLIENT_APPROVAL_HINT,
        }
        if authenticated and WRITE_SCOPE in self.required_scopes:
            meta["write_scope"] = WRITE_SCOPE
        if self.internal_side_effects:
            meta["internal_side_effects"] = self.internal_side_effects
        if self.retry_behavior:
            meta["retry_behavior"] = self.retry_behavior
        return meta


READ_ONLY_TOOL_POLICY = MCPToolPolicy(read_only=True)
READ_ONLY_WITH_INTERNAL_WRITES_POLICY = MCPToolPolicy(
    read_only=True,
    internal_side_effects=(
        "May refresh server-owned derived summaries or initialize default project metadata; "
        "does not store user conversation memory."
    ),
)
MEMORY_INSERT_POLICY = MCPToolPolicy(
    read_only=False,
    required_scopes=(READ_SCOPE, WRITE_SCOPE),
    idempotent=False,
    memory_effect="writes local conversation memory",
    retry_behavior=(
        "Exact repeated inserts are deduplicated; changed same-thread payloads may append or create memory."
    ),
)
NON_DESTRUCTIVE_WRITE_POLICY = MCPToolPolicy(
    read_only=False,
    required_scopes=(READ_SCOPE, WRITE_SCOPE),
    idempotent=False,
    memory_effect="writes local memory state",
)
MCP_TOOL_POLICIES: dict[str, MCPToolPolicy] = {
    "memory_validate": READ_ONLY_TOOL_POLICY,
    "memory_insert": MEMORY_INSERT_POLICY,
    "memory_search": READ_ONLY_TOOL_POLICY,
    "memory_retrieve": READ_ONLY_WITH_INTERNAL_WRITES_POLICY,
    "memory_ask": READ_ONLY_TOOL_POLICY,
    "memory_fact_search": READ_ONLY_TOOL_POLICY,
    "memory_profile_get": READ_ONLY_WITH_INTERNAL_WRITES_POLICY,
    "memory_fact_supersede": NON_DESTRUCTIVE_WRITE_POLICY,
    "memory_pending_approve": NON_DESTRUCTIVE_WRITE_POLICY,
    "memory_pending_reject": NON_DESTRUCTIVE_WRITE_POLICY,
    "memory_project_list": READ_ONLY_WITH_INTERNAL_WRITES_POLICY,
    "memory_project_default_get": READ_ONLY_WITH_INTERNAL_WRITES_POLICY,
    "memory_project_get": READ_ONLY_WITH_INTERNAL_WRITES_POLICY,
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


def _tool_policy(tool_name: str) -> MCPToolPolicy:
    try:
        return MCP_TOOL_POLICIES[tool_name]
    except KeyError as exc:
        raise ValueError(f"MCP tool policy missing for {tool_name}") from exc


def _tool_meta(tool_name: str, *, config: HubConfig | None = None) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if tool_name in {"memory_validate", "memory_insert"}:
        meta.update(CONVERSATION_JSON_TOOL_META)
    auth_mode = str(config.api.auth) if config is not None else None
    meta["ai-memory-hub/auth"] = _tool_policy(tool_name).auth_meta(auth_mode=auth_mode)
    return meta


def _validate_tool_registration(handlers: dict[str, ToolFn]) -> None:
    handler_names = set(handlers)
    missing_descriptions = handler_names - set(TOOL_DESCRIPTIONS)
    missing_policies = handler_names - set(MCP_TOOL_POLICIES)
    if missing_descriptions or missing_policies:
        problems: list[str] = []
        if missing_descriptions:
            problems.append(f"descriptions missing for {sorted(missing_descriptions)}")
        if missing_policies:
            problems.append(f"policies missing for {sorted(missing_policies)}")
        raise ValueError("MCP tool registration is incomplete: " + "; ".join(problems))


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


def _insufficient_scope_error(
    scope: str, *, auth_mode: str = "unknown", granted_scopes: tuple[str, ...] = ()
) -> dict[str, Any]:
    return _envelope(
        status="error",
        error_code="insufficient_scope",
        error_message=(
            f"{scope} scope is required for this MCP tool. "
            f"{MCP_CLIENT_APPROVAL_HINT} {AUTH_RECONNECT_HINT}"
        ),
        required_scope=scope,
        required_scopes=[scope],
        granted_scopes=sorted(granted_scopes),
        auth_mode=auth_mode,
        auth_config_path=AUTH_CONFIG_PATH,
        auth_config_hint=AUTH_RECONNECT_HINT,
        approval_hint=MCP_CLIENT_APPROVAL_HINT,
    )


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
    tool_call_id = _mcp_tool_call_id(ctx)
    if tool_call_id is not None:
        data["mcp_tool_call_id"] = tool_call_id
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


async def _mcp_permission_denied_response(
    ctx: FastMCPContext | None, *, tool_name: str, exc: Exception
) -> dict[str, Any]:
    logger.warning(
        "MCP tool permission denied",
        extra={
            "event": "mcp_permission_denied",
            "tool": tool_name,
            "exception_type": type(exc).__name__,
        },
    )
    await _emit_mcp_tool_log(
        ctx,
        tool_name=tool_name,
        status="error",
        error_code="permission_denied",
    )
    return _envelope(
        status="error",
        error_code="permission_denied",
        error_message="Access to the requested project was denied",
    )


def _is_permission_denied_exception(exc: Exception) -> bool:
    return isinstance(exc, PermissionError) or str(exc) == "project access denied"


def _log_mcp_tool_failure(
    *,
    operation: str,
    error_code: str,
    exc: Exception,
    mcp_tool_call_id: str | None = None,
) -> None:
    logger.exception(
        "MCP tool failed",
        extra={
            "event": "mcp_tool_failed",
            "operation": operation,
            "error_code": error_code,
            "mcp_tool_call_id": mcp_tool_call_id or "-",
        },
        exc_info=(type(exc), RuntimeError(redact_secrets(str(exc))), exc.__traceback__),
    )


def _mcp_tool_call_id(ctx: FastMCPContext | None) -> str | None:
    if ctx is None:
        return None
    for attr in ("tool_call_id", "request_id", "message_id", "id"):
        value = getattr(ctx, attr, None)
        if isinstance(value, str | int) and str(value).strip():
            return _safe_correlation_id(value)
    request_context = getattr(ctx, "request_context", None)
    if request_context is not None:
        for attr in ("request_id", "message_id", "id"):
            value = getattr(request_context, attr, None)
            if isinstance(value, str | int) and str(value).strip():
                return _safe_correlation_id(value)
    return None


def _safe_correlation_id(value: str | int) -> str:
    text = str(value).strip()
    if "\r" in text or "\n" in text:
        return "invalid"
    return text[:128]


def _instrument_mcp_tool(tool_name: str, tool_fn: ToolFn) -> ToolFn:
    @wraps(tool_fn)
    async def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        status = "error"
        error_code = "unhandled_exception"
        ctx = kwargs.get("ctx")
        audit_token = set_audit_context(
            source_surface="mcp",
            request_id=_mcp_tool_call_id(ctx) if ctx is not None else None,
        )
        with start_observability_span(
            f"mcp.{tool_name}",
            attributes={
                "mcp.tool": tool_name,
                "operation": tool_name,
            },
        ) as span:
            try:
                result = await tool_fn(*args, **kwargs)
                status = str(result.get("status") or "ok")
                if status == "not_found":
                    status = "error"
                error_code = str(result.get("error_code") or "none")
                return result
            except Exception as exc:
                error_code = type(exc).__name__
                raise
            finally:
                elapsed_ms = (time.perf_counter() - started) * 1000
                if span is not None:
                    span.set_attribute("memory.status", status)
                    span.set_attribute("memory.error_code", error_code)
                    span.set_attribute("memory.duration_ms", elapsed_ms)
                metrics.increment(
                    "memory_mcp_tool_calls_total",
                    tool=tool_name,
                    status=status,
                    error_code=error_code,
                )
                metrics.observe(
                    "memory_mcp_tool_duration_ms",
                    elapsed_ms,
                    tool=tool_name,
                    status=status,
                )
                reset_audit_context(audit_token)

    return wrapped


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
    filtered: list[dict[str, Any]] = []
    tags = unwrap_array(tags)
    source = unwrap_array(source)
    required_tags = set(_coerce_tags(tags))
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


def _validate_response_format(response_format: Any) -> str:
    value = unwrap_array(response_format)
    if value is None:
        return MCPResponseFormat.CONCISE.value
    normalized = str(value)
    if normalized not in response_format_values():
        raise ValueError(response_format_error_message())
    return normalized


def _validate_optional_limit(limit: Any, *, field_name: str = "limit") -> int | None:
    value = unwrap_array(limit)
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an integer") from exc
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 100:
        raise ValueError(f"{field_name} must be an integer between 1 and 100")
    return value


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

    def require_scope(scope: str) -> dict[str, Any] | None:
        auth = current_auth_context()
        if auth is None or scope in auth.scopes:
            return None
        return _insufficient_scope_error(
            scope,
            auth_mode=auth.auth_mode,
            granted_scopes=tuple(auth.scopes),
        )

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
            validate_json(normalized)
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

    async def memory_insert(
        conversation_json: ConversationJsonArg,
        project_id: str | None = None,
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
        scope_error = require_scope(WRITE_SCOPE)
        if scope_error is not None:
            await _emit_mcp_tool_log(
                ctx,
                tool_name="memory_insert",
                status="error",
                error_code="insufficient_scope",
            )
            return scope_error
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
            validate_json(normalized)
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
        except PermissionError as exc:
            return await _mcp_permission_denied_response(
                ctx, tool_name="memory_insert", exc=exc
            )
        except Exception as exc:
            if _is_permission_denied_exception(exc):
                return await _mcp_permission_denied_response(
                    ctx, tool_name="memory_insert", exc=exc
                )
            _log_mcp_tool_failure(
                operation="memory_insert",
                error_code="insert_failed",
                exc=exc,
                mcp_tool_call_id=_mcp_tool_call_id(ctx),
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

        payload = _with_envelope_defaults(result)
        metrics.increment(
            "memory_insert_total",
            source=normalized.get("source") or "unknown",
            status=payload.get("status") or "ok",
            deduplicated=payload.get("deduplicated", False),
        )
        return payload

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
        response_format: ResponseFormatArg = MCPResponseFormat.CONCISE.value,
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
            response_format = _validate_response_format(response_format)
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
            result["cursor"] = next_cursor
            result = format_search_response(result, response_format)
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        except PermissionError as exc:
            return await _mcp_permission_denied_response(
                ctx, tool_name="memory_search", exc=exc
            )
        except Exception as exc:
            if _is_permission_denied_exception(exc):
                return await _mcp_permission_denied_response(
                    ctx, tool_name="memory_search", exc=exc
                )
            _log_mcp_tool_failure(
                operation="memory_search",
                error_code="search_failed",
                exc=exc,
                mcp_tool_call_id=_mcp_tool_call_id(ctx),
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

        return _with_envelope_defaults(result)

    async def memory_retrieve(
        id: str,
        project_id: str | None = None,
        memory_status: str = "active",
        response_format: ResponseFormatArg = MCPResponseFormat.CONCISE.value,
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
        if not isinstance(id, str) or not id.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="id must be a non-empty string",
            )
        try:
            response_format = _validate_response_format(response_format)
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        try:
            memory = await agent.retrieve(
                id,
                owner_id=owner_id(),
                project_id=project_id,
                memory_status=unwrap_array(memory_status) or "active",
            )
        except PermissionError as exc:
            return await _mcp_permission_denied_response(
                ctx, tool_name="memory_retrieve", exc=exc
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )

        if memory is None:
            return _envelope(
                status="not_found",
                id=id,
                error_code="not_found",
                error_message="memory not found",
            )
        return _with_envelope_defaults(
            format_retrieve_response(
                _envelope(status="ok", id=id, memory=redact_content_hashes(memory)),
                response_format,
            )
        )

    async def memory_ask(
        question: str,
        top_k: int = 5,
        max_context_tokens: int | None = None,
        result_mode: str = SearchResultMode.CHUNKS.value,
        response_format: ResponseFormatArg = MCPResponseFormat.CONCISE.value,
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
            response_format = _validate_response_format(response_format)
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
        except PermissionError as exc:
            return await _mcp_permission_denied_response(
                ctx, tool_name="memory_ask", exc=exc
            )
        except Exception as exc:
            if _is_permission_denied_exception(exc):
                return await _mcp_permission_denied_response(
                    ctx, tool_name="memory_ask", exc=exc
                )
            _log_mcp_tool_failure(
                operation="memory_ask",
                error_code="ask_failed",
                exc=exc,
                mcp_tool_call_id=_mcp_tool_call_id(ctx),
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
        formatted = format_ask_response(result, response_format)
        response = _with_envelope_defaults(formatted)
        if response_format == MCPResponseFormat.CONCISE.value and "results" not in formatted:
            response.pop("results", None)
        return response

    async def memory_fact_search(
        query: FactQueryArg = None,
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
        limit: FactLimitArg = None,
        project_id: str | None = None,
        response_format: ResponseFormatArg = MCPResponseFormat.CONCISE.value,
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
        try:
            response_format = _validate_response_format(response_format)
            limit = _validate_optional_limit(limit)
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        try:
            result = await agent.fact_search(
                query=unwrap_array(query),
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
        except PermissionError as exc:
            return await _mcp_permission_denied_response(
                ctx, tool_name="memory_fact_search", exc=exc
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        except Exception as exc:
            if _is_permission_denied_exception(exc):
                return await _mcp_permission_denied_response(
                    ctx, tool_name="memory_fact_search", exc=exc
                )
            raise
        return _with_envelope_defaults(
            format_fact_search_response(result, response_format, limit=limit)
        )

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
        limit: FactLimitArg = None,
        project_id: str | None = None,
        response_format: ResponseFormatArg = MCPResponseFormat.CONCISE.value,
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
        try:
            response_format = _validate_response_format(response_format)
            limit = _validate_optional_limit(limit)
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
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
        except PermissionError as exc:
            return await _mcp_permission_denied_response(
                ctx, tool_name="memory_profile_get", exc=exc
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        except Exception as exc:
            if _is_permission_denied_exception(exc):
                return await _mcp_permission_denied_response(
                    ctx, tool_name="memory_profile_get", exc=exc
                )
            raise
        return _with_envelope_defaults(
            format_profile_response(result, response_format, limit=limit)
        )

    async def memory_fact_supersede(
        fact_id: str,
        superseded_by: str,
        project_id: str | None = None,
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
        scope_error = require_scope(WRITE_SCOPE)
        if scope_error is not None:
            await _emit_mcp_tool_log(
                ctx,
                tool_name="memory_fact_supersede",
                status="error",
                error_code="insufficient_scope",
            )
            return scope_error
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
        try:
            result = await agent.fact_supersede(
                fact_id=fact_id,
                superseded_by=superseded_by,
                owner_id=owner_id(),
                project_id=project_id,
            )
        except PermissionError as exc:
            return await _mcp_permission_denied_response(
                ctx, tool_name="memory_fact_supersede", exc=exc
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        except Exception as exc:
            if _is_permission_denied_exception(exc):
                return await _mcp_permission_denied_response(
                    ctx, tool_name="memory_fact_supersede", exc=exc
                )
            raise
        return _with_envelope_defaults(result)

    async def memory_pending_approve(
        id: str, project_id: str | None = None, ctx: FastMCPContext | None = None
    ) -> dict[str, Any]:
        scope_error = require_scope(WRITE_SCOPE)
        if scope_error is not None:
            await _emit_mcp_tool_log(
                ctx,
                tool_name="memory_pending_approve",
                status="error",
                error_code="insufficient_scope",
            )
            return scope_error
        if not isinstance(id, str) or not id.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="id must be a non-empty string",
            )
        try:
            result = await agent.approve_pending_memory(
                id, owner_id=owner_id(), project_id=project_id
            )
        except PermissionError as exc:
            return await _mcp_permission_denied_response(
                ctx, tool_name="memory_pending_approve", exc=exc
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        except Exception as exc:
            if _is_permission_denied_exception(exc):
                return await _mcp_permission_denied_response(
                    ctx, tool_name="memory_pending_approve", exc=exc
                )
            raise
        return _with_envelope_defaults(result)

    async def memory_pending_reject(
        id: str, project_id: str | None = None, ctx: FastMCPContext | None = None
    ) -> dict[str, Any]:
        scope_error = require_scope(WRITE_SCOPE)
        if scope_error is not None:
            await _emit_mcp_tool_log(
                ctx,
                tool_name="memory_pending_reject",
                status="error",
                error_code="insufficient_scope",
            )
            return scope_error
        if not isinstance(id, str) or not id.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="id must be a non-empty string",
            )
        try:
            result = await agent.reject_pending_memory(
                id, owner_id=owner_id(), project_id=project_id
            )
        except PermissionError as exc:
            return await _mcp_permission_denied_response(
                ctx, tool_name="memory_pending_reject", exc=exc
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        except Exception as exc:
            if _is_permission_denied_exception(exc):
                return await _mcp_permission_denied_response(
                    ctx, tool_name="memory_pending_reject", exc=exc
                )
            raise
        return _with_envelope_defaults(result)

    async def memory_project_list(ctx: FastMCPContext | None = None) -> dict[str, Any]:
        result = await agent.project_list(owner_id=owner_id())
        return _with_envelope_defaults(result)

    async def memory_project_default_get(
        ctx: FastMCPContext | None = None,
    ) -> dict[str, Any]:
        result = await agent.project_default_get(owner_id=owner_id())
        return _with_envelope_defaults(result)

    async def memory_project_get(
        project_id: str, ctx: FastMCPContext | None = None
    ) -> dict[str, Any]:
        if not isinstance(project_id, str) or not project_id.strip():
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message="project_id must be a non-empty string",
            )
        try:
            result = await agent.project_get(project_id, owner_id=owner_id())
        except PermissionError as exc:
            return await _mcp_permission_denied_response(
                ctx, tool_name="memory_project_get", exc=exc
            )
        except ValueError as exc:
            return _envelope(
                status="error",
                error_code="invalid_input",
                error_message=str(exc),
            )
        except Exception as exc:
            if _is_permission_denied_exception(exc):
                return await _mcp_permission_denied_response(
                    ctx, tool_name="memory_project_get", exc=exc
                )
            raise
        return _with_envelope_defaults(result)

    handlers: dict[str, ToolFn] = {
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
        "memory_project_list": memory_project_list,
        "memory_project_default_get": memory_project_default_get,
        "memory_project_get": memory_project_get,
    }
    return {name: _instrument_mcp_tool(name, handler) for name, handler in handlers.items()}


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
    _validate_tool_registration(handlers)

    for tool_name, tool_fn in handlers.items():
        tool_policy = _tool_policy(tool_name)
        mcp.tool(
            name=tool_name,
            description=TOOL_DESCRIPTIONS[tool_name],
            annotations=tool_policy.annotations(),
            meta=_tool_meta(tool_name, config=config),
        )(tool_fn)
    _register_resources(mcp, agent)
    _register_prompts(mcp)

    return mcp
