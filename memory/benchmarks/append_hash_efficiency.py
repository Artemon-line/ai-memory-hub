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

_EXISTING_MESSAGE_COUNT = 64
_BASE_PAYLOAD = {
    "id": "00000000-0000-4000-8000-000000000301",
    "source": "benchmark",
    "timestamp": "2026-10-27T00:00:00Z",
    "messages": [
        {
            "role": "user" if index % 2 == 0 else "assistant",
            "text": f"Deterministic append hash message {index}.",
        }
        for index in range(_EXISTING_MESSAGE_COUNT)
    ],
    "metadata": {},
}
_APPEND_PAYLOAD = {
    **_BASE_PAYLOAD,
    "messages": [
        *_BASE_PAYLOAD["messages"],
        {"role": "user", "text": "One verified append hash message."},
    ],
}


def run_benchmark() -> dict[str, Any]:
    """Measure redundant aggregate hashing for a complete-history append."""
    original_runtime = mvp_ingestion._RUNTIME
    original_hash_ordered_messages = mvp_ingestion.hash_ordered_messages
    ledger = _WorkLedger()
    metadata_store = _CountingMetadataStore(ledger)
    hash_calls: list[int] = []

    def counting_hash(messages: list[dict[str, Any]]) -> str:
        hash_calls.append(len(messages))
        return original_hash_ordered_messages(messages)

    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=_CountingEmbedder(ledger),
        metadata_store=metadata_store,
        vector_store=_CountingVectorStore(ledger),
        health_state={"mode": "ok"},
        allow_trusted_appends=True,
    )
    try:
        mvp_ingestion.hash_ordered_messages = counting_hash
        mvp_ingestion.configure_runtime(runtime=runtime)
        mvp_ingestion.ingest_messages(deepcopy(_BASE_PAYLOAD))
        hash_calls.clear()
        result = mvp_ingestion.ingest_messages(deepcopy(_APPEND_PAYLOAD))
        stored = metadata_store.rows[str(_BASE_PAYLOAD["id"])]
    finally:
        mvp_ingestion.hash_ordered_messages = original_hash_ordered_messages
        mvp_ingestion._RUNTIME = original_runtime

    optimized_messages_scanned = sum(hash_calls)
    previous_messages_scanned = len(_APPEND_PAYLOAD["messages"]) * 2
    expected_raw_hash = original_hash_ordered_messages(stored["messages"])
    expected_hash = mvp_ingestion._project_scoped_conversation_hash(
        expected_raw_hash,
        project_id=mvp_ingestion.LOCAL_DEFAULT_PROJECT_ID,
    )
    return {
        "append_result": result,
        "previous_append_path": {
            "aggregate_hash_passes": 2,
            "message_hashes_scanned": previous_messages_scanned,
        },
        "verified_identity_reuse_path": {
            "aggregate_hash_calls": hash_calls,
            "aggregate_hash_passes": len(hash_calls),
            "message_hashes_scanned": optimized_messages_scanned,
        },
        "reduction": 1.0 - (optimized_messages_scanned / previous_messages_scanned),
        "stored_hash_matches_combined_history": (
            stored["metadata"]["conversation_hash"] == expected_hash
        ),
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    if report["append_result"].get("appended_messages") != 1:
        raise AssertionError("append fixture must add exactly one message")
    if report["verified_identity_reuse_path"]["aggregate_hash_passes"] != 1:
        raise AssertionError("complete-history append must derive its aggregate hash once")
    if report["reduction"] < 0.5:
        raise AssertionError("aggregate append hash scanning must fall by at least 50%")
    if not report["stored_hash_matches_combined_history"]:
        raise AssertionError("reused aggregate hash must match the combined stored history")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure trusted-append aggregate hashing work.")
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
