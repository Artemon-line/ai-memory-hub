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
    assert report["latency_ms"]["default_unbudgeted"]["first_call"] >= 0
    assert report["latency_ms"]["default_unbudgeted"]["warm_p50"] >= 0
    assert report["latency_ms"]["default_unbudgeted"]["warm_p95"] >= 0
    assert report["latency_ms"]["default_unbudgeted"]["warm_p99"] >= 0
    assert report["selected_context"]["default_chunks"] == 8
    assert report["selected_context"]["budgeted_tokens"] <= 80
    assert report["selected_context"]["config_enabled_tokens"] <= 80
    assert report["chunking"]["message_strategy_chunks"] == 1
    assert report["chunking"]["token_strategy_chunks"] > 1
    assert report["work_amplification"]["default_unbudgeted"]["warm"]["per_request"] == {
        "search_calls": 1.0,
        "candidate_rows": 8.0,
        "audit_writes": 1.0,
    }
    assert report["work_amplification"]["budgeted_request"]["warm"]["per_request"] == {
        "search_calls": 1.0,
        "candidate_rows": 8.0,
        "audit_writes": 1.0,
    }
    assert report["work_amplification"]["default_unbudgeted"]["result"] == {
        "returned_results": 8,
        "selected_chunks": 8,
        "dropped_chunks": 0,
        "context_tokens": 0,
        "candidate_to_selected_ratio": 1.0,
    }
    assert report["work_amplification"]["budgeted_request"]["result"] == {
        "returned_results": 2,
        "selected_chunks": 2,
        "dropped_chunks": 6,
        "context_tokens": 75,
        "candidate_to_selected_ratio": 4.0,
    }
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


def test_token_budget_benchmark_does_not_emit_audit_persistence_warnings(caplog) -> None:
    run_benchmark(iterations=2, budget=80)

    assert "Audit event persistence failed" not in caplog.text
