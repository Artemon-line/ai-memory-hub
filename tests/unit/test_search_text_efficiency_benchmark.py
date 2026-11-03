from __future__ import annotations

import pytest

from memory.benchmarks.search_text_efficiency import assert_thresholds, run_benchmark
from memory.ingestion import mvp_ingestion


def test_search_text_efficiency_reuses_each_source_conversation_once() -> None:
    report = run_benchmark()

    assert report["measurement"] == "deterministic_work_amplification"
    assert report["fixture"] == {
        "facts": 120,
        "unique_source_conversations": 3,
        "results": 120,
    }
    assert report["previous_path"]["conversation_search_text_constructions"] == 120
    assert report["optimized_path"] == {
        "conversation_search_text_constructions": 3,
        "request_local_cache_entries": 3,
    }
    assert report["reductions"]["conversation_search_text_constructions"] == 0.975
    assert_thresholds(report)


def test_search_text_efficiency_counts_reconstruction_not_cache_cardinality(
    monkeypatch,
) -> None:
    original = mvp_ingestion._conversation_search_text_with_cache

    def rebuilding(conversation, *, cache):
        if cache is not None:
            cache.clear()
        return original(conversation, cache=cache)

    monkeypatch.setattr(
        mvp_ingestion, "_conversation_search_text_with_cache", rebuilding
    )

    report = run_benchmark()

    assert report["optimized_path"]["request_local_cache_entries"] == 1
    assert report["optimized_path"]["conversation_search_text_constructions"] == 120
    with pytest.raises(AssertionError, match="constructed once"):
        assert_thresholds(report)
