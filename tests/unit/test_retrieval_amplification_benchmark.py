from __future__ import annotations

from memory.benchmarks.retrieval_amplification import assert_thresholds, run_benchmark


def test_retrieval_amplification_benchmark_reduces_unfiltered_budget() -> None:
    report = run_benchmark()

    assert report["measurement"] == "deterministic_candidate_admission_budget"
    assert report["previous_unfiltered_limits"] == {
        "lexical": 50,
        "fact": 25,
        "total": 75,
    }
    assert report["optimized_unfiltered_limits"] == {
        "lexical": 15,
        "fact": 15,
        "total": 30,
    }
    assert report["filtered_limits"] == {
        "lexical": 50,
        "fact": 25,
        "total": 75,
    }
    assert report["unfiltered_candidate_budget_reduction"] == 0.6
    assert_thresholds(report)


def test_retrieval_amplification_respects_configured_hard_caps() -> None:
    report = run_benchmark(keyword_limit=4, fact_limit=2)

    assert report["optimized_unfiltered_limits"]["lexical"] == 4
    assert report["optimized_unfiltered_limits"]["fact"] == 2
