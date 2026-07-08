from __future__ import annotations

import json

import pytest

from memory.benchmarks import graph_quality
from memory.benchmarks.graph_quality import assert_thresholds, run_evaluation


def test_graph_quality_report_shape_and_sections() -> None:
    report = run_evaluation()

    assert report["corpus"]["conversations"] == 5
    assert set(report) >= {
        "corpus",
        "entity",
        "relationship",
        "provenance",
        "conflict_detection",
        "graph_retrieval",
        "baseline_retrieval",
        "thresholds",
    }
    assert report["thresholds"]["failures"] == []


def test_graph_quality_rejects_invalid_arguments() -> None:
    with pytest.raises(ValueError, match="top_k"):
        run_evaluation(top_k=0)
    with pytest.raises(ValueError, match="threshold"):
        run_evaluation(min_entity_precision=1.5)


def test_graph_quality_threshold_failures_are_explicit() -> None:
    report = run_evaluation(min_entity_precision=1.0)

    assert report["thresholds"]["failures"]
    with pytest.raises(AssertionError, match="entity.precision"):
        assert_thresholds(report)


def test_graph_quality_metric_calculations_are_reported() -> None:
    report = run_evaluation()

    assert report["entity"]["precision"] >= 0.9
    assert report["entity"]["recall"] == 1.0
    assert report["entity"]["f1"] >= 0.94
    assert report["relationship"]["precision"] == 1.0
    assert report["relationship"]["recall"] == 1.0
    assert report["provenance"]["accuracy"] == 1.0


def test_graph_retrieval_reports_baseline_and_no_regression() -> None:
    report = run_evaluation()

    graph = report["graph_retrieval"]["summary"]
    baseline = report["baseline_retrieval"]["summary"]
    assert graph["precision_at_1"] >= baseline["precision_at_1"]
    assert graph["recall_at_k"] >= baseline["recall_at_k"]
    assert graph["mrr"] >= baseline["mrr"]


def test_graph_quality_detects_graph_retrieval_regression() -> None:
    report = run_evaluation()
    report["graph_retrieval"]["summary"]["mrr"] = 0.0
    report["thresholds"]["failures"] = graph_quality._threshold_failures(
        entity=report["entity"],
        relationship=report["relationship"],
        provenance=report["provenance"],
        graph_retrieval=report["graph_retrieval"],
        baseline_retrieval=report["baseline_retrieval"],
        min_entity_precision=0.0,
        min_entity_recall=0.0,
        min_relationship_precision=0.0,
        min_relationship_recall=0.0,
        min_provenance_accuracy=0.0,
        min_graph_precision_at_1=0.0,
        min_graph_recall_at_k=0.0,
        min_graph_mrr=0.0,
    )

    assert any("regressed below baseline" in failure for failure in report["thresholds"]["failures"])


def test_graph_quality_cli_exit_codes(capsys) -> None:
    assert graph_quality.main([]) == 0
    ok_payload = json.loads(capsys.readouterr().out)
    assert ok_payload["thresholds"]["failures"] == []

    assert graph_quality.main(["--min-entity-precision", "1.0"]) == 1
    failing_payload = json.loads(capsys.readouterr().out)
    assert failing_payload["thresholds"]["failures"]
