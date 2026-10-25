from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from queue import Empty, Queue
from threading import Thread
from typing import Any, Callable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class RetrievalIntent(StrEnum):
    MEMORY = "memory"
    FACT = "fact"
    TIMELINE = "timeline"
    HANDOFF = "handoff"


class TemporalPreference(StrEnum):
    LATEST = "latest"
    HISTORICAL = "historical"
    ANY = "any"


class PlannedConversationFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    tags: tuple[str, ...] = ()
    thread_id: str | None = None
    handoff_only: bool = False
    source_client: str | None = None
    source_session_id: str | None = None

    @field_validator("source", "thread_id", "source_client", "source_session_id")
    @classmethod
    def _validate_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized or len(normalized) > 256:
            raise ValueError("filter values must contain between 1 and 256 characters")
        return normalized

    @field_validator("date_from", "date_to")
    @classmethod
    def _validate_datetime(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("date filters must be ISO-8601 datetimes") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @field_validator("tags", mode="before")
    @classmethod
    def _validate_tags(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str) or not isinstance(value, Sequence):
            raise ValueError("tags must be a sequence of strings")
        normalized = tuple(dict.fromkeys(str(tag).strip() for tag in value if str(tag).strip()))
        if len(normalized) > 20 or any(len(tag) > 128 for tag in normalized):
            raise ValueError("tags exceed query-planning limits")
        return normalized


class RetrievalQueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_query: str
    semantic_query: str
    filters: PlannedConversationFilters = Field(default_factory=PlannedConversationFilters)
    intent: RetrievalIntent = RetrievalIntent.MEMORY
    exact_terms: tuple[str, ...] = ()
    preferred_fields: tuple[str, ...] = ()
    temporal_preference: TemporalPreference = TemporalPreference.ANY
    planning_mode: str = "deterministic"

    def diagnostics(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class _PlannerProposal(BaseModel):
    """Allow-listed model output. Trusted authorization scope is intentionally absent."""

    model_config = ConfigDict(extra="forbid")

    semantic_query: str | None = None
    filters: PlannedConversationFilters | None = None
    intent: RetrievalIntent | None = None
    exact_terms: tuple[str, ...] | None = None
    preferred_fields: tuple[str, ...] | None = None
    temporal_preference: TemporalPreference | None = None

    @field_validator("semantic_query")
    @classmethod
    def _validate_semantic_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 4096:
            raise ValueError("semantic_query must be non-empty and bounded")
        return normalized

    @field_validator("exact_terms", mode="before")
    @classmethod
    def _validate_exact_terms(cls, value: Any) -> tuple[str, ...] | None:
        return _bounded_string_tuple(value, allowed=None, maximum=20)

    @field_validator("preferred_fields", mode="before")
    @classmethod
    def _validate_preferred_fields(cls, value: Any) -> tuple[str, ...] | None:
        return _bounded_string_tuple(
            value,
            allowed={"text", "title", "source", "tags", "thread_id", "timestamp", "handoff"},
            maximum=7,
        )


QueryPlanner = Callable[[str, Mapping[str, Any]], Mapping[str, Any] | None]

_SOURCE_ALIASES = {
    "chatgpt": "chatgpt",
    "claude": "claude-code",
    "claude code": "claude-code",
    "codex": "codex",
    "codex cli": "codex-cli",
    "opencode": "opencode",
}
_SOURCE_PATTERN = re.compile(
    r"\b(?:(?:from|in)\s+(?:my\s+)?|my\s+)"
    r"(?P<source>chatgpt|claude(?:\s+code)?|codex(?:\s+cli)?|opencode)"
    r"\s+(?:conversation|conversations|discussion|discussions|memory|memories|session|sessions)\b",
    re.IGNORECASE,
)
_EXPLICIT_RANGE_PATTERN = re.compile(
    r"\bfrom\s+(?P<from>\d{4}-\d{2}-\d{2})(?:[T ][^\s]+)?\s+to\s+(?P<to>\d{4}-\d{2}-\d{2})(?:[T ][^\s]+)?\b",
    re.IGNORECASE,
)
_SINCE_PATTERN = re.compile(r"\b(?:since|after)\s+(?P<date>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_BEFORE_PATTERN = re.compile(r"\b(?:before|until)\s+(?P<date>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_RELATIVE_PATTERN = re.compile(
    r"\b(?:in|from|during)?\s*(?P<period>today|yesterday|last\s+week|last\s+month|past\s+\d+\s+(?:days?|weeks?|months?))\b",
    re.IGNORECASE,
)
_TAG_PATTERN = re.compile(r"(?<!\w)(?:tag:|tagged\s+|#)(?P<tag>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})", re.IGNORECASE)
_THREAD_PATTERN = re.compile(r"\bthread(?:\s+id)?\s*[:#]?\s*(?P<thread>[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255})", re.IGNORECASE)
_HANDOFF_PATTERN = re.compile(r"\b(?:handoff|hand-off|resume|unfinished\s+(?:work|task))\b", re.IGNORECASE)
_HANDOFF_FILTER_PATTERN = re.compile(
    r"\b(?:handoffs?\s+only|only\s+handoffs?|resume(?:\s+(?:the|my))?(?:\s+latest)?\s+handoff|unfinished\s+(?:work|task))\b",
    re.IGNORECASE,
)
_LATEST_PATTERN = re.compile(r"\b(?:latest|current|newest|most\s+recent)\b", re.IGNORECASE)
_HISTORICAL_PATTERN = re.compile(r"\b(?:historical|history|older|previous|formerly)\b", re.IGNORECASE)
_TIMELINE_PATTERN = re.compile(r"\b(?:timeline|chronology|over\s+time|changed?)\b", re.IGNORECASE)
_FACT_PATTERN = re.compile(r"\b(?:fact|facts|what\s+is|who\s+is|which\s+is)\b", re.IGNORECASE)
_QUOTED_PATTERN = re.compile(r"[\"']([^\"']{1,256})[\"']")


def plan_retrieval_query(
    query: str,
    *,
    explicit_filters: Mapping[str, Any] | None = None,
    planner: QueryPlanner | None = None,
    planner_timeout_seconds: float = 0.5,
    now: datetime | None = None,
) -> RetrievalQueryPlan:
    original_query = str(query).strip()
    if not original_query:
        raise ValueError("query must be a non-empty string")
    reference_time = (now or datetime.now(UTC)).astimezone(UTC)
    deterministic = _deterministic_plan(original_query, reference_time)
    plan = deterministic
    model_failed = False
    if planner is not None:
        try:
            raw_proposal = _call_planner(
                planner,
                original_query,
                deterministic.diagnostics(),
                timeout_seconds=planner_timeout_seconds,
            )
            if raw_proposal is not None:
                proposal = _PlannerProposal.model_validate(raw_proposal)
                plan = _apply_proposal(deterministic, proposal)
        except (TimeoutError, TypeError, ValueError, ValidationError):
            model_failed = True
    merged_filters = _merge_explicit_filters(plan.filters, explicit_filters or {})
    mode = "model_fallback" if model_failed else plan.planning_mode
    return plan.model_copy(update={"filters": merged_filters, "planning_mode": mode})


def _deterministic_plan(query: str, now: datetime) -> RetrievalQueryPlan:
    working = query
    source: str | None = None
    source_match = _SOURCE_PATTERN.search(working)
    if source_match:
        source = _SOURCE_ALIASES[source_match.group("source").lower().replace("  ", " ")]
        working = _remove_match(working, source_match)

    date_from: str | None = None
    date_to: str | None = None
    range_match = _EXPLICIT_RANGE_PATTERN.search(working)
    if range_match:
        date_from = _day_start(range_match.group("from"))
        date_to = _day_end(range_match.group("to"))
        working = _remove_match(working, range_match)
    else:
        since_match = _SINCE_PATTERN.search(working)
        before_match = _BEFORE_PATTERN.search(working)
        if since_match:
            date_from = _day_start(since_match.group("date"))
            working = _remove_match(working, since_match)
        if before_match:
            date_to = _day_end(before_match.group("date"))
            working = _remove_match(working, before_match)
        relative_match = _RELATIVE_PATTERN.search(working)
        if relative_match:
            date_from, date_to = _relative_range(relative_match.group("period"), now)
            working = _remove_match(working, relative_match)

    tags = tuple(dict.fromkeys(match.group("tag") for match in _TAG_PATTERN.finditer(working)))
    working = _TAG_PATTERN.sub(" ", working)
    thread_match = _THREAD_PATTERN.search(working)
    thread_id = thread_match.group("thread") if thread_match else None
    if thread_match:
        working = _remove_match(working, thread_match)

    handoff_only = bool(_HANDOFF_FILTER_PATTERN.search(query))
    temporal = TemporalPreference.ANY
    if _LATEST_PATTERN.search(query):
        temporal = TemporalPreference.LATEST
    elif _HISTORICAL_PATTERN.search(query) or date_from or date_to:
        temporal = TemporalPreference.HISTORICAL

    if handoff_only:
        intent = RetrievalIntent.HANDOFF
    elif _TIMELINE_PATTERN.search(query):
        intent = RetrievalIntent.TIMELINE
    elif _FACT_PATTERN.search(query):
        intent = RetrievalIntent.FACT
    else:
        intent = RetrievalIntent.MEMORY

    exact_terms = tuple(dict.fromkeys(term.strip() for term in _QUOTED_PATTERN.findall(query)))
    working = _strip_query_scaffolding(working)
    semantic_query = working or " ".join(exact_terms) or query
    preferred_fields = _preferred_fields(source, tags, thread_id, handoff_only, temporal)
    return RetrievalQueryPlan(
        original_query=query,
        semantic_query=semantic_query,
        filters=PlannedConversationFilters(
            source=source,
            date_from=date_from,
            date_to=date_to,
            tags=tags,
            thread_id=thread_id,
            handoff_only=handoff_only,
        ),
        intent=intent,
        exact_terms=exact_terms,
        preferred_fields=preferred_fields,
        temporal_preference=temporal,
    )


def _merge_explicit_filters(
    planned: PlannedConversationFilters, explicit: Mapping[str, Any]
) -> PlannedConversationFilters:
    values = planned.model_dump()
    for field in (
        "source",
        "date_from",
        "date_to",
        "thread_id",
        "source_client",
        "source_session_id",
    ):
        explicit_value = explicit.get(field)
        if explicit_value is not None and str(explicit_value).strip():
            values[field] = explicit_value
    explicit_tags = explicit.get("tags")
    if explicit_tags:
        values["tags"] = tuple(
            dict.fromkeys((*planned.tags, *(str(tag) for tag in explicit_tags if str(tag))))
        )
    values["handoff_only"] = bool(explicit.get("handoff_only")) or planned.handoff_only
    return PlannedConversationFilters.model_validate(values)


def _apply_proposal(base: RetrievalQueryPlan, proposal: _PlannerProposal) -> RetrievalQueryPlan:
    updates = proposal.model_dump(exclude_none=True)
    if proposal.filters is not None:
        proposed = proposal.filters
        values = proposed.model_dump()
        for field in (
            "source",
            "date_from",
            "date_to",
            "thread_id",
            "source_client",
            "source_session_id",
        ):
            base_value = getattr(base.filters, field)
            if base_value is not None:
                values[field] = base_value
        values["tags"] = tuple(dict.fromkeys((*base.filters.tags, *proposed.tags)))
        values["handoff_only"] = base.filters.handoff_only or proposed.handoff_only
        updates["filters"] = values
    updates["planning_mode"] = "model_assisted"
    values = base.model_dump()
    values.update(updates)
    return RetrievalQueryPlan.model_validate(values)


def _call_planner(
    planner: QueryPlanner,
    query: str,
    deterministic_plan: Mapping[str, Any],
    *,
    timeout_seconds: float,
) -> Mapping[str, Any] | None:
    if timeout_seconds <= 0:
        raise ValueError("planner_timeout_seconds must be positive")
    result_queue: Queue[tuple[bool, Any]] = Queue(maxsize=1)

    def invoke() -> None:
        try:
            result_queue.put((True, planner(query, deterministic_plan)))
        except Exception as exc:  # pragma: no cover - surfaced as a safe fallback
            result_queue.put((False, exc))

    Thread(target=invoke, name="memory-query-planner", daemon=True).start()
    try:
        succeeded, value = result_queue.get(timeout=timeout_seconds)
    except Empty as exc:
        raise TimeoutError("query planner timed out") from exc
    if not succeeded:
        raise ValueError("query planner failed") from value
    if value is not None and not isinstance(value, Mapping):
        raise TypeError("query planner must return a mapping or None")
    return value


def _bounded_string_tuple(
    value: Any, *, allowed: set[str] | None, maximum: int
) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError("value must be a sequence of strings")
    normalized = tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    if len(normalized) > maximum or any(len(item) > 256 for item in normalized):
        raise ValueError("planner output exceeds limits")
    if allowed is not None and not set(normalized).issubset(allowed):
        raise ValueError("planner output contains an unsupported field")
    return normalized


def _relative_range(period: str, now: datetime) -> tuple[str, str]:
    normalized = " ".join(period.lower().split())
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if normalized == "today":
        start, end = today, today + timedelta(days=1) - timedelta(microseconds=1)
    elif normalized == "yesterday":
        start, end = today - timedelta(days=1), today - timedelta(microseconds=1)
    elif normalized == "last week":
        this_week = today - timedelta(days=today.weekday())
        start, end = this_week - timedelta(days=7), this_week - timedelta(microseconds=1)
    elif normalized == "last month":
        this_month = today.replace(day=1)
        end = this_month - timedelta(microseconds=1)
        start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        count_match = re.search(r"(\d+)\s+(day|week|month)", normalized)
        if count_match is None:
            return None, None  # type: ignore[return-value]
        count = min(int(count_match.group(1)), 3650)
        unit = count_match.group(2)
        days = count * {"day": 1, "week": 7, "month": 30}[unit]
        start, end = now - timedelta(days=days), now
    return _isoformat(start), _isoformat(end)


def _day_start(value: str) -> str:
    return f"{value}T00:00:00Z"


def _day_end(value: str) -> str:
    return f"{value}T23:59:59.999999Z"


def _isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _remove_match(value: str, match: re.Match[str]) -> str:
    return f"{value[: match.start()]} {value[match.end() :]}"


def _strip_query_scaffolding(value: str) -> str:
    value = re.sub(r"\b(?:find|show|search(?:\s+for)?|look\s+for|retrieve)\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\b(?:my|me|please|about|memory|memories|conversation|conversations|discussion|discussions)\b", " ", value, flags=re.IGNORECASE)
    value = _LATEST_PATTERN.sub(" ", value)
    value = _HISTORICAL_PATTERN.sub(" ", value)
    value = _HANDOFF_FILTER_PATTERN.sub(" ", value)
    return " ".join(value.strip(" ,.:;!?-").split())


def _preferred_fields(
    source: str | None,
    tags: tuple[str, ...],
    thread_id: str | None,
    handoff_only: bool,
    temporal: TemporalPreference,
) -> tuple[str, ...]:
    fields = ["text"]
    if source:
        fields.append("source")
    if tags:
        fields.append("tags")
    if thread_id:
        fields.append("thread_id")
    if handoff_only:
        fields.append("handoff")
    if temporal != TemporalPreference.ANY:
        fields.append("timestamp")
    return tuple(fields)
