from __future__ import annotations

import argparse
import json
from typing import Any

from memory.ingestion import mvp_ingestion

_TEXTS = [f"item-{index:02d}" for index in range(10)]


class _BoundedEmbeddingProvider:
    dimension = 2
    max_batch_size = 3
    max_batch_input_bytes = 32

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(int(text[-2:])), float(len(text))] for text in texts]


def run_benchmark() -> dict[str, Any]:
    original_runtime = mvp_ingestion._RUNTIME
    provider = _BoundedEmbeddingProvider()
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=provider,
        metadata_store=object(),
        vector_store=object(),
        health_state={"embedding": {"provider": "benchmark", "model": "deterministic"}},
    )
    chunks = [
        {
            "chunk_id": f"chunk-{index}",
            "chunk_index": index,
            "message_hash": f"hash-{index}",
            "role": "user",
            "text": text,
        }
        for index, text in enumerate(_TEXTS)
    ]
    try:
        mvp_ingestion.configure_runtime(runtime=runtime)
        embeddings = mvp_ingestion.embed_chunks(chunks)
    finally:
        mvp_ingestion._RUNTIME = original_runtime

    largest_batch = max(len(batch) for batch in provider.calls)
    previous_largest_batch = len(_TEXTS)
    return {
        "previous_unbounded_path": {
            "embedding_calls": 1,
            "largest_batch_inputs": previous_largest_batch,
        },
        "bounded_path": {
            "embedding_calls": len(provider.calls),
            "batch_input_counts": [len(batch) for batch in provider.calls],
            "batch_input_bytes": [
                mvp_ingestion._embedding_batch_input_bytes(batch) for batch in provider.calls
            ],
            "largest_batch_inputs": largest_batch,
        },
        "largest_batch_reduction": 1.0 - (largest_batch / previous_largest_batch),
        "mapping_preserved": [row["vector"][0] for row in embeddings]
        == [float(index) for index in range(len(_TEXTS))],
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    bounded = report["bounded_path"]
    if bounded["largest_batch_inputs"] > _BoundedEmbeddingProvider.max_batch_size:
        raise AssertionError("embedding batch count limit was exceeded")
    if max(bounded["batch_input_bytes"]) > _BoundedEmbeddingProvider.max_batch_input_bytes:
        raise AssertionError("embedding batch byte limit was exceeded")
    if report["largest_batch_reduction"] < 0.5:
        raise AssertionError("largest embedding request must shrink by at least 50%")
    if not report["mapping_preserved"]:
        raise AssertionError("embedding-to-chunk mapping changed across batches")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure bounded embedding request work.")
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
