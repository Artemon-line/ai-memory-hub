from __future__ import annotations

from memory.benchmarks.search_text_efficiency import assert_thresholds, run_benchmark


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
