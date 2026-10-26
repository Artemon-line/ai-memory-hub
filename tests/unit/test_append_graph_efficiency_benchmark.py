from __future__ import annotations

import pytest

from memory.benchmarks.append_graph_efficiency import assert_thresholds, run_benchmark


def test_append_graph_benchmark_scans_only_new_messages() -> None:
    report = run_benchmark()

    assert report["previous_append_path"]["graph_messages_scanned"] == 3
    assert report["incremental_append_path"]["graph_messages_scanned"] == 1
    assert report["incremental_append_path"]["graph_calls"] == [
        {"messages_scanned": 1, "start_message_index": 2}
    ]
    assert report["reduction"] == pytest.approx(2 / 3)
    assert report["appended_relationship_message_index"] == 2


def test_append_graph_benchmark_thresholds_pass() -> None:
    assert_thresholds(run_benchmark())
