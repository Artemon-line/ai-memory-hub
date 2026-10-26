from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from typing import Any

from memory.backend.metadata_store import LOCAL_DEFAULT_PROJECT_ID
from memory.ingestion import mvp_ingestion

_FACT_COUNT = 120
_CONVERSATION_COUNT = 3


@dataclass
class _ReadLedger:
    single_read_calls: int = 0
    batch_read_calls: int = 0
    batch_ids_requested: int = 0
    rows_returned: int = 0


class _UnusedEmbedder:
    dimension = 2

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError(f"benchmark must not embed {len(texts)} text(s)")


class _CountingMetadataStore:
    def __init__(self, ledger: _ReadLedger) -> None:
        self._ledger = ledger
        self.rows = {
            _memory_id(index): {
                "id": _memory_id(index),
                "source": "benchmark",
                "timestamp": "2026-10-26T00:00:00Z",
                "messages": [
                    {"role": "user", "text": f"Benchmark source conversation {index}."}
                ],
                "metadata": {"project_id": LOCAL_DEFAULT_PROJECT_ID},
            }
            for index in range(_CONVERSATION_COUNT)
        }
        self.facts = [
            {
                "id": f"fact-{index}",
                "subject": "benchmark",
                "predicate": "description",
                "object": f"benchmark record {index}",
                "source_conversation_id": _memory_id(index % _CONVERSATION_COUNT),
                "source_message_indexes": [0],
                "project_id": LOCAL_DEFAULT_PROJECT_ID,
                "created_at": "2026-10-26T00:00:00Z",
                "updated_at": "2026-10-26T00:00:00Z",
                "qualifiers": {},
            }
            for index in range(_FACT_COUNT)
        ]

    def search_facts(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return list(self.facts)

    def get(self, memory_id: str) -> dict[str, Any] | None:
        self._ledger.single_read_calls += 1
        row = self.rows.get(memory_id)
        self._ledger.rows_returned += int(row is not None)
        return row

    def get_many(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        self._ledger.batch_read_calls += 1
        self._ledger.batch_ids_requested += len(ids)
        rows = {memory_id: self.rows[memory_id] for memory_id in ids if memory_id in self.rows}
        self._ledger.rows_returned += len(rows)
        return rows


def run_benchmark() -> dict[str, Any]:
    """Measure source-conversation reads for fact ranking and projection."""
    original_runtime = mvp_ingestion._RUNTIME
    ledger = _ReadLedger()
    store = _CountingMetadataStore(ledger)
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=_UnusedEmbedder(),
        metadata_store=store,
        vector_store=object(),
        health_state={"mode": "ok"},
    )
    try:
        mvp_ingestion.configure_runtime(runtime=runtime)
        result = mvp_ingestion.fact_search(query="benchmark record")
    finally:
        mvp_ingestion._RUNTIME = original_runtime

    previous_single_reads = _FACT_COUNT * 2
    optimized_read_calls = ledger.single_read_calls + ledger.batch_read_calls
    return {
        "measurement": "deterministic_work_amplification",
        "fixture": {
            "facts": _FACT_COUNT,
            "unique_source_conversations": _CONVERSATION_COUNT,
            "results": len(result["results"]),
        },
        "previous_path": {
            "single_read_calls": previous_single_reads,
            "reason": "one source read per fact during ranking and public projection",
        },
        "optimized_path": {
            "single_read_calls": ledger.single_read_calls,
            "batch_read_calls": ledger.batch_read_calls,
            "batch_ids_requested": ledger.batch_ids_requested,
            "rows_returned": ledger.rows_returned,
        },
        "reductions": {
            "metadata_read_calls": _reduction(previous_single_reads, optimized_read_calls),
        },
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    fixture = report["fixture"]
    optimized = report["optimized_path"]
    expected_batches = math.ceil(
        fixture["unique_source_conversations"]
        / mvp_ingestion._FACT_SOURCE_HYDRATION_BATCH_SIZE
    )
    if fixture["results"] != fixture["facts"]:
        raise AssertionError("fact hydration benchmark must preserve every result")
    if optimized["single_read_calls"] != 0:
        raise AssertionError("batch-capable stores must perform zero single-record source reads")
    if optimized["batch_read_calls"] != expected_batches:
        raise AssertionError("source hydration must use the minimum bounded batch count")
    if optimized["rows_returned"] != fixture["unique_source_conversations"]:
        raise AssertionError("source hydration must deduplicate conversation rows")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure fact source-conversation hydration work amplification."
    )
    parser.parse_args(argv)
    report = run_benchmark()
    print(json.dumps(report, indent=2, sort_keys=True))
    try:
        assert_thresholds(report)
    except AssertionError as exc:
        print(str(exc))
        return 1
    return 0


def _memory_id(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012d}"


def _reduction(baseline: int, optimized: int) -> float:
    if baseline <= 0:
        return 0.0
    return 1.0 - (optimized / baseline)


if __name__ == "__main__":
    raise SystemExit(main())
