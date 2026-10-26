from __future__ import annotations

import pytest

from memory.benchmarks.ingestion_efficiency import assert_thresholds, run_benchmark


def test_ingestion_efficiency_reports_zero_duplicate_indexing_work() -> None:
    report = run_benchmark()

    assert report["measurement"] == "deterministic_work_amplification"
    assert "latency_ms" not in report["accepted"]
    assert "latency_ms" not in report["duplicate"]
    assert report["accepted"]["work"]["embedding_inputs"] == 2
    assert report["accepted"]["work"]["vector_rows_written"] == 2
    assert report["duplicate"]["result"]["embedded_chunks"] == 0
    assert report["duplicate"]["work"]["embedding_calls"] == 0
    assert report["duplicate"]["work"]["vector_write_calls"] == 0
    assert report["duplicate"]["work"]["audit_rows_written"] == 1
    assert report["reductions"] == {
        "embedding_inputs": 1.0,
        "vector_rows_written": 1.0,
        "append_summary_generation_passes": 0.5,
        "append_derived_rows_written": 0.5,
    }
    assert_thresholds(report)


def test_ingestion_efficiency_reports_single_pass_append_summaries() -> None:
    report = run_benchmark()

    assert report["append"]["result"]["appended_messages"] == 1
    assert report["previous_append_path"]["summary_generation_passes"] == 2
    assert report["append"]["work"]["derived_rows_written"] > 0
    assert report["previous_append_path"]["derived_rows_written"] == (
        report["append"]["work"]["derived_rows_written"] * 2
    )


def test_ingestion_efficiency_thresholds_reject_embedding_work() -> None:
    report = run_benchmark()
    report["duplicate"]["work"]["embedding_inputs"] = 1

    with pytest.raises(AssertionError, match="zero embedding work"):
        assert_thresholds(report)
