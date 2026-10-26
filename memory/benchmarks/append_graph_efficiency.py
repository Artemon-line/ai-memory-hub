from __future__ import annotations

import argparse
import json
from copy import deepcopy
from typing import Any

from memory.benchmarks.ingestion_efficiency import (
    _CountingEmbedder,
    _CountingMetadataStore,
    _CountingVectorStore,
    _WorkLedger,
)
from memory.ingestion import mvp_ingestion

_BASE_PAYLOAD = {
    "id": "00000000-0000-4000-8000-000000000201",
    "source": "benchmark",
    "timestamp": "2026-10-26T00:00:00Z",
    "title": "Velvet Lantern",
    "messages": [
        {"role": "user", "text": "Velvet Lantern uses PGVector."},
        {"role": "assistant", "text": "The graph decision was recorded."},
    ],
    "metadata": {},
}

_APPEND_PAYLOAD = {
    **_BASE_PAYLOAD,
    "messages": [
        *_BASE_PAYLOAD["messages"],
        {"role": "user", "text": "Velvet Lantern changed to green."},
    ],
}


def run_benchmark() -> dict[str, Any]:
    original_runtime = mvp_ingestion._RUNTIME
    original_extract = mvp_ingestion.extract_memory_graph
    ledger = _WorkLedger()
    metadata_store = _CountingMetadataStore(ledger)
    calls: list[dict[str, int]] = []

    def counting_extract(
        conversation: dict[str, Any],
        *,
        messages: list[dict[str, Any]] | None = None,
        start_message_index: int = 0,
    ) -> dict[str, list[dict[str, Any]]]:
        selected = conversation.get("messages", []) if messages is None else messages
        calls.append(
            {
                "messages_scanned": len(selected),
                "start_message_index": start_message_index,
            }
        )
        return original_extract(
            conversation,
            messages=messages,
            start_message_index=start_message_index,
        )

    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=_CountingEmbedder(ledger),
        metadata_store=metadata_store,
        vector_store=_CountingVectorStore(ledger),
        health_state={"mode": "ok"},
        allow_trusted_appends=True,
        graph_enabled=True,
    )
    try:
        mvp_ingestion.extract_memory_graph = counting_extract
        mvp_ingestion.configure_runtime(runtime=runtime)
        mvp_ingestion.ingest_messages(deepcopy(_BASE_PAYLOAD))
        calls.clear()
        result = mvp_ingestion.ingest_messages(deepcopy(_APPEND_PAYLOAD))
        relationships = mvp_ingestion.graph_relationship_search(include_superseded=True)[
            "results"
        ]
    finally:
        mvp_ingestion.extract_memory_graph = original_extract
        mvp_ingestion._RUNTIME = original_runtime

    previous_messages_scanned = len(_APPEND_PAYLOAD["messages"])
    messages_scanned = sum(call["messages_scanned"] for call in calls)
    reduction = 1.0 - (messages_scanned / previous_messages_scanned)
    appended_relationship = next(
        row for row in relationships if row.get("predicate") == "changed_to"
    )
    return {
        "append_result": result,
        "previous_append_path": {
            "graph_messages_scanned": previous_messages_scanned,
        },
        "incremental_append_path": {
            "graph_calls": calls,
            "graph_messages_scanned": messages_scanned,
        },
        "reduction": reduction,
        "appended_relationship_message_index": appended_relationship["provenance"][0][
            "message_index"
        ],
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    if report["append_result"].get("appended_messages") != 1:
        raise AssertionError("append fixture must add exactly one message")
    if report["incremental_append_path"]["graph_messages_scanned"] != 1:
        raise AssertionError("graph extraction must scan only the appended message")
    if report["reduction"] < 0.5:
        raise AssertionError("append graph scan work must fall by at least 50%")
    if report["appended_relationship_message_index"] != 2:
        raise AssertionError("incremental graph provenance must retain the absolute index")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure append graph derivation work.")
    parser.parse_args(argv)
    report = run_benchmark()
    print(json.dumps(report, indent=2, sort_keys=True))
    try:
        assert_thresholds(report)
    except AssertionError as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
