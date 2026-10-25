from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from memory.ingestion.fact_timeline import FactTimelineProjector
from memory.ingestion.mvp_ingestion import extract_facts
from memory.ingestion.query_planning import plan_retrieval_query

CORPUS_PATH = Path(__file__).with_name("corpora") / "quality-v1.json"
DEFAULT_THRESHOLDS: dict[str, float] = {
    "precision_at_1": 0.85,
    "recall_at_k": 0.90,
    "mrr": 0.85,
    "ndcg_at_k": 0.90,
    "filtered_recall": 0.90,
    "fact_precision": 0.90,
    "fact_recall": 0.80,
    "temporal_accuracy": 0.90,
    "conflict_accuracy": 0.90,
    "answer_contract_accuracy": 1.0,
    "max_latency_p95_ms": 250.0,
    "max_fallback_rate": 0.35,
    "min_fallback_accuracy": 0.90,
}


def load_corpus(path: str | Path | None = None) -> dict[str, Any]:
    corpus_path = Path(path) if path is not None else CORPUS_PATH
    payload = json.loads(corpus_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "1.0":
        raise ValueError("unsupported quality corpus schema_version")
    if payload.get("data_classification") != "synthetic":
        raise ValueError("quality corpus must contain synthetic data only")
    return payload


def run_evaluation(
    *,
    corpus_path: str | Path | None = None,
    observations_path: str | Path | None = None,
    thresholds: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    corpus = load_corpus(corpus_path)
    effective_thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    _validate_thresholds(effective_thresholds)
    observations = (
        json.loads(Path(observations_path).read_text(encoding="utf-8"))
        if observations_path is not None
        else _run_deterministic_provider(corpus)
    )
    metrics = _evaluate_observations(corpus, observations)
    failures = _threshold_failures(metrics, effective_thresholds)
    return {
        "report_schema_version": "1.0",
        "corpus": {
            "id": corpus["id"],
            "schema_version": corpus["schema_version"],
            "data_classification": corpus["data_classification"],
            "documents": len(_expanded_documents(corpus)),
            "retrieval_cases": len(corpus["retrieval_cases"]),
            "extraction_cases": len(corpus["extraction_cases"]),
            "temporal_cases": len(corpus["temporal_cases"]),
        },
        "runner": observations.get("runner", "external"),
        "metrics": metrics,
        "thresholds": {
            "values": effective_thresholds,
            "rationale": corpus["threshold_policy"]["rationale"],
            "baseline": corpus["threshold_policy"]["baseline"],
            "failures": failures,
        },
        "cases": observations,
    }


def assert_thresholds(report: Mapping[str, Any]) -> None:
    failures = report.get("thresholds", {}).get("failures", [])
    if failures:
        raise AssertionError("; ".join(str(item) for item in failures))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the versioned memory quality gate.")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument(
        "--observations",
        type=Path,
        help="Evaluate a provider/model observation file against the same labelled corpus.",
    )
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path.")
    args = parser.parse_args(argv)
    report = run_evaluation(corpus_path=args.corpus, observations_path=args.observations)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    try:
        assert_thresholds(report)
    except AssertionError:
        return 1
    return 0


def _run_deterministic_provider(corpus: Mapping[str, Any]) -> dict[str, Any]:
    retrieval: list[dict[str, Any]] = []
    latencies: list[float] = []
    documents = _expanded_documents(corpus)
    for case in corpus["retrieval_cases"]:
        started = time.perf_counter()
        plan = plan_retrieval_query(str(case["query"]))
        ranked = _rank_documents(plan.semantic_query, documents, scale=str(case["score_scale"]))
        attempts = 1
        if plan.filters.model_dump(exclude_none=True):
            limit = max(5, int(case["k"]) * 3)
            while True:
                visible = [doc for doc in ranked[:limit] if _matches_plan(doc, plan.filters.model_dump())]
                if len(visible) >= int(case["k"]) or limit >= len(ranked):
                    ranked = visible
                    break
                attempts += 1
                limit = min(limit * 2, len(ranked))
        fallback = str(case.get("reranker", "ok")) in {"timeout", "error"}
        result_ids = [str(doc["id"]) for doc in ranked[: int(case["k"])]]
        latencies.append((time.perf_counter() - started) * 1000)
        retrieval.append(
            {
                "name": case["name"],
                "result_ids": result_ids,
                "query_plan": plan.diagnostics(),
                "filter_attempts": attempts,
                "reranker_fallback": fallback,
            }
        )

    extraction: list[dict[str, Any]] = []
    for case in corpus["extraction_cases"]:
        facts = extract_facts(case["conversation"])
        extraction.append(
            {
                "name": case["name"],
                "facts": [_fact_key(fact) for fact in facts],
            }
        )

    temporal = []
    projections = {}
    for case in corpus["temporal_cases"]:
        projection = FactTimelineProjector().project(case["facts"])
        projections[str(case["name"])] = projection
        latest = projection.latest_payload() or {}
        temporal.append(
            {
                "name": case["name"],
                "latest_values": sorted(
                    str(value)
                    for value in (latest.get("values") or [latest.get("value")])
                    if value is not None
                ),
                "history_values": [row["value"] for row in projection.timeline_payload()],
                "conflict": projection.has_latest_conflict,
            }
        )

    answers = []
    for case in corpus["answer_cases"]:
        projection = projections[str(case["temporal_case"])]
        values = sorted(entry.value for entry in projection.unique_latest_entries)
        answer = values[0] if case["mode"] == "concise" else f"Conflicting latest values: {'; '.join(values)}"
        answers.append(
            {
                "name": case["name"],
                "mode": case["mode"],
                "answer": answer,
                "evidence_ids": sorted(
                    str(entry.fact["id"]) for entry in projection.unique_latest_entries
                ),
            }
        )
    return {
        "runner": "deterministic-ci",
        "retrieval": retrieval,
        "extraction": extraction,
        "temporal": temporal,
        "answers": answers,
        "latency_ms": latencies,
    }


def _rank_documents(query: str, documents: list[dict[str, Any]], *, scale: str) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    rows = []
    for document in documents:
        document_tokens = _tokens(_document_text(document))
        overlap = sum(1 for token in query_tokens if token in document_tokens)
        phrase = int(query.lower() in _document_text(document).lower())
        identifier = sum(2 for token in query_tokens if any(char.isdigit() for char in token) and token in document_tokens)
        score = float(overlap + phrase * 3 + identifier)
        if scale == "distance":
            score = 1.0 / (1.0 + score)
        elif scale == "wide_similarity":
            score *= 100.0
        rows.append((score, document))
    reverse = scale != "distance"
    rows.sort(key=lambda item: (item[0], str(item[1]["id"])), reverse=reverse)
    return [document for _score, document in rows]


def _tokens(value: str) -> set[str]:
    aliases = {
        "credential": "token",
        "credentials": "token",
        "automobile": "car",
        "vehiculo": "car",
        "coche": "car",
        "proyecto": "project",
        "reciente": "latest",
    }
    normalized = "".join(char.lower() if char.isalnum() or char in "-_" else " " for char in value)
    return {aliases.get(token, token) for token in normalized.split() if len(token) > 1}


def _document_text(document: Mapping[str, Any]) -> str:
    return " ".join(
        [
            str(document.get("title", "")),
            str(document.get("text", "")),
            " ".join(str(tag) for tag in document.get("tags", [])),
        ]
    )


def _expanded_documents(corpus: Mapping[str, Any]) -> list[dict[str, Any]]:
    documents = [dict(document) for document in corpus["documents"]]
    for specification in corpus.get("generated_distractors", []):
        for index in range(int(specification["count"])):
            documents.append(
                {
                    "id": f"{specification['id_prefix']}-{index:03d}",
                    "source": specification["source"],
                    "title": specification["title"],
                    "text": specification["text"],
                    "tags": list(specification.get("tags", [])),
                }
            )
    return documents


def _matches_plan(document: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    source = filters.get("source")
    if source and document.get("source") != source:
        return False
    tags = set(filters.get("tags") or [])
    if tags and not tags.issubset(set(document.get("tags") or [])):
        return False
    thread_id = filters.get("thread_id")
    if thread_id and document.get("thread_id") != thread_id:
        return False
    return not filters.get("handoff_only") or bool(document.get("handoff"))


def _evaluate_observations(corpus: Mapping[str, Any], observations: Mapping[str, Any]) -> dict[str, Any]:
    expected_retrieval = {case["name"]: case for case in corpus["retrieval_cases"]}
    retrieval_rows = []
    for observed in observations.get("retrieval", []):
        expected = expected_retrieval[str(observed["name"])]
        result_ids = [str(value) for value in observed.get("result_ids", [])]
        relevant = set(str(value) for value in expected["relevant_ids"])
        gains = [1.0 if value in relevant else 0.0 for value in result_ids]
        first = next((index + 1 for index, value in enumerate(result_ids) if value in relevant), None)
        retrieval_rows.append(
            {
                "filtered": bool(expected.get("filtered")),
                "precision_at_1": float(bool(result_ids and result_ids[0] in relevant)),
                "recall_at_k": len(relevant.intersection(result_ids)) / max(len(relevant), 1),
                "reciprocal_rank": 0.0 if first is None else 1.0 / first,
                "ndcg_at_k": _ndcg(gains, min(len(relevant), int(expected["k"]))),
                "fallback": bool(observed.get("reranker_fallback")),
            }
        )
    retrieval = {
        "precision_at_1": _mean(row["precision_at_1"] for row in retrieval_rows),
        "recall_at_k": _mean(row["recall_at_k"] for row in retrieval_rows),
        "mrr": _mean(row["reciprocal_rank"] for row in retrieval_rows),
        "ndcg_at_k": _mean(row["ndcg_at_k"] for row in retrieval_rows),
        "filtered_recall": _mean(row["recall_at_k"] for row in retrieval_rows if row["filtered"]),
    }
    fallback_rows = [row for row in retrieval_rows if row["fallback"]]
    retrieval["fallback_rate"] = len(fallback_rows) / max(len(retrieval_rows), 1)
    retrieval["fallback_accuracy"] = _mean(row["precision_at_1"] for row in fallback_rows)

    expected_facts = {
        case["name"]: {tuple(value) for value in case["expected_facts"]}
        for case in corpus["extraction_cases"]
    }
    produced_facts = {
        str(case["name"]): {tuple(value) for value in case.get("facts", [])}
        for case in observations.get("extraction", [])
    }
    expected_set = {(name, *fact) for name, facts in expected_facts.items() for fact in facts}
    produced_set = {(name, *fact) for name, facts in produced_facts.items() for fact in facts}
    matched = expected_set & produced_set
    extraction = {
        "precision": len(matched) / max(len(produced_set), 1),
        "recall": len(matched) / max(len(expected_set), 1),
    }

    expected_temporal = {case["name"]: case for case in corpus["temporal_cases"]}
    temporal_rows = []
    for observed in observations.get("temporal", []):
        expected = expected_temporal[str(observed["name"])]
        temporal_rows.append(
            (
                sorted(observed.get("latest_values", [])) == sorted(expected["latest_values"]),
                bool(observed.get("conflict")) == bool(expected["conflict"]),
            )
        )
    temporal = {
        "accuracy": _mean(float(row[0]) for row in temporal_rows),
        "conflict_accuracy": _mean(float(row[1]) for row in temporal_rows),
    }

    expected_answers = {case["name"]: case for case in corpus["answer_cases"]}
    answer_rows = []
    for observed in observations.get("answers", []):
        expected = expected_answers[str(observed["name"])]
        answer_rows.append(
            observed.get("mode") == expected["mode"]
            and observed.get("answer") == expected["expected_answer"]
            and observed.get("evidence_ids") == expected["expected_evidence_ids"]
        )
    latency_values = sorted(float(value) for value in observations.get("latency_ms", []))
    return {
        "retrieval": retrieval,
        "extraction": extraction,
        "temporal": temporal,
        "answers": {"contract_accuracy": _mean(float(value) for value in answer_rows)},
        "latency_ms": {
            "p50": _percentile(latency_values, 0.50),
            "p95": _percentile(latency_values, 0.95),
            "samples": len(latency_values),
        },
    }


def _fact_key(fact: Mapping[str, Any]) -> list[str]:
    return [str(fact.get("subject", "")), str(fact.get("predicate", "")), str(fact.get("object", ""))]


def _ndcg(gains: list[float], ideal_relevant: int) -> float:
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(ideal_relevant))
    return dcg / ideal if ideal else 1.0


def _mean(values: Iterable[float]) -> float:
    collected = list(values)
    return statistics.fmean(collected) if collected else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    return values[min(math.ceil(len(values) * percentile) - 1, len(values) - 1)]


def _validate_thresholds(thresholds: Mapping[str, float]) -> None:
    for name, value in thresholds.items():
        if name == "max_latency_p95_ms":
            if value <= 0:
                raise ValueError("latency threshold must be positive")
        elif not 0.0 <= value <= 1.0:
            raise ValueError(f"threshold {name} must be between 0 and 1")


def _threshold_failures(metrics: Mapping[str, Any], thresholds: Mapping[str, float]) -> list[str]:
    measured = {
        "precision_at_1": metrics["retrieval"]["precision_at_1"],
        "recall_at_k": metrics["retrieval"]["recall_at_k"],
        "mrr": metrics["retrieval"]["mrr"],
        "ndcg_at_k": metrics["retrieval"]["ndcg_at_k"],
        "filtered_recall": metrics["retrieval"]["filtered_recall"],
        "fact_precision": metrics["extraction"]["precision"],
        "fact_recall": metrics["extraction"]["recall"],
        "temporal_accuracy": metrics["temporal"]["accuracy"],
        "conflict_accuracy": metrics["temporal"]["conflict_accuracy"],
        "answer_contract_accuracy": metrics["answers"]["contract_accuracy"],
        "fallback_rate": metrics["retrieval"]["fallback_rate"],
        "fallback_accuracy": metrics["retrieval"]["fallback_accuracy"],
        "latency_p95_ms": metrics["latency_ms"]["p95"],
    }
    failures = []
    for name, threshold in thresholds.items():
        metric = name.removeprefix("min_").removeprefix("max_")
        value = float(measured[metric])
        failed = value > threshold if name.startswith("max_") else value < threshold
        if failed:
            operator = ">" if name.startswith("max_") else "<"
            failures.append(f"{metric} {value:.3f} {operator} {threshold:.3f}")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
