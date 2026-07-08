from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

from memory.advanced_memory import extract_memory_graph, normalize_graph_text


@dataclass(frozen=True)
class ExpectedEntity:
    entity_type: str
    name: str
    conversation_id: str
    message_index: int


@dataclass(frozen=True)
class ExpectedRelationship:
    subject: str
    predicate: str
    object: str
    conversation_id: str
    message_index: int


@dataclass(frozen=True)
class GraphRetrievalCase:
    name: str
    query: str
    expected_conversation_ids: tuple[str, ...]


DEFAULT_THRESHOLDS = {
    "entity_precision": 0.90,
    "entity_recall": 0.80,
    "relationship_precision": 0.95,
    "relationship_recall": 0.75,
    "provenance_accuracy": 0.98,
    "graph_precision_at_1": 0.85,
    "graph_recall_at_k": 0.90,
    "graph_mrr": 0.85,
}


def run_evaluation(
    *,
    top_k: int = 3,
    min_entity_precision: float = DEFAULT_THRESHOLDS["entity_precision"],
    min_entity_recall: float = DEFAULT_THRESHOLDS["entity_recall"],
    min_relationship_precision: float = DEFAULT_THRESHOLDS["relationship_precision"],
    min_relationship_recall: float = DEFAULT_THRESHOLDS["relationship_recall"],
    min_provenance_accuracy: float = DEFAULT_THRESHOLDS["provenance_accuracy"],
    min_graph_precision_at_1: float = DEFAULT_THRESHOLDS["graph_precision_at_1"],
    min_graph_recall_at_k: float = DEFAULT_THRESHOLDS["graph_recall_at_k"],
    min_graph_mrr: float = DEFAULT_THRESHOLDS["graph_mrr"],
) -> dict[str, Any]:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    _validate_thresholds(
        min_entity_precision,
        min_entity_recall,
        min_relationship_precision,
        min_relationship_recall,
        min_provenance_accuracy,
        min_graph_precision_at_1,
        min_graph_recall_at_k,
        min_graph_mrr,
    )
    corpus = _golden_corpus()
    extracted = [_extract_item(item) for item in corpus]
    entity = _entity_metrics(corpus, extracted)
    relationship = _relationship_metrics(corpus, extracted)
    provenance = _provenance_metrics(corpus, extracted)
    conflict_detection = _conflict_metrics(extracted)
    graph_retrieval = _graph_retrieval_metrics(corpus, extracted, top_k=top_k)
    baseline_retrieval = _baseline_retrieval_metrics(corpus, top_k=top_k)
    thresholds = {
        "min_entity_precision": min_entity_precision,
        "min_entity_recall": min_entity_recall,
        "min_relationship_precision": min_relationship_precision,
        "min_relationship_recall": min_relationship_recall,
        "min_provenance_accuracy": min_provenance_accuracy,
        "min_graph_precision_at_1": min_graph_precision_at_1,
        "min_graph_recall_at_k": min_graph_recall_at_k,
        "min_graph_mrr": min_graph_mrr,
        "failures": _threshold_failures(
            entity=entity,
            relationship=relationship,
            provenance=provenance,
            graph_retrieval=graph_retrieval,
            baseline_retrieval=baseline_retrieval,
            min_entity_precision=min_entity_precision,
            min_entity_recall=min_entity_recall,
            min_relationship_precision=min_relationship_precision,
            min_relationship_recall=min_relationship_recall,
            min_provenance_accuracy=min_provenance_accuracy,
            min_graph_precision_at_1=min_graph_precision_at_1,
            min_graph_recall_at_k=min_graph_recall_at_k,
            min_graph_mrr=min_graph_mrr,
        ),
    }
    return {
        "corpus": {
            "conversations": len(corpus),
            "entities": sum(len(item["expected_entities"]) for item in corpus),
            "relationships": sum(len(item["expected_relationships"]) for item in corpus),
            "retrieval_cases": len(_retrieval_cases()),
        },
        "top_k": top_k,
        "entity": entity,
        "relationship": relationship,
        "provenance": provenance,
        "conflict_detection": conflict_detection,
        "graph_retrieval": graph_retrieval,
        "baseline_retrieval": baseline_retrieval,
        "thresholds": thresholds,
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    failures = report.get("thresholds", {}).get("failures", [])
    if failures:
        raise AssertionError("; ".join(str(failure) for failure in failures))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic graph-memory quality.")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-entity-precision", type=float, default=DEFAULT_THRESHOLDS["entity_precision"])
    parser.add_argument("--min-entity-recall", type=float, default=DEFAULT_THRESHOLDS["entity_recall"])
    parser.add_argument("--min-relationship-precision", type=float, default=DEFAULT_THRESHOLDS["relationship_precision"])
    parser.add_argument("--min-relationship-recall", type=float, default=DEFAULT_THRESHOLDS["relationship_recall"])
    parser.add_argument("--min-provenance-accuracy", type=float, default=DEFAULT_THRESHOLDS["provenance_accuracy"])
    parser.add_argument("--min-graph-precision-at-1", type=float, default=DEFAULT_THRESHOLDS["graph_precision_at_1"])
    parser.add_argument("--min-graph-recall-at-k", type=float, default=DEFAULT_THRESHOLDS["graph_recall_at_k"])
    parser.add_argument("--min-graph-mrr", type=float, default=DEFAULT_THRESHOLDS["graph_mrr"])
    args = parser.parse_args(argv)
    report = run_evaluation(
        top_k=args.top_k,
        min_entity_precision=args.min_entity_precision,
        min_entity_recall=args.min_entity_recall,
        min_relationship_precision=args.min_relationship_precision,
        min_relationship_recall=args.min_relationship_recall,
        min_provenance_accuracy=args.min_provenance_accuracy,
        min_graph_precision_at_1=args.min_graph_precision_at_1,
        min_graph_recall_at_k=args.min_graph_recall_at_k,
        min_graph_mrr=args.min_graph_mrr,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    try:
        assert_thresholds(report)
    except AssertionError:
        return 1
    return 0


def _extract_item(item: dict[str, Any]) -> dict[str, Any]:
    graph = extract_memory_graph(item["conversation"])
    return {
        "conversation_id": item["conversation"]["id"],
        "entities": graph["entities"],
        "relationships": graph["relationships"],
    }


def _entity_metrics(corpus: list[dict[str, Any]], extracted: list[dict[str, Any]]) -> dict[str, Any]:
    expected = _expected_entity_keys(corpus)
    produced = {
        _entity_key(entity, conversation_id=str(item["conversation_id"]))
        for item in extracted
        for entity in item["entities"]
    }
    matched = expected & produced
    return _classification_report(expected=expected, produced=produced, matched=matched)


def _relationship_metrics(corpus: list[dict[str, Any]], extracted: list[dict[str, Any]]) -> dict[str, Any]:
    expected = _expected_relationship_keys(corpus)
    produced = {
        _relationship_key(relationship, conversation_id=str(item["conversation_id"]))
        for item in extracted
        for relationship in item["relationships"]
    }
    matched = expected & produced
    return _classification_report(expected=expected, produced=produced, matched=matched)


def _provenance_metrics(corpus: list[dict[str, Any]], extracted: list[dict[str, Any]]) -> dict[str, Any]:
    expected_relationships = _expected_relationship_keys(corpus)
    expected_entities = _expected_entity_keys(corpus)
    matched = 0
    correct = 0
    for item in extracted:
        conversation_id = str(item["conversation_id"])
        for entity in item["entities"]:
            key = _entity_key(entity, conversation_id=conversation_id)
            if key not in expected_entities:
                continue
            matched += 1
            correct += int(_has_provenance(entity, conversation_id, key[-1]))
        for relationship in item["relationships"]:
            key = _relationship_key(relationship, conversation_id=conversation_id)
            if key not in expected_relationships:
                continue
            matched += 1
            correct += int(_has_provenance(relationship, conversation_id, key[-1]))
    return {
        "matched_records": matched,
        "correct_records": correct,
        "accuracy": 1.0 if matched == 0 else correct / matched,
    }


def _conflict_metrics(extracted: list[dict[str, Any]]) -> dict[str, float]:
    expected_groups = {("graph-quality-conflict", "velvet lantern:changed_to")}
    produced_groups = {
        (str(item["conversation_id"]), str(relationship.get("conflict_group")))
        for item in extracted
        for relationship in item["relationships"]
        if relationship.get("conflict_group")
    }
    matched = expected_groups & produced_groups
    return {
        "precision": len(matched) / max(len(produced_groups), 1),
        "recall": len(matched) / len(expected_groups),
        "f1": _f1(
            len(matched) / max(len(produced_groups), 1),
            len(matched) / len(expected_groups),
        ),
    }


def _graph_retrieval_metrics(
    corpus: list[dict[str, Any]], extracted: list[dict[str, Any]], *, top_k: int
) -> dict[str, Any]:
    cases = [_evaluate_graph_case(case, extracted, top_k=top_k) for case in _retrieval_cases()]
    return {"summary": _retrieval_summary(cases), "cases": cases}


def _baseline_retrieval_metrics(corpus: list[dict[str, Any]], *, top_k: int) -> dict[str, Any]:
    cases = [_evaluate_baseline_case(case, corpus, top_k=top_k) for case in _retrieval_cases()]
    return {"summary": _retrieval_summary(cases), "cases": cases}


def _evaluate_graph_case(
    case: GraphRetrievalCase, extracted: list[dict[str, Any]], *, top_k: int
) -> dict[str, Any]:
    tokens = set(_tokens(case.query))
    scored: list[tuple[int, str]] = []
    for item in extracted:
        score = 0
        for relationship in item["relationships"]:
            text = " ".join(
                [
                    str(relationship.get("subject", "")),
                    str(relationship.get("predicate", "")),
                    str(relationship.get("object", "")),
                ]
            )
            score += len(tokens & set(_tokens(text)))
        if score:
            scored.append((score, str(item["conversation_id"])))
    result_ids = [conversation_id for _, conversation_id in sorted(scored, key=lambda row: (-row[0], row[1]))[:top_k]]
    return _retrieval_case_report(case, result_ids)


def _evaluate_baseline_case(
    case: GraphRetrievalCase, corpus: list[dict[str, Any]], *, top_k: int
) -> dict[str, Any]:
    tokens = set(_tokens(case.query))
    scored: list[tuple[int, str]] = []
    for item in corpus:
        text = _conversation_text(item["conversation"])
        score = len(tokens & set(_tokens(text)))
        if score:
            scored.append((score, str(item["conversation"]["id"])))
    result_ids = [conversation_id for _, conversation_id in sorted(scored, key=lambda row: (row[0], row[1]))[:top_k]]
    return _retrieval_case_report(case, result_ids)


def _retrieval_case_report(case: GraphRetrievalCase, result_ids: list[str]) -> dict[str, Any]:
    expected = set(case.expected_conversation_ids)
    hits = [memory_id for memory_id in result_ids if memory_id in expected]
    first_hit_rank = next(
        (index + 1 for index, memory_id in enumerate(result_ids) if memory_id in expected),
        None,
    )
    return {
        "name": case.name,
        "query": case.query,
        "expected_ids": list(case.expected_conversation_ids),
        "result_ids": result_ids,
        "precision_at_1": 1.0 if result_ids and result_ids[0] in expected else 0.0,
        "recall_at_k": len(hits) / len(expected),
        "reciprocal_rank": 0.0 if first_hit_rank is None else 1.0 / first_hit_rank,
    }


def _retrieval_summary(cases: list[dict[str, Any]]) -> dict[str, float]:
    count = max(len(cases), 1)
    return {
        "precision_at_1": sum(float(case["precision_at_1"]) for case in cases) / count,
        "recall_at_k": sum(float(case["recall_at_k"]) for case in cases) / count,
        "mrr": sum(float(case["reciprocal_rank"]) for case in cases) / count,
    }


def _classification_report(
    *, expected: set[tuple[str, ...]], produced: set[tuple[str, ...]], matched: set[tuple[str, ...]]
) -> dict[str, Any]:
    precision = len(matched) / max(len(produced), 1)
    recall = len(matched) / max(len(expected), 1)
    return {
        "expected": len(expected),
        "produced": len(produced),
        "matched": len(matched),
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
    }


def _threshold_failures(
    *,
    entity: dict[str, Any],
    relationship: dict[str, Any],
    provenance: dict[str, Any],
    graph_retrieval: dict[str, Any],
    baseline_retrieval: dict[str, Any],
    min_entity_precision: float,
    min_entity_recall: float,
    min_relationship_precision: float,
    min_relationship_recall: float,
    min_provenance_accuracy: float,
    min_graph_precision_at_1: float,
    min_graph_recall_at_k: float,
    min_graph_mrr: float,
) -> list[str]:
    graph = graph_retrieval["summary"]
    baseline = baseline_retrieval["summary"]
    checks = [
        ("entity.precision", entity["precision"], min_entity_precision),
        ("entity.recall", entity["recall"], min_entity_recall),
        ("relationship.precision", relationship["precision"], min_relationship_precision),
        ("relationship.recall", relationship["recall"], min_relationship_recall),
        ("provenance.accuracy", provenance["accuracy"], min_provenance_accuracy),
        ("graph_retrieval.precision_at_1", graph["precision_at_1"], min_graph_precision_at_1),
        ("graph_retrieval.recall_at_k", graph["recall_at_k"], min_graph_recall_at_k),
        ("graph_retrieval.mrr", graph["mrr"], min_graph_mrr),
    ]
    failures = [
        f"{name} {observed:.3f} < {expected:.3f}"
        for name, observed, expected in checks
        if observed < expected
    ]
    for name in ("precision_at_1", "recall_at_k", "mrr"):
        if graph[name] < baseline[name]:
            failures.append(f"graph_retrieval.{name} regressed below baseline {graph[name]:.3f} < {baseline[name]:.3f}")
    return failures


def _validate_thresholds(*values: float) -> None:
    for value in values:
        if value < 0 or value > 1:
            raise ValueError("threshold values must be between 0 and 1")


def _expected_entity_keys(corpus: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    return {
        (
            expected.entity_type,
            normalize_graph_text(expected.name),
            expected.conversation_id,
            str(expected.message_index),
        )
        for item in corpus
        for expected in item["expected_entities"]
    }


def _expected_relationship_keys(corpus: list[dict[str, Any]]) -> set[tuple[str, str, str, str, str, str]]:
    return {
        (
            normalize_graph_text(expected.subject),
            expected.predicate,
            normalize_graph_text(expected.object),
            expected.conversation_id,
            str(expected.message_index),
            str(expected.message_index),
        )
        for item in corpus
        for expected in item["expected_relationships"]
    }


def _entity_key(entity: dict[str, Any], *, conversation_id: str) -> tuple[str, str, str, str]:
    message_index = _first_message_index(entity)
    return (
        str(entity.get("entity_type")),
        normalize_graph_text(str(entity.get("name", ""))),
        conversation_id,
        str(message_index),
    )


def _relationship_key(
    relationship: dict[str, Any], *, conversation_id: str
) -> tuple[str, str, str, str, str, str]:
    message_index = _first_message_index(relationship)
    return (
        normalize_graph_text(str(relationship.get("subject", ""))),
        str(relationship.get("predicate")),
        normalize_graph_text(str(relationship.get("object", ""))),
        conversation_id,
        str(message_index),
        str(message_index),
    )


def _has_provenance(record: dict[str, Any], conversation_id: str, message_index: str) -> bool:
    provenance = record.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        return False
    first = provenance[0]
    return (
        isinstance(first, dict)
        and str(first.get("conversation_id")) == conversation_id
        and str(first.get("message_index")) == message_index
    )


def _first_message_index(record: dict[str, Any]) -> int:
    provenance = record.get("provenance")
    if isinstance(provenance, list) and provenance and isinstance(provenance[0], dict):
        return int(provenance[0].get("message_index", 0))
    return 0


def _f1(precision: float, recall: float) -> float:
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def _tokens(value: str) -> list[str]:
    return [token for token in normalize_graph_text(value).replace("_", " ").split() if token]


def _conversation_text(conversation: dict[str, Any]) -> str:
    messages = conversation.get("messages", [])
    return " ".join(str(message.get("text", "")) for message in messages if isinstance(message, dict))


def _golden_corpus() -> list[dict[str, Any]]:
    return [
        {
            "conversation": _conversation(
                "graph-quality-project",
                "Velvet Lantern",
                "Velvet Lantern uses PGVector. The creator is Ada Lovelace.",
            ),
            "expected_entities": [
                ExpectedEntity("project", "Velvet Lantern", "graph-quality-project", 0),
                ExpectedEntity("tool", "PGVector", "graph-quality-project", 0),
                ExpectedEntity("provider", "PGVector", "graph-quality-project", 0),
                ExpectedEntity("person", "Ada Lovelace", "graph-quality-project", 0),
            ],
            "expected_relationships": [
                ExpectedRelationship("Velvet Lantern", "uses_tool", "PGVector", "graph-quality-project", 0),
                ExpectedRelationship("Velvet Lantern", "created_by", "Ada Lovelace", "graph-quality-project", 0),
            ],
        },
        {
            "conversation": _conversation(
                "graph-quality-handoff",
                "Codex handoff",
                "Codex works on Memory Hub. Memory Hub uses Docker.",
            ),
            "expected_entities": [
                ExpectedEntity("tool", "Codex", "graph-quality-handoff", 0),
                ExpectedEntity("person", "Codex", "graph-quality-handoff", 0),
                ExpectedEntity("project", "Memory Hub", "graph-quality-handoff", 0),
                ExpectedEntity("tool", "Docker", "graph-quality-handoff", 0),
            ],
            "expected_relationships": [
                ExpectedRelationship("Codex", "works_on", "Memory Hub", "graph-quality-handoff", 0),
                ExpectedRelationship("Memory Hub", "uses_tool", "Docker", "graph-quality-handoff", 0),
            ],
        },
        {
            "conversation": _conversation(
                "graph-quality-profile",
                "User profile",
                "I own a Gibson Special. My favorite editor is Codex.",
            ),
            "expected_entities": [
                ExpectedEntity("person", "user", "graph-quality-profile", 0),
                ExpectedEntity("owned_item", "a Gibson Special", "graph-quality-profile", 0),
                ExpectedEntity("preference", "editor", "graph-quality-profile", 0),
                ExpectedEntity("preference", "Codex", "graph-quality-profile", 0),
            ],
            "expected_relationships": [
                ExpectedRelationship("user", "owns", "a Gibson Special", "graph-quality-profile", 0),
                ExpectedRelationship("editor", "prefers", "Codex", "graph-quality-profile", 0),
            ],
        },
        {
            "conversation": _conversation(
                "graph-quality-negative",
                "Nearby entities",
                "Codex and opencode were discussed near PGVector.",
            ),
            "expected_entities": [
                ExpectedEntity("tool", "Codex", "graph-quality-negative", 0),
                ExpectedEntity("tool", "opencode", "graph-quality-negative", 0),
                ExpectedEntity("tool", "PGVector", "graph-quality-negative", 0),
                ExpectedEntity("provider", "PGVector", "graph-quality-negative", 0),
            ],
            "expected_relationships": [],
        },
        {
            "conversation": _conversation(
                "graph-quality-conflict",
                "Preference conflict",
                "Velvet Lantern changed to blue. Velvet Lantern changed to green.",
            ),
            "expected_entities": [
                ExpectedEntity("project", "Velvet Lantern", "graph-quality-conflict", 0),
                ExpectedEntity("preference", "blue", "graph-quality-conflict", 0),
                ExpectedEntity("preference", "green", "graph-quality-conflict", 0),
            ],
            "expected_relationships": [
                ExpectedRelationship("Velvet Lantern", "changed_to", "blue", "graph-quality-conflict", 0),
                ExpectedRelationship("Velvet Lantern", "changed_to", "green", "graph-quality-conflict", 0),
            ],
        },
    ]


def _retrieval_cases() -> list[GraphRetrievalCase]:
    return [
        GraphRetrievalCase("project_creator", "who created Velvet Lantern", ("graph-quality-project",)),
        GraphRetrievalCase("project_tool", "which tool does Memory Hub use", ("graph-quality-handoff",)),
        GraphRetrievalCase("owned_item", "what does the user own", ("graph-quality-profile",)),
        GraphRetrievalCase("preference_change", "what changed about Velvet Lantern", ("graph-quality-conflict",)),
    ]


def _conversation(conversation_id: str, title: str, text: str) -> dict[str, Any]:
    return {
        "id": conversation_id,
        "source": "benchmark",
        "title": title,
        "timestamp": "2026-07-08T00:00:00Z",
        "messages": [{"role": "user", "text": text}],
        "metadata": {"imported_at": "2026-07-08T00:00:00Z"},
    }


if __name__ == "__main__":
    raise SystemExit(main())
