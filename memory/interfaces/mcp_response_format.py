from __future__ import annotations

from enum import StrEnum
from typing import Any

from memory.ingestion.fact_timeline import FactField
from memory.ingestion.thread_models import MCPResponseFormat, ThreadMetadataKey, ThreadResultKey


class MCPPayloadKey(StrEnum):
    ANSWER = "answer"
    ANSWER_BASIS = "answer_basis"
    CITATION = "citation"
    CITATION_COUNT = "citation_count"
    CONFIDENCE = "confidence"
    CONFIDENCE_REASON = "confidence_reason"
    FACT_COUNT = "fact_count"
    FACTS = "facts"
    LATEST = "latest"
    MEMORY = "memory"
    MEMORY_RESULT_COUNT = "memory_result_count"
    MESSAGES = "messages"
    OBJECT = "object"
    OBJECT_NORMALIZED = "object_normalized"
    OMITTED_MESSAGES = "omitted_messages"
    RESULTS = "results"
    STATUS = "status"
    SUMMARY = "summary"
    TOTAL = "total"
    UNIQUE = "unique"
    RETURNED = "returned"
    OMITTED = "omitted"
    LIMIT = "limit"


_SEARCH_ROW_KEYS: tuple[str | StrEnum, ...] = (
    "id",
    "score",
    "text",
    "role",
    "chunk_index",
    "matching_chunks",
    "evidence_chunks",
    "used_in_answer",
)
_THREAD_ROW_KEYS: tuple[str | StrEnum, ...] = (
    ThreadResultKey.THREAD_ID.value,
    ThreadResultKey.THREAD_CONVERSATION_IDS.value,
    ThreadResultKey.THREAD_CONVERSATION_COUNT.value,
    ThreadResultKey.MATCHING_CONVERSATIONS.value,
)
_FACT_KEYS: tuple[str | StrEnum, ...] = (
    FactField.ID,
    FactField.SUBJECT,
    FactField.PREDICATE,
    FactField.OBJECT,
    FactField.OBJECT_NORMALIZED,
    FactField.CONFIDENCE,
    FactField.SOURCE_QUALITY,
    FactField.LAST_CONFIRMED_AT,
    FactField.STORED_AT,
    FactField.AUTHOR,
    FactField.SOURCE_CONVERSATION_ID,
)
_PROFILE_SUMMARY_KEYS: tuple[str | StrEnum, ...] = (
    "text",
    "active_fact_count",
    "freshest_at",
    "confidence_counts",
    "source_quality_counts",
)
_CHUNK_EVIDENCE_KEYS: tuple[str | StrEnum, ...] = (
    "type",
    "conversation_id",
    "chunk_index",
    "role",
    "text",
    "score",
    "used_in_answer",
)
_CITATION_KEYS: tuple[str | StrEnum, ...] = (
    "id",
    "chunk_index",
    "score",
    "text",
    "fact_id",
    FactField.PREDICATE,
    FactField.SOURCE_QUALITY,
    FactField.CONFIDENCE_REASON,
    FactField.LAST_CONFIRMED_AT,
    FactField.STORED_AT,
    FactField.AUTHOR,
    "save_intent",
    "save_intent_source",
)
DEFAULT_CONCISE_FACT_LIMIT = 10
_DEFAULT_MEMORY_STATUS = "active"
_MEMORY_STATUS_KEY = "memory_status"
_CONCISE_SEARCH_TEXT_LIMIT = 800
_CONCISE_SEARCH_EVIDENCE_TEXT_LIMIT = 500
_CONCISE_SEARCH_KEYS: tuple[str | StrEnum, ...] = (
    MCPPayloadKey.STATUS,
    "cursor",
    MCPPayloadKey.TOTAL,
    MCPPayloadKey.UNIQUE,
    MCPPayloadKey.RETURNED,
    MCPPayloadKey.OMITTED,
    MCPPayloadKey.LIMIT,
)
_CONCISE_ASK_KEYS: tuple[str | StrEnum, ...] = (
    MCPPayloadKey.STATUS,
    MCPPayloadKey.ANSWER,
    MCPPayloadKey.CONFIDENCE,
    MCPPayloadKey.CONFIDENCE_REASON,
    MCPPayloadKey.ANSWER_BASIS,
    MCPPayloadKey.LATEST,
)
_CONCISE_RETRIEVE_MESSAGE_LIMIT = 6
_CONCISE_RETRIEVE_MESSAGE_TEXT_LIMIT = 800
_CONCISE_RETRIEVE_METADATA_KEYS = (_MEMORY_STATUS_KEY,)


def format_search_response(
    payload: dict[str, Any], response_format: str
) -> dict[str, Any]:
    if response_format == MCPResponseFormat.DETAILED.value:
        return payload
    formatted = _compact_mapping(payload, _CONCISE_SEARCH_KEYS)
    results = payload.get("results", [])
    formatted["results"] = [
        _concise_search_row(row) for row in results if isinstance(row, dict)
    ] if isinstance(results, list) else []
    return formatted


def format_retrieve_response(
    payload: dict[str, Any], response_format: str
) -> dict[str, Any]:
    if response_format == MCPResponseFormat.DETAILED.value:
        return payload
    formatted = _compact_mapping(payload, ("status", "id"))
    memory = payload.get("memory")
    if isinstance(memory, dict):
        formatted["memory"] = _concise_retrieved_memory(memory)
    return formatted


def format_ask_response(payload: dict[str, Any], response_format: str) -> dict[str, Any]:
    if response_format == MCPResponseFormat.DETAILED.value:
        return payload
    formatted = _compact_mapping(payload, _CONCISE_ASK_KEYS)
    formatted.setdefault("status", payload.get("status", "ok"))
    formatted["memory_result_count"] = _list_count(payload.get("results"))
    formatted["fact_count"] = _list_count(payload.get("facts"))
    formatted["citation_count"] = _list_count(payload.get("citations"))
    return formatted


def format_fact_search_response(
    payload: dict[str, Any], response_format: str, *, limit: int | None = None
) -> dict[str, Any]:
    if response_format == MCPResponseFormat.DETAILED.value:
        return payload
    formatted = {"status": payload.get("status", "ok")}
    results, counts = _limited_concise_facts(payload.get("results"), limit=limit)
    formatted["results"] = results
    formatted.update(
        {
            "total_results": counts["total"],
            "unique_results": counts["unique"],
            "returned_results": counts["returned"],
            "omitted_results": counts["omitted"],
            "result_limit": counts["limit"],
        }
    )
    return formatted


def format_profile_response(
    payload: dict[str, Any], response_format: str, *, limit: int | None = None
) -> dict[str, Any]:
    if response_format == MCPResponseFormat.DETAILED.value:
        return payload
    formatted = {
        "status": payload.get("status", "ok"),
        "subject": payload.get("subject"),
    }
    summary = payload.get("summary")
    if isinstance(summary, dict):
        formatted["summary"] = _compact_mapping(summary, _PROFILE_SUMMARY_KEYS)
    facts, counts = _limited_concise_facts(payload.get("facts"), limit=limit)
    formatted["facts"] = facts
    formatted.update(
        {
            "total_facts": counts["total"],
            "unique_facts": counts["unique"],
            "returned_facts": counts["returned"],
            "omitted_facts": counts["omitted"],
            "fact_limit": counts["limit"],
        }
    )
    return formatted


def _concise_search_row(row: dict[str, Any]) -> dict[str, Any]:
    concise = _compact_mapping(row, (*_SEARCH_ROW_KEYS, *_THREAD_ROW_KEYS))
    if concise.get("text") is not None:
        concise["text"] = _truncate_text(
            str(concise["text"]), limit=_CONCISE_SEARCH_TEXT_LIMIT
        )
    if "matching_chunks" not in concise and "conversation_match_count" in row:
        concise["matching_chunks"] = row["conversation_match_count"]
    evidence_chunks = concise.get("evidence_chunks")
    if isinstance(evidence_chunks, list):
        concise["evidence_chunks"] = _concise_search_evidence_chunks(evidence_chunks)
    concise["citation"] = _conversation_citation(
        row.get("conversation"), fallback_id=row.get("id")
    )
    return concise


def _concise_search_evidence_chunks(
    evidence_chunks: list[Any],
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for chunk in evidence_chunks:
        if not isinstance(chunk, dict):
            continue
        concise = _compact_mapping(chunk, _CITATION_KEYS)
        if concise.get("text") is not None:
            concise["text"] = _truncate_text(
                str(concise["text"]), limit=_CONCISE_SEARCH_EVIDENCE_TEXT_LIMIT
            )
        chunks.append(concise)
    return chunks


def _conversation_citation(conversation: Any, *, fallback_id: Any) -> dict[str, Any]:
    if not isinstance(conversation, dict):
        return {"id": fallback_id}
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    citation = {
        "id": conversation.get("id", fallback_id),
        "source": conversation.get("source"),
        "title": conversation.get("title"),
        "timestamp": conversation.get("timestamp"),
        "thread_id": metadata.get(ThreadMetadataKey.THREAD_ID.value),
    }
    summary_text = _conversation_summary_text(metadata)
    if summary_text:
        citation["summary"] = summary_text
    return _drop_empty_values(citation)


def _conversation_summary_text(metadata: dict[str, Any]) -> str | None:
    generated_summary = metadata.get("generated_summary")
    if isinstance(generated_summary, dict) and generated_summary.get("text"):
        return str(generated_summary["text"])
    summary = metadata.get("summary")
    return str(summary) if summary else None


def _concise_retrieved_memory(memory: dict[str, Any]) -> dict[str, Any]:
    concise = _conversation_citation(memory, fallback_id=memory.get("id"))
    metadata = memory.get("metadata")
    if isinstance(metadata, dict):
        concise.update(_compact_mapping(metadata, _CONCISE_RETRIEVE_METADATA_KEYS))
    concise.setdefault(_MEMORY_STATUS_KEY, _DEFAULT_MEMORY_STATUS)
    messages = memory.get("messages")
    if not isinstance(messages, list):
        return concise
    compact_messages = [
        _concise_message(message) for message in messages if isinstance(message, dict)
    ]
    concise["message_count"] = len(compact_messages)
    concise["messages"] = compact_messages[:_CONCISE_RETRIEVE_MESSAGE_LIMIT]
    omitted = len(compact_messages) - len(concise["messages"])
    if omitted > 0:
        concise["omitted_messages"] = omitted
    return concise


def _concise_message(message: dict[str, Any]) -> dict[str, Any]:
    text = message.get("text")
    concise = _compact_mapping(message, ("role",))
    if text is not None:
        concise["text"] = _truncate_text(
            str(text),
            limit=_CONCISE_RETRIEVE_MESSAGE_TEXT_LIMIT,
        )
    return concise


def _concise_fact(fact: dict[str, Any]) -> dict[str, Any]:
    concise = _compact_mapping(fact, _FACT_KEYS)
    if FactField.ID.value not in concise and fact.get("fact_id") is not None:
        concise[FactField.ID.value] = fact["fact_id"]
    if FactField.OBJECT.value not in concise and fact.get(FactField.OBJECT_RAW.value) is not None:
        concise[FactField.OBJECT.value] = fact[FactField.OBJECT_RAW.value]
    if (
        FactField.OBJECT_NORMALIZED.value not in concise
        and concise.get(FactField.OBJECT.value) is not None
    ):
        concise[FactField.OBJECT_NORMALIZED.value] = concise[FactField.OBJECT.value]
    concise["superseded"] = bool(
        fact.get(FactField.SUPERSEDED_BY.value) or fact.get(FactField.SUPERSEDED_AT.value)
    )
    if fact.get(FactField.SUPERSEDED_BY.value) is not None:
        concise[FactField.SUPERSEDED_BY.value] = fact[FactField.SUPERSEDED_BY.value]
    if fact.get(FactField.SUPERSEDED_AT.value) is not None:
        concise[FactField.SUPERSEDED_AT.value] = fact[FactField.SUPERSEDED_AT.value]
    if fact.get(FactField.DELETED_AT.value) is not None:
        concise[FactField.DELETED_AT.value] = fact[FactField.DELETED_AT.value]
    return concise


def _concise_evidence(item: dict[str, Any]) -> dict[str, Any]:
    evidence_type = item.get("type")
    if evidence_type == "fact":
        concise = _concise_fact(item)
        concise["type"] = "fact"
        if item.get("fact_id") is not None:
            concise["fact_id"] = item["fact_id"]
        if item.get("used_in_answer") is not None:
            concise["used_in_answer"] = item["used_in_answer"]
        return concise
    if evidence_type == "chunk":
        return _compact_mapping(item, _CHUNK_EVIDENCE_KEYS)
    return {
        key: value
        for key, value in item.items()
        if key not in {"conversation", "memory", "metadata"}
    }


def _concise_structured_evidence(
    structured_evidence: dict[str, Any]
) -> dict[str, Any]:
    concise: dict[str, Any] = {}
    facts = structured_evidence.get("facts")
    if isinstance(facts, list):
        concise["facts"] = [
            _concise_evidence(fact) for fact in facts if isinstance(fact, dict)
        ]
    results = structured_evidence.get("results")
    if isinstance(results, list):
        concise["results"] = [
            _concise_search_row(row) for row in results if isinstance(row, dict)
        ]
    return concise


def _compact_mapping(
    keys_source: dict[str, Any],
    keys: tuple[str | StrEnum, ...],
) -> dict[str, Any]:
    return {
        key_value: keys_source[key_value]
        for key in keys
        for key_value in (_payload_key(key),)
        if key_value in keys_source and keys_source[key_value] is not None
    }


def _payload_key(key: str | StrEnum) -> str:
    return key.value if isinstance(key, StrEnum) else key


def _drop_empty_values(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


def _truncate_text(text: str, *, limit: int) -> str:
    value = " ".join(text.strip().split())
    if len(value) <= limit:
        return value
    suffix = "..."
    return value[: limit - len(suffix)].rstrip() + suffix


def _limited_concise_facts(
    facts: Any, *, limit: int | None
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    compact = [
        _concise_fact(fact) for fact in facts if isinstance(fact, dict)
    ] if isinstance(facts, list) else []
    deduped = _dedupe_concise_facts(compact)
    effective_limit = limit if limit is not None else DEFAULT_CONCISE_FACT_LIMIT
    limited = deduped[:effective_limit]
    return limited, {
        "total": len(compact),
        "unique": len(deduped),
        "returned": len(limited),
        "omitted": max(len(deduped) - len(limited), 0),
        "limit": effective_limit,
    }


def _dedupe_concise_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for fact in facts:
        key = (
            str(fact.get(FactField.SUBJECT.value, "")),
            str(fact.get(FactField.PREDICATE.value, "")),
            str(
                fact.get(FactField.OBJECT_NORMALIZED.value)
                or fact.get(FactField.OBJECT.value, "")
            ),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(fact)
    return deduped


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0
