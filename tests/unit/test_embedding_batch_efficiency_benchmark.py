from __future__ import annotations

import pytest

from memory.benchmarks.embedding_batch_efficiency import assert_thresholds, run_benchmark


def test_embedding_batch_benchmark_bounds_requests_and_preserves_mapping() -> None:
    report = run_benchmark()

    assert report["previous_unbounded_path"] == {
        "embedding_calls": 1,
        "largest_batch_inputs": 10,
    }
    assert report["bounded_path"] == {
        "embedding_calls": 4,
        "batch_input_counts": [3, 3, 3, 1],
        "batch_input_bytes": [31, 31, 31, 11],
        "largest_batch_inputs": 3,
    }
    assert report["largest_batch_reduction"] == pytest.approx(0.7)
    assert report["mapping_preserved"] is True


def test_embedding_batch_benchmark_thresholds_pass() -> None:
    assert_thresholds(run_benchmark())
