from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from memory.ingestion.query_planning import (
    RetrievalIntent,
    TemporalPreference,
    plan_retrieval_query,
)


def test_deterministic_plan_rewrites_source_and_relative_date_query() -> None:
    plan = plan_retrieval_query(
        "find my Codex discussions about pgvector from last month",
        now=datetime(2026, 10, 25, 12, tzinfo=UTC),
    )

    assert plan.original_query == "find my Codex discussions about pgvector from last month"
    assert plan.semantic_query == "pgvector"
    assert plan.filters.source == "codex"
    assert plan.filters.date_from == "2026-09-01T00:00:00Z"
    assert plan.filters.date_to == "2026-09-30T23:59:59.999999Z"
    assert plan.temporal_preference == TemporalPreference.HISTORICAL
    assert plan.preferred_fields == ("text", "source", "timestamp")


def test_deterministic_plan_covers_tags_thread_handoff_intent_and_exact_terms() -> None:
    plan = plan_retrieval_query(
        'resume the latest handoff for thread:abc-123 tagged backend about "vector filters"'
    )

    assert plan.intent == RetrievalIntent.HANDOFF
    assert plan.temporal_preference == TemporalPreference.LATEST
    assert plan.filters.thread_id == "abc-123"
    assert plan.filters.tags == ("backend",)
    assert plan.filters.handoff_only is True
    assert plan.exact_terms == ("vector filters",)


def test_explicit_filters_are_authoritative_and_tags_only_narrow() -> None:
    plan = plan_retrieval_query(
        "my Codex discussions tagged retrieval since 2026-01-01",
        explicit_filters={
            "source": "chatgpt",
            "date_from": "2026-02-01T00:00:00Z",
            "tags": ["approved"],
            "thread_id": "trusted-thread",
        },
    )

    assert plan.filters.source == "chatgpt"
    assert plan.filters.date_from == "2026-02-01T00:00:00Z"
    assert plan.filters.tags == ("retrieval", "approved")
    assert plan.filters.thread_id == "trusted-thread"


@pytest.mark.parametrize("forbidden", ["owner_id", "project_id", "memory_status", "sensitivity"])
def test_model_planner_cannot_set_trusted_scope(forbidden: str) -> None:
    def planner(_query: str, _base: object) -> dict[str, object]:
        return {forbidden: "broadened", "semantic_query": "unsafe rewrite"}

    plan = plan_retrieval_query("original query", planner=planner)

    assert plan.semantic_query == "original query"
    assert plan.planning_mode == "model_fallback"
    assert forbidden not in plan.diagnostics()


def test_valid_model_plan_is_allow_listed_and_cannot_remove_deterministic_filters() -> None:
    def planner(_query: str, _base: object) -> dict[str, object]:
        return {
            "semantic_query": "pgvector indexing",
            "intent": "fact",
            "filters": {"source": "chatgpt", "tags": ["database"]},
            "preferred_fields": ["text", "title"],
        }

    plan = plan_retrieval_query(
        "my Codex discussions tagged retrieval about indexing",
        planner=planner,
    )

    assert plan.semantic_query == "pgvector indexing"
    assert plan.intent == RetrievalIntent.FACT
    assert plan.filters.source == "codex"
    assert plan.filters.tags == ("retrieval", "database")
    assert plan.planning_mode == "model_assisted"


def test_model_planner_timeout_falls_back_without_blocking_retrieval() -> None:
    def planner(_query: str, _base: object) -> dict[str, str]:
        time.sleep(0.2)
        return {"semantic_query": "too late"}

    started = time.perf_counter()
    plan = plan_retrieval_query(
        "original query",
        planner=planner,
        planner_timeout_seconds=0.01,
    )

    assert time.perf_counter() - started < 0.15
    assert plan.semantic_query == "original query"
    assert plan.planning_mode == "model_fallback"
