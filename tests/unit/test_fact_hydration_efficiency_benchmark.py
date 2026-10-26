from __future__ import annotations

from memory.benchmarks.fact_hydration_efficiency import assert_thresholds, run_benchmark


def test_fact_hydration_efficiency_batches_deduplicated_source_reads() -> None:
    report = run_benchmark()

    assert report["measurement"] == "deterministic_work_amplification"
    assert report["fixture"] == {
        "facts": 120,
        "unique_source_conversations": 3,
        "results": 120,
    }
    assert report["previous_path"]["single_read_calls"] == 240
    assert report["optimized_path"] == {
        "single_read_calls": 0,
        "batch_read_calls": 1,
        "batch_ids_requested": 3,
        "rows_returned": 3,
    }
    assert report["reductions"]["metadata_read_calls"] > 0.99
    assert_thresholds(report)
