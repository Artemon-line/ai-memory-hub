from __future__ import annotations

import argparse
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from memory.backend.metadata_store import IndexChunkField, IndexState, MetadataField
from memory.ingestion import mvp_ingestion

_PAYLOAD = {
    "id": "00000000-0000-4000-8000-000000000101",
    "source": "benchmark",
    "timestamp": "2026-10-26T00:00:00Z",
    "messages": [
        {"role": "user", "text": "Remember the bounded ingestion benchmark."},
        {"role": "assistant", "text": "The benchmark is deterministic."},
    ],
    "metadata": {},
}

_APPEND_PAYLOAD = {
    **_PAYLOAD,
    "messages": [
        *_PAYLOAD["messages"],
        {"role": "user", "text": "Append only one new benchmark message."},
    ],
}


@dataclass
class _WorkLedger:
    embedding_calls: int = 0
    embedding_inputs: int = 0
    vector_write_calls: int = 0
    vector_rows_written: int = 0
    metadata_rows_written: int = 0
    derived_rows_written: int = 0
    audit_rows_written: int = 0
    bytes_written: int = 0

    def reset(self) -> None:
        for field_name in self.__dataclass_fields__:
            setattr(self, field_name, 0)

    def snapshot(self) -> dict[str, int]:
        return {
            field_name: int(getattr(self, field_name))
            for field_name in self.__dataclass_fields__
        }


class _CountingEmbedder:
    dimension = 2

    def __init__(self, ledger: _WorkLedger) -> None:
        self._ledger = ledger

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self._ledger.embedding_calls += 1
        self._ledger.embedding_inputs += len(texts)
        return [[float(len(text)), float(index)] for index, text in enumerate(texts)]


class _CountingMetadataStore:
    def __init__(self, ledger: _WorkLedger) -> None:
        self._ledger = ledger
        self.rows: dict[str, dict[str, Any]] = {}
        self._generated_summaries: dict[str, dict[str, Any]] = {}

    def insert_new(self, conversation: dict[str, Any]) -> tuple[str, bool]:
        duplicate = self.get_by_conversation_hash(
            str(conversation["metadata"]["conversation_hash"])
        )
        if duplicate is not None:
            return str(duplicate["id"]), False
        memory_id = str(conversation["id"])
        self.rows[memory_id] = conversation
        self._record_metadata_write(conversation)
        return memory_id, True

    def insert(self, conversation: dict[str, Any]) -> str:
        memory_id = str(conversation["id"])
        self.rows[memory_id] = conversation
        self._record_metadata_write(conversation)
        return memory_id

    def get(self, memory_id: str) -> dict[str, Any] | None:
        return self.rows.get(memory_id)

    def get_by_conversation_hash(
        self,
        conversation_hash: str,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        for conversation in self.rows.values():
            metadata = conversation.get("metadata", {})
            if (
                metadata.get("conversation_hash") == conversation_hash
                and (project_id is None or metadata.get("project_id") == project_id)
            ):
                return conversation
        return None

    def mark_chunks_indexed(self, memory_id: str, chunk_ids: list[str]) -> None:
        conversation = self.rows[memory_id]
        targets = set(chunk_ids)
        for chunk in conversation["metadata"].get(MetadataField.INDEX_CHUNKS.value, []):
            if str(chunk.get(IndexChunkField.CHUNK_ID.value)) in targets:
                chunk[IndexChunkField.INDEX_STATE.value] = IndexState.INDEXED.value
        self._record_metadata_write(conversation)

    def upsert_generated_summary(self, summary: dict[str, Any]) -> None:
        self._generated_summaries[str(summary["id"])] = summary
        self._ledger.derived_rows_written += 1
        self._ledger.bytes_written += _json_size(summary)

    def append_audit_event(self, event: dict[str, Any]) -> None:
        self._ledger.audit_rows_written += 1
        self._ledger.bytes_written += _json_size(event)

    def _record_metadata_write(self, conversation: dict[str, Any]) -> None:
        self._ledger.metadata_rows_written += 1
        self._ledger.bytes_written += _json_size(conversation)


class _CountingVectorStore:
    def __init__(self, ledger: _WorkLedger) -> None:
        self._ledger = ledger
        self.rows: list[dict[str, Any]] = []

    def insert(
        self,
        memory_id: str,
        embeddings: list[dict[str, Any]],
        replace: bool = False,
    ) -> None:
        self._ledger.vector_write_calls += 1
        self._ledger.vector_rows_written += len(embeddings)
        self._ledger.bytes_written += _json_size(embeddings)
        if replace:
            self.rows = [row for row in self.rows if row["memory_id"] != memory_id]
        self.rows.extend({"memory_id": memory_id, **embedding} for embedding in embeddings)

    def get_indexed_chunk_ids(self, memory_id: str) -> list[str]:
        return [
            str(row[IndexChunkField.CHUNK_ID.value])
            for row in self.rows
            if row["memory_id"] == memory_id
        ]


def run_benchmark() -> dict[str, Any]:
    """Measure work for an accepted insert, duplicate, and trusted append."""
    original_runtime = mvp_ingestion._RUNTIME
    ledger = _WorkLedger()
    metadata_store = _CountingMetadataStore(ledger)
    vector_store = _CountingVectorStore(ledger)
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=_CountingEmbedder(ledger),
        metadata_store=metadata_store,
        vector_store=vector_store,
        health_state={
            "mode": "ok",
            "metadata_provider": "benchmark",
            "vector_provider": "memory",
            "embedding": {"provider": "benchmark", "model": "deterministic"},
        },
        allow_trusted_appends=True,
    )
    try:
        mvp_ingestion.configure_runtime(runtime=runtime)
        accepted_result = mvp_ingestion.ingest_messages(deepcopy(_PAYLOAD))
        accepted_work = ledger.snapshot()

        ledger.reset()
        duplicate = deepcopy(_PAYLOAD)
        duplicate["id"] = "00000000-0000-4000-8000-000000000102"
        duplicate_result = mvp_ingestion.ingest_messages(duplicate)
        duplicate_work = ledger.snapshot()

        ledger.reset()
        append_result = mvp_ingestion.ingest_messages(deepcopy(_APPEND_PAYLOAD))
        append_work = ledger.snapshot()
    finally:
        mvp_ingestion._RUNTIME = original_runtime

    message_count = len(_PAYLOAD["messages"])
    baseline_embedding_inputs = int(accepted_result["embedded_chunks"])
    previous_append_summary_writes = append_work["derived_rows_written"] * 2
    return {
        "measurement": "deterministic_work_amplification",
        "accepted": {
            "result": accepted_result,
            "work": accepted_work,
            "work_per_message": _per_message(accepted_work, message_count),
        },
        "duplicate": {
            "result": duplicate_result,
            "work": duplicate_work,
            "work_per_message": _per_message(duplicate_work, message_count),
        },
        "append": {
            "result": append_result,
            "work": append_work,
            "work_per_new_message": _per_message(
                append_work,
                int(append_result["appended_messages"]),
            ),
        },
        "previous_duplicate_path": {
            "embedding_inputs": baseline_embedding_inputs,
            "vector_rows_written": baseline_embedding_inputs,
        },
        "previous_append_path": {
            "summary_generation_passes": 2,
            "derived_rows_written": previous_append_summary_writes,
        },
        "reductions": {
            "embedding_inputs": _reduction(
                baseline_embedding_inputs,
                duplicate_work["embedding_inputs"],
            ),
            "vector_rows_written": _reduction(
                baseline_embedding_inputs,
                duplicate_work["vector_rows_written"],
            ),
            "append_summary_generation_passes": _reduction(2, 1),
            "append_derived_rows_written": _reduction(
                previous_append_summary_writes,
                append_work["derived_rows_written"],
            ),
        },
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    duplicate = report["duplicate"]
    work = duplicate["work"]
    result = duplicate["result"]
    if result.get("deduplicated") is not True:
        raise AssertionError("duplicate ingestion must report deduplicated=true")
    if work["embedding_inputs"] != 0:
        raise AssertionError("duplicate ingestion must perform zero embedding work")
    if work["vector_rows_written"] != 0:
        raise AssertionError("duplicate ingestion must perform zero vector-row writes")
    append = report["append"]
    if append["result"].get("appended_messages") != 1:
        raise AssertionError("append fixture must add exactly one message")
    if report["reductions"]["append_derived_rows_written"] < 0.5:
        raise AssertionError("append summary writes must fall by at least 50%")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure accepted, duplicate, and append ingestion work amplification."
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


def _json_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _per_message(work: dict[str, int], message_count: int) -> dict[str, float]:
    return {key: value / message_count for key, value in work.items()}


def _reduction(baseline: int, optimized: int) -> float:
    if baseline <= 0:
        return 0.0
    return 1.0 - (optimized / baseline)


if __name__ == "__main__":
    raise SystemExit(main())
