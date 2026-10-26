from __future__ import annotations

import time
from typing import Any, Sequence

from memory.ingestion.retrieval_ranking import (
    RerankCandidate,
    TokenOverlapReranker,
    rank_candidate_lists,
)


def _row(memory_id: str, text: str, *, score: float = 0.0) -> dict[str, Any]:
    return {
        "id": memory_id,
        "chunk_index": 0,
        "role": "user",
        "text": text,
        "score": score,
        "conversation": {
            "id": memory_id,
            "source": "codex",
            "timestamp": "2026-01-01T00:00:00Z",
            "metadata": {"tags": ["retrieval"], "private": "must-not-leak"},
        },
    }


def test_rank_fusion_is_independent_of_provider_score_scale() -> None:
    first = _row("a", "alpha", score=0.001)
    second = _row("b", "beta", score=0.002)
    lexical = [_row("b", "beta", score=999_999), _row("a", "alpha", score=-999_999)]

    baseline = rank_candidate_lists("query", {"dense": [first, second], "lexical": lexical})
    first["score"] = 10_000_000
    second["score"] = -10_000_000
    changed = rank_candidate_lists("query", {"dense": [first, second], "lexical": lexical})

    assert [row["id"] for row in baseline.rows] == [row["id"] for row in changed.rows]
    assert baseline.diagnostics["method"] == "reciprocal_rank_fusion"


def test_exact_identifier_and_phrase_are_high_signal() -> None:
    identifier = "11111111-1111-4111-8111-111111111111"
    rows = rank_candidate_lists(
        f"compare {identifier}",
        {
            "dense": [
                _row("ordinary", "generic result"),
                _row(identifier, "identifier result"),
            ]
        },
    ).rows
    phrase_rows = rank_candidate_lists(
        "camera settings",
        {
            "dense": [
                _row("ordinary", "generic result"),
                _row("phrase", "the exact velvet lantern phrase"),
            ]
        },
        exact_terms=("velvet lantern",),
    ).rows

    assert rows[0]["id"] == identifier
    assert phrase_rows[0]["id"] == "phrase"


def test_timeout_uses_deterministic_fallback_and_stable_ordering() -> None:
    class SlowReranker:
        def rerank(
            self, _query: str, _candidates: Sequence[RerankCandidate]
        ) -> dict[str, float]:
            time.sleep(0.2)
            return {}

    candidate_lists = {
        "dense": [_row("b", "same"), _row("a", "same")],
        "lexical": [_row("a", "same"), _row("b", "same")],
    }
    started = time.perf_counter()
    first = rank_candidate_lists(
        "same",
        candidate_lists,
        reranker=SlowReranker(),
        reranker_timeout_seconds=0.01,
    )
    second = rank_candidate_lists("same", candidate_lists)

    assert time.perf_counter() - started < 0.15
    assert first.diagnostics["reranker"] == "timeout_fallback"
    assert [row["id"] for row in first.rows] == [row["id"] for row in second.rows]
    assert [row["id"] for row in first.rows] == ["a", "b"]


def test_reranker_receives_bounded_redacted_projection_only() -> None:
    class CapturingReranker:
        def __init__(self) -> None:
            self.candidates: Sequence[RerankCandidate] = ()
            self.query = ""

        def rerank(
            self, query: str, candidates: Sequence[RerankCandidate]
        ) -> dict[str, float]:
            self.query = query
            self.candidates = candidates
            return {candidate.key: 1.0 for candidate in candidates}

    reranker = CapturingReranker()
    rank_candidate_lists(
        "deploy api_key=query-secret " + "context " * 100,
        {
            "dense": [
                _row("a", "deploy api_key=super-secret " + "detail " * 100),
                _row("b", "deploy second"),
            ]
        },
        reranker=reranker,
        reranker_candidate_limit=2,
        projection_text_limit=40,
    )

    assert len(reranker.candidates) == 2
    projection = reranker.candidates[0]
    assert len(projection.text) <= 40
    assert "super-secret" not in projection.text
    assert "api_key=***" in projection.text
    assert not hasattr(projection, "conversation")
    assert "must-not-leak" not in projection.model_dump_json()
    assert len(reranker.query) <= 40
    assert "query-secret" not in reranker.query


def test_rare_exact_term_is_high_signal() -> None:
    result = rank_candidate_lists(
        "find incident ZXCV-48291",
        {
            "dense": [
                _row("generic", "general incident review"),
                _row("exact", "resolution for ZXCV-48291"),
            ],
            "lexical": [_row("generic", "general incident review")],
        },
    )

    assert result.rows[0]["id"] == "exact"


def test_fused_ranks_keep_nearby_conversation_chunks_together() -> None:
    from memory.ingestion.mvp_ingestion import group_conversation_results

    first = _row("conversation-a", "first")
    first["chunk_index"] = 0
    second = _row("conversation-b", "second")
    third = _row("conversation-a", "third")
    third["chunk_index"] = 1
    ranked = rank_candidate_lists("unmatched", {"dense": [first, second, third]}).rows

    grouped = group_conversation_results(ranked)

    assert [row["id"] for row in grouped] == [
        "conversation-a",
        "conversation-a",
        "conversation-b",
    ]


def test_local_token_overlap_reranker_is_a_concrete_implementation() -> None:
    result = rank_candidate_lists(
        "pgvector indexing",
        {
            "dense": [
                _row("generic", "general database note"),
                _row("relevant", "pgvector indexing details"),
            ]
        },
        reranker=TokenOverlapReranker(),
    )

    assert result.rows[0]["id"] == "relevant"
    assert result.diagnostics["reranker"] == "semantic"


def test_semantic_reranker_batch_limit_keeps_unseen_tail_below_batch() -> None:
    class NegativeReranker:
        def rerank(
            self, _query: str, candidates: Sequence[RerankCandidate]
        ) -> dict[str, float]:
            return {candidate.key: -1.0 for candidate in candidates}

    result = rank_candidate_lists(
        "unmatched",
        {"dense": [_row("first", "first"), _row("tail", "tail")]},
        reranker=NegativeReranker(),
        reranker_candidate_limit=1,
    )

    assert [row["id"] for row in result.rows] == ["first", "tail"]
    assert result.diagnostics["reranker"] == "insufficient_candidates"


def test_semantic_reranker_is_skipped_when_only_one_candidate_can_be_ranked() -> None:
    class FailingReranker:
        def rerank(
            self, query: str, candidates: Sequence[RerankCandidate]
        ) -> dict[str, float]:
            _ = query, candidates
            raise AssertionError("single-candidate reranker must not be called")

    result = rank_candidate_lists(
        "only candidate",
        {"dense": [_row("only", "only candidate")]},
        reranker=FailingReranker(),
    )

    assert [row["id"] for row in result.rows] == ["only"]
    assert result.diagnostics["reranker"] == "insufficient_candidates"
    assert result.diagnostics["reranker_candidate_count"] == 1

    empty = rank_candidate_lists(
        "no candidates",
        {"dense": []},
        reranker=FailingReranker(),
    )

    assert empty.rows == []
    assert empty.diagnostics["reranker"] == "insufficient_candidates"
    assert empty.diagnostics["reranker_candidate_count"] == 0
