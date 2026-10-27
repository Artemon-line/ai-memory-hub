from __future__ import annotations

import pytest

from memory.benchmarks.append_hash_efficiency import assert_thresholds, run_benchmark


def test_append_hash_benchmark_reuses_verified_aggregate_identity() -> None:
    report = run_benchmark()

    assert report["previous_append_path"] == {
        "aggregate_hash_passes": 2,
        "message_hashes_scanned": 130,
    }
    assert report["verified_identity_reuse_path"] == {
        "aggregate_hash_calls": [65],
        "aggregate_hash_passes": 1,
        "message_hashes_scanned": 65,
    }
    assert report["reduction"] == pytest.approx(0.5)
    assert report["stored_hash_matches_combined_history"] is True


def test_append_hash_benchmark_thresholds_pass() -> None:
    assert_thresholds(run_benchmark())
