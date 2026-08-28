from __future__ import annotations

from typing import Any

from memory.ingestion.thread_models import MCPResponseFormat, ThreadMetadataKey, ThreadResultKey

_SEARCH_ROW_KEYS = (
    "id",
    "score",
    "text",
    "role",
    "chunk_index",
    "matching_chunks",
    "evidence_chunks",
    "used_in_answer",
)
_THREAD_ROW_KEYS = (
    ThreadResultKey.THREAD_ID.value,
    ThreadResultKey.THREAD_CONVERSATION_IDS.value,
    ThreadResultKey.THREAD_CONVERSATION_COUNT.value,
    ThreadResultKey.MATCHING_CONVERSATIONS.value,
)
_FACT_KEYS = (
    "id",
    "subject",
    "predicate",
    "object",
    "object_normalized",
    "confidence",
    "source_quality",
    "last_confirmed_at",
    "source_conversation_id",
)
_PROFILE_SUMMARY_KEYS = (
    "text",
    "active_fact_count",
    "freshest_at",
    "confidence_counts",
    "source_quality_counts",
)
_CHUNK_EVIDENCE_KEYS = (
    "type",
    "conversation_id",
    "chunk_index",
    "role",
    "text",
    "score",
    "used_in_answer",
)
_CITATION_KEYS = (
    "id",
    "chunk_index",
    "score",
    "text",
    "fact_id",
    "predicate",
    "source_quality",
    "confidence_reason",
    "last_confirmed_at",
    "save_intent",
    "save_intent_source",
)
DEFAULT_CONCISE_FACT_LIMIT = 10
_CONCISE_ASK_KEYS = (
    "status",
    "answer",
    "confidence",
    "confidence_reason",
    "answer_basis",
)


def format_search_response(
    payload: dict[str, Any], response_format: str
) -> dict[str, Any]:
    if response_format == MCPResponseFormat.DETAILED.value:
        return payload
    formatted = dict(payload)
    results = payload.get("results", [])
    formatted["results"] = [
        _concise_search_row(row) for row in results if isinstance(row, dict)
    ] if isinstance(results, list) else []
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
    if "matching_chunks" not in concise and "conversation_match_count" in row:
        concise["matching_chunks"] = row["conversation_match_count"]
    evidence_chunks = concise.get("evidence_chunks")
    if isinstance(evidence_chunks, list):
        concise["evidence_chunks"] = [
            _compact_mapping(chunk, _CITATION_KEYS)
            for chunk in evidence_chunks
            if isinstance(chunk, dict)
        ]
    concise["citation"] = _conversation_citation(
        row.get("conversation"), fallback_id=row.get("id")
    )
    return concise


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


def _concise_fact(fact: dict[str, Any]) -> dict[str, Any]:
    concise = _compact_mapping(fact, _FACT_KEYS)
    if "id" not in concise and fact.get("fact_id") is not None:
        concise["id"] = fact["fact_id"]
    if "object" not in concise and fact.get("object_raw") is not None:
        concise["object"] = fact["object_raw"]
    if "object_normalized" not in concise and concise.get("object") is not None:
        concise["object_normalized"] = concise["object"]
    concise["superseded"] = bool(fact.get("superseded_by") or fact.get("superseded_at"))
    if fact.get("superseded_by") is not None:
        concise["superseded_by"] = fact["superseded_by"]
    if fact.get("superseded_at") is not None:
        concise["superseded_at"] = fact["superseded_at"]
    if fact.get("deleted_at") is not None:
        concise["deleted_at"] = fact["deleted_at"]
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


def _compact_mapping(keys_source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {
        key: keys_source[key]
        for key in keys
        if key in keys_source and keys_source[key] is not None
    }


def _drop_empty_values(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}


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
            str(fact.get("subject", "")),
            str(fact.get("predicate", "")),
            str(fact.get("object_normalized") or fact.get("object", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(fact)
    return deduped


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0
