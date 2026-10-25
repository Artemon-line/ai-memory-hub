from __future__ import annotations

import math
import re
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Thread
from typing import Any, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict

from memory.backend.log_safety import redact_secrets


class RerankCandidate(BaseModel):
    """Bounded projection safe to pass to an optional external reranker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    memory_id: str
    chunk_index: int
    role: str = ""
    text: str
    source: str = ""
    timestamp: str = ""
    tags: tuple[str, ...] = ()


class SemanticReranker(Protocol):
    def rerank(
        self, query: str, candidates: Sequence[RerankCandidate]
    ) -> Mapping[str, float]: ...


class TokenOverlapReranker:
    """Local deterministic reranker useful when no model service is configured."""

    def rerank(
        self, query: str, candidates: Sequence[RerankCandidate]
    ) -> Mapping[str, float]:
        query_tokens = set(_tokens(query))
        if not query_tokens:
            return {candidate.key: 0.0 for candidate in candidates}
        return {
            candidate.key: len(query_tokens.intersection(_tokens(candidate.text)))
            / len(query_tokens)
            for candidate in candidates
        }


class DeterministicRerankerFallback:
    """Neutral scores preserve fusion and deterministic metadata ordering."""

    def rerank(
        self, query: str, candidates: Sequence[RerankCandidate]
    ) -> Mapping[str, float]:
        _ = query
        return {candidate.key: 0.0 for candidate in candidates}


@dataclass(frozen=True)
class RankingResult:
    rows: list[dict[str, Any]]
    diagnostics: dict[str, Any]


def rank_candidate_lists(
    query: str,
    candidate_lists: Mapping[str, Sequence[dict[str, Any]]],
    *,
    source_weights: Mapping[str, float] | None = None,
    fusion_k: int = 60,
    exact_terms: Sequence[str] = (),
    reranker: SemanticReranker | None = None,
    reranker_timeout_seconds: float = 0.5,
    reranker_candidate_limit: int = 25,
    projection_text_limit: int = 2000,
    deterministic_boosts: Mapping[str, float] | None = None,
) -> RankingResult:
    if fusion_k < 1:
        raise ValueError("fusion_k must be positive")
    if reranker_candidate_limit < 1:
        raise ValueError("reranker_candidate_limit must be positive")
    if projection_text_limit < 1:
        raise ValueError("projection_text_limit must be positive")

    fused, source_counts = _reciprocal_rank_fusion(
        candidate_lists,
        source_weights=source_weights or {},
        fusion_k=fusion_k,
    )
    exact_priorities = {
        _candidate_key(row): _exact_priority(query, row, exact_terms=exact_terms, rows=fused)
        for row in fused
    }
    exact_ordered = sorted(
        fused,
        key=lambda row: (
            exact_priorities[_candidate_key(row)],
            -float(row["_fusion_score"]),
            _stable_row_key(row),
        ),
    )

    bounded_rows = exact_ordered[:reranker_candidate_limit]
    projections = [
        _candidate_projection(row, text_limit=projection_text_limit) for row in bounded_rows
    ]
    scores, reranker_mode = _reranker_scores(
        redact_secrets(query)[:projection_text_limit],
        projections,
        reranker=reranker,
        timeout_seconds=reranker_timeout_seconds,
    )
    boosts = deterministic_boosts or {}
    semantically_reranked = (
        {candidate.key for candidate in projections} if reranker_mode == "semantic" else set()
    )
    ranked = sorted(
        exact_ordered,
        key=lambda row: (
            exact_priorities[_candidate_key(row)],
            0 if _candidate_key(row) in semantically_reranked else 1,
            -scores.get(_candidate_key(row), 0.0),
            -float(boosts.get(_candidate_key(row), 0.0)),
            -float(row["_fusion_score"]),
            _stable_row_key(row),
        ),
    )
    for rank, row in enumerate(ranked):
        key = _candidate_key(row)
        row["_exact_priority"] = exact_priorities[key]
        row["_reranker_score"] = scores.get(key, 0.0)
        row["_deterministic_boost"] = float(boosts.get(key, 0.0))
        row["_ranking_score"] = float(rank)
        row["_ranking_position"] = rank
        row["_ranking_boost"] = float(row["_fusion_score"])

    return RankingResult(
        rows=ranked,
        diagnostics={
            "method": "reciprocal_rank_fusion",
            "fusion_k": fusion_k,
            "source_candidate_counts": source_counts,
            "fused_candidate_count": len(fused),
            "reranker": reranker_mode,
            "reranker_candidate_count": len(projections),
            "reranker_candidate_limit": reranker_candidate_limit,
            "projection_text_limit": projection_text_limit,
        },
    )


def _reciprocal_rank_fusion(
    candidate_lists: Mapping[str, Sequence[dict[str, Any]]],
    *,
    source_weights: Mapping[str, float],
    fusion_k: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows_by_key: dict[str, dict[str, Any]] = {}
    scores: dict[str, float] = {}
    sources: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for source, rows in candidate_lists.items():
        weight = max(0.0, float(source_weights.get(source, 1.0)))
        seen: set[str] = set()
        accepted = 0
        for rank, row in enumerate(rows, start=1):
            key = _candidate_key(row)
            if not key or key in seen:
                continue
            seen.add(key)
            accepted += 1
            if key not in rows_by_key:
                rows_by_key[key] = dict(row)
            scores[key] = scores.get(key, 0.0) + weight / (fusion_k + rank)
            sources.setdefault(key, []).append(source)
        counts[source] = accepted

    fused: list[dict[str, Any]] = []
    for key, row in rows_by_key.items():
        row["_fusion_score"] = scores[key]
        row["_candidate_sources"] = tuple(sources[key])
        fused.append(row)
    fused.sort(key=lambda row: (-float(row["_fusion_score"]), _stable_row_key(row)))
    return fused, counts


def _reranker_scores(
    query: str,
    candidates: Sequence[RerankCandidate],
    *,
    reranker: SemanticReranker | None,
    timeout_seconds: float,
) -> tuple[dict[str, float], str]:
    fallback = DeterministicRerankerFallback()
    if reranker is None:
        return dict(fallback.rerank(query, candidates)), "deterministic_fallback"
    if timeout_seconds <= 0:
        raise ValueError("reranker_timeout_seconds must be positive")

    result_queue: Queue[tuple[bool, Any]] = Queue(maxsize=1)

    def invoke() -> None:
        try:
            result_queue.put((True, reranker.rerank(query, candidates)))
        except Exception as exc:  # pragma: no cover - converted to deterministic fallback
            result_queue.put((False, exc))

    Thread(target=invoke, name="memory-semantic-reranker", daemon=True).start()
    try:
        succeeded, raw_scores = result_queue.get(timeout=timeout_seconds)
    except Empty:
        return dict(fallback.rerank(query, candidates)), "timeout_fallback"
    if not succeeded or not isinstance(raw_scores, Mapping):
        return dict(fallback.rerank(query, candidates)), "error_fallback"

    allowed = {candidate.key for candidate in candidates}
    parsed: dict[str, float] = {}
    for key, value in raw_scores.items():
        normalized_key = str(key)
        if normalized_key not in allowed or isinstance(value, bool):
            return dict(fallback.rerank(query, candidates)), "invalid_output_fallback"
        try:
            score = float(value)
        except (TypeError, ValueError):
            return dict(fallback.rerank(query, candidates)), "invalid_output_fallback"
        if not math.isfinite(score):
            return dict(fallback.rerank(query, candidates)), "invalid_output_fallback"
        parsed[normalized_key] = score
    return parsed, "semantic"


def _candidate_projection(row: Mapping[str, Any], *, text_limit: int) -> RerankCandidate:
    conversation = row.get("conversation")
    if not isinstance(conversation, Mapping):
        conversation = {}
    metadata = conversation.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    raw_tags = metadata.get("tags", ())
    tags = ()
    if isinstance(raw_tags, Sequence) and not isinstance(raw_tags, str):
        tags = tuple(str(tag)[:128] for tag in raw_tags if str(tag))[:20]
    return RerankCandidate(
        key=_candidate_key(row),
        memory_id=str(row.get("id", "")),
        chunk_index=int(row.get("chunk_index", 0)),
        role=str(row.get("role", ""))[:32],
        text=redact_secrets(str(row.get("text", "")))[:text_limit],
        source=str(conversation.get("source", ""))[:128],
        timestamp=str(conversation.get("timestamp", ""))[:64],
        tags=tags,
    )


def _exact_priority(
    query: str,
    row: Mapping[str, Any],
    *,
    exact_terms: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> int:
    query_folded = query.casefold()
    memory_id = str(row.get("id", "")).casefold()
    if memory_id and memory_id in query_folded:
        return 0
    text = str(row.get("text", "")).casefold()
    if any(term.strip() and term.strip().casefold() in text for term in exact_terms):
        return 1
    rare_terms = _rare_exact_terms(query, rows)
    if any(term in set(_tokens(text)) for term in rare_terms):
        return 2
    return 3


def _rare_exact_terms(query: str, rows: Sequence[Mapping[str, Any]]) -> set[str]:
    query_tokens = set(_tokens(query))
    if not query_tokens:
        return set()
    document_frequency = {
        token: sum(token in set(_tokens(str(row.get("text", "")))) for row in rows)
        for token in query_tokens
    }
    ceiling = max(1, len(rows) // 4)
    return {
        token
        for token, frequency in document_frequency.items()
        if frequency <= ceiling and (len(token) >= 12 or any(char.isdigit() for char in token))
    }


def _candidate_key(row: Mapping[str, Any]) -> str:
    memory_id = str(row.get("id", ""))
    if not memory_id:
        return ""
    return f"{memory_id}:{int(row.get('chunk_index', 0))}"


def _stable_row_key(row: Mapping[str, Any]) -> tuple[str, int, str]:
    return (
        str(row.get("id", "")),
        int(row.get("chunk_index", 0)),
        str(row.get("text", "")),
    )


def _tokens(value: str) -> list[str]:
    return [
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", value)
        if len(token) >= 2
    ]
