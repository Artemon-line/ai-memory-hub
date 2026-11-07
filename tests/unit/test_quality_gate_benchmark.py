from __future__ import annotations

import json

import pytest

from memory.benchmarks import quality_gate


def test_quality_gate_covers_representative_quality_dimensions() -> None:
    report = quality_gate.run_evaluation()

    assert report["corpus"] == {
        "id": "ai-memory-hub-quality-v1",
        "schema_version": "1.0",
        "data_classification": "synthetic",
        "documents": 130,
        "retrieval_cases": 8,
        "extraction_cases": 5,
        "temporal_cases": 3,
    }
    assert report["thresholds"]["failures"] == []
    assert set(report["metrics"]) == {
        "retrieval",
        "extraction",
        "temporal",
        "answers",
        "latency_ms",
    }
    latency = report["metrics"]["latency_ms"]
    assert latency["samples"] > 0
    assert latency["p50"] <= latency["p95"] <= latency["p99"] <= latency["max"]


def test_quality_gate_exercises_sparse_filters_and_fallbacks() -> None:
    report = quality_gate.run_evaluation()
    cases = {case["name"]: case for case in report["cases"]["retrieval"]}

    assert cases["sparse_beyond_top_100"]["filter_attempts"] > 1
    assert cases["sparse_beyond_top_100"]["result_ids"][0] == "filtered-target"
    assert cases["natural_language_source_filter"]["query_plan"]["filters"]["source"] == "codex"
    assert cases["rank_fusion_timeout"]["reranker_fallback"] is True
    assert report["metrics"]["retrieval"]["fallback_rate"] == 0.25
    assert report["metrics"]["retrieval"]["fallback_accuracy"] == 1.0


def test_provider_observations_produce_comparable_report(tmp_path) -> None:
    corpus = quality_gate.load_corpus()
    observations = quality_gate._run_deterministic_provider(corpus)
    observations["runner"] = "provider-example"
    observations_path = tmp_path / "observations.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")

    report = quality_gate.run_evaluation(observations_path=observations_path)

    assert report["runner"] == "provider-example"
    assert report["report_schema_version"] == "1.0"
    assert report["thresholds"]["failures"] == []


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown"])
def test_provider_observations_require_exact_case_coverage(
    tmp_path, mutation: str
) -> None:
    corpus = quality_gate.load_corpus()
    observations = quality_gate._run_deterministic_provider(corpus)
    rows = observations["retrieval"]
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows[-1] = dict(rows[0])
    else:
        rows[-1]["name"] = "unknown-case"
    observations_path = tmp_path / "observations.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")

    with pytest.raises(ValueError, match="coverage mismatch"):
        quality_gate.run_evaluation(observations_path=observations_path)


@pytest.mark.parametrize("sample_count", [0, 1, 9])
def test_provider_observations_require_one_latency_sample_per_retrieval(
    tmp_path, sample_count: int
) -> None:
    corpus = quality_gate.load_corpus()
    observations = quality_gate._run_deterministic_provider(corpus)
    observations["latency_ms"] = [1.0] * sample_count
    observations_path = tmp_path / "observations.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")

    with pytest.raises(ValueError, match="latency_ms coverage mismatch"):
        quality_gate.run_evaluation(observations_path=observations_path)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_quality_gate_rejects_non_finite_thresholds(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        quality_gate.run_evaluation(thresholds={"precision_at_1": value})


def test_quality_gate_threshold_failures_are_explicit() -> None:
    report = quality_gate.run_evaluation(thresholds={"max_fallback_rate": 0.1})

    assert report["thresholds"]["failures"]
    with pytest.raises(AssertionError, match="fallback_rate"):
        quality_gate.assert_thresholds(report)


def test_quality_gate_rejects_non_synthetic_or_unversioned_corpus(tmp_path) -> None:
    corpus = quality_gate.load_corpus()
    corpus["data_classification"] = "production"
    corpus_path = tmp_path / "unsafe.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    with pytest.raises(ValueError, match="synthetic"):
        quality_gate.load_corpus(corpus_path)

    corpus["data_classification"] = "synthetic"
    corpus["schema_version"] = "2.0"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        quality_gate.load_corpus(corpus_path)


def test_quality_gate_cli_writes_machine_readable_report(tmp_path, capsys) -> None:
    output = tmp_path / "report.json"

    assert quality_gate.main(["--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["thresholds"]["failures"] == []
    assert json.loads(capsys.readouterr().out)["report_schema_version"] == "1.0"
