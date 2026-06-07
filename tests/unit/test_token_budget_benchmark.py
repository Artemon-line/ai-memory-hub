from __future__ import annotations

import pytest

from memory.benchmarks.token_budget import assert_thresholds, run_benchmark


def test_token_budget_benchmark_reports_latency_and_context_metrics() -> None:
    report = run_benchmark(iterations=2, budget=80)

    assert report["iterations"] == 2
    assert report["budget"] == 80
    assert report["latency_ms"]["default_unbudgeted_avg"] >= 0
    assert report["latency_ms"]["budgeted_request_avg"] >= 0
    assert report["latency_ms"]["config_enabled_avg"] >= 0
    assert report["selected_context"]["default_chunks"] == 8
    assert report["selected_context"]["budgeted_tokens"] <= 80
    assert report["selected_context"]["config_enabled_tokens"] <= 80
    assert report["chunking"]["message_strategy_chunks"] == 1
    assert report["chunking"]["token_strategy_chunks"] > 1
    assert report["thresholds"]["failures"] == []


def test_token_budget_benchmark_threshold_failures_are_explicit() -> None:
    report = run_benchmark(
        iterations=1,
        budget=80,
        max_budgeted_ratio=0.0,
        max_config_enabled_ratio=0.0,
    )

    assert report["thresholds"]["failures"]
    with pytest.raises(AssertionError):
        assert_thresholds(report)
