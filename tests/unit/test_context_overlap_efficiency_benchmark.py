from __future__ import annotations

import pytest

from memory.benchmarks.context_overlap_efficiency import assert_thresholds, run_benchmark


def test_context_overlap_benchmark_reduces_tokens_without_losing_unique_text() -> None:
    report = run_benchmark()

    assert report["before"]["chunks"] == 2
    assert report["after"]["chunks"] == 2
    assert report["after"]["tokens"] < report["before"]["tokens"]
    assert report["tokens_avoided"] > 0
    assert report["reduction_percent"] >= 1.0
    assert report["unique_evidence_preserved"] is True
    assert report["thresholds"]["failures"] == []


def test_context_overlap_benchmark_threshold_failure_is_explicit() -> None:
    report = run_benchmark(minimum_reduction_percent=100.0)

    assert report["thresholds"]["failures"]
    with pytest.raises(AssertionError):
        assert_thresholds(report)
