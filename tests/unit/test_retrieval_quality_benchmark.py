from __future__ import annotations

import pytest

from memory.benchmarks.retrieval_quality import assert_thresholds, run_evaluation


def test_retrieval_quality_evaluation_reports_baseline_and_tuned_metrics() -> None:
    report = run_evaluation()

    assert report["corpus"]["conversations"] == 6
    assert report["corpus"]["cases"] == 6
    assert report["baseline"]["mrr"] < report["tuned"]["mrr"]
    assert report["baseline"]["precision_at_1"] < report["tuned"]["precision_at_1"]
    assert report["tuned"]["precision_at_1"] >= 0.85
    assert report["tuned"]["recall_at_k"] >= 0.90
    assert report["thresholds"]["failures"] == []
    assert all(case["result_ids"][0] in case["expected_ids"] for case in report["cases"])


def test_retrieval_quality_evaluation_rejects_invalid_top_k() -> None:
    with pytest.raises(ValueError, match="top_k"):
        run_evaluation(top_k=0)


def test_retrieval_quality_threshold_failures_are_explicit() -> None:
    report = run_evaluation(min_precision_at_1=1.1)

    assert report["thresholds"]["failures"]
    with pytest.raises(AssertionError, match="precision_at_1"):
        assert_thresholds(report)
