from __future__ import annotations

from memory.benchmarks.reranker_efficiency import assert_thresholds, run_benchmark


def test_reranker_efficiency_benchmark_skips_single_candidate_calls() -> None:
    report = run_benchmark()

    assert report["measurement"] == "deterministic_reranker_call_amplification"
    assert report["single_candidate"] == {
        "previous_calls": 1,
        "optimized_calls": 0,
        "optimized_candidates_sent": 0,
        "diagnostic_mode": "insufficient_candidates",
        "result_ids": ["only"],
    }
    assert report["single_candidate_call_reduction"] == 1.0
    assert report["multi_candidate"]["optimized_calls"] == 1
    assert report["multi_candidate"]["optimized_candidates_sent"] == 2
    assert report["multi_candidate"]["diagnostic_mode"] == "semantic"
    assert_thresholds(report)
