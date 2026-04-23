from __future__ import annotations

import math
from pathlib import Path
from typing import Any
import pyarrow as pa


class LanceDBVectorStore:
    def __init__(self, db_path: str | Path, table_name: str = "memory_vectors", dimension: int = 32):
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.table_name = table_name
        self.dimension = dimension

        try:
            import lancedb
        except ImportError as exc:
            raise RuntimeError(
                "lancedb package is required for vector_db=lancedb"
            ) from exc

        self._db = lancedb.connect(str(self.db_path))
        self._table = self._open_or_create_table()

    def _open_or_create_table(self):
        schema = pa.schema(
            [
                pa.field("memory_id", pa.string()),
                pa.field("chunk_index", pa.int64()),
                pa.field("role", pa.string()),
                pa.field("text", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), self.dimension)),
            ]
        )

        # reuse existing table if possible
        names = {
            item[0] if isinstance(item, (list, tuple)) else item
            for item in self._db.list_tables()
        }
        if self.table_name in names:
            existing_table = self._db.open_table(self.table_name)
            existing_schema = existing_table.schema
            # Check if vector field is FixedSizeList with correct dimension
            vector_field = existing_schema.field("vector")
            vector_type = vector_field.type
            if isinstance(vector_type, pa.FixedSizeListType) and vector_type.list_size == self.dimension:
                return existing_table

        # Create or overwrite table with correct schema
        print("DB is gone")
        table = self._db.create_table(
            self.table_name, schema=schema, mode="overwrite"
        )
        table.create_index(metric="cosine")
        return table

    def insert(self, metadata_id: str, embeddings: list[dict[str, Any]]) -> None:
        rows = []
        for item in embeddings:
            rows.append(
                {
                    "memory_id": metadata_id,
                    "chunk_index": int(item["chunk_index"]),
                    "role": str(item["role"]),
                    "text": str(item["text"]),
                    "vector": [float(v) for v in item["vector"]],
                }
            )
        if rows:
            self._table.add(rows)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        result = self._table.search(query_vector).limit(top_k).to_list()
        normalized: list[dict[str, Any]] = []
        for row in result:
            normalized.append(
                {
                    "memory_id": str(row.get("memory_id", "")),
                    "chunk_index": int(row.get("chunk_index", 0)),
                    "role": str(row.get("role", "")),
                    "text": str(row.get("text", "")),
                    "score": float(row.get("_distance", 0.0)),
                }
            )
        return normalized


class InMemoryVectorStore:
    """Fallback implementation used for pgvector config and unit tests."""

    def __init__(self):
        self._rows: list[dict[str, Any]] = []

    def insert(self, metadata_id: str, embeddings: list[dict[str, Any]]) -> None:
        for item in embeddings:
            self._rows.append(
                {
                    "memory_id": metadata_id,
                    "chunk_index": int(item["chunk_index"]),
                    "role": str(item["role"]),
                    "text": str(item["text"]),
                    "vector": [float(v) for v in item["vector"]],
                }
            )

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in self._rows:
            score = _cosine_distance(query_vector, row["vector"])
            scored.append((score, row))

        scored.sort(key=lambda item: item[0])
        output: list[dict[str, Any]] = []
        for score, row in scored[:top_k]:
            output.append(
                {
                    "memory_id": str(row["memory_id"]),
                    "chunk_index": int(row["chunk_index"]),
                    "role": str(row["role"]),
                    "text": str(row["text"]),
                    "score": float(score),
                }
            )
        return output


def _cosine_distance(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"Vector dimensions must match: {len(a)} vs {len(b)}")
    if not a:
        return 1.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for av, bv in zip(a, b):
        dot += av * bv
        norm_a += av * av
        norm_b += bv * bv

    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0

    cosine = dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
    return 1.0 - cosine
