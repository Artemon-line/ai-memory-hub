from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any, Callable, Protocol

import pyarrow as pa

from memory.backend.contracts import ProviderCapabilities
from memory.backend.errors import NotSupportedError, VectorDimensionError

logger = logging.getLogger(__name__)


class VectorStore(Protocol):
    @property
    def expected_dimensionality(self) -> int: ...

    def insert(self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False) -> None: ...

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]: ...

    def delete(self, ids: list[str]) -> None: ...

    def get_stats(self) -> dict[str, Any]: ...

    def health(self) -> dict[str, Any]: ...


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
        self._index_ready = False

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def _open_or_create_table(self):
        schema = pa.schema(
            [
                pa.field("memory_id", pa.string()),
                pa.field("chunk_id", pa.string()),
                pa.field("chunk_index", pa.int64()),
                pa.field("message_hash", pa.string()),
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
            field_names = set(existing_schema.names)
            required_fields = {"memory_id", "chunk_id", "chunk_index", "message_hash", "role", "text", "vector"}
            if (
                required_fields.issubset(field_names)
                and isinstance(vector_type, pa.FixedSizeListType)
                and vector_type.list_size == self.dimension
            ):
                return existing_table

        # Create or overwrite table with correct schema
        table = self._db.create_table(
            self.table_name, schema=schema, mode="overwrite"
        )
        return table

    def insert(self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False) -> None:
        rows = []
        for item in embeddings:
            vector = [float(v) for v in item["vector"]]
            self._validate_dimension(vector, operation="insert")
            rows.append(
                {
                    "memory_id": metadata_id,
                    "chunk_id": str(item.get("chunk_id") or f"{metadata_id}:{int(item['chunk_index'])}"),
                    "chunk_index": int(item["chunk_index"]),
                    "message_hash": str(item.get("message_hash", "")),
                    "role": str(item["role"]),
                    "text": str(item["text"]),
                    "vector": vector,
                }
            )
        if rows:
            if replace:
                # Avoid duplicates if re-indexing
                self._table.delete(f"memory_id = '{metadata_id}'")
            self._table.add(rows)
            self._ensure_index()

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        self._validate_dimension(query_vector, operation="search")
        result = self._table.search(query_vector).limit(top_k).to_list()
        normalized: list[dict[str, Any]] = []
        for row in result:
            normalized.append(
                {
                    "memory_id": str(row.get("memory_id", "")),
                    "chunk_id": str(row.get("chunk_id", "")),
                    "chunk_index": int(row.get("chunk_index", 0)),
                    "message_hash": str(row.get("message_hash", "")),
                    "role": str(row.get("role", "")),
                    "text": str(row.get("text", "")),
                    "score": float(row.get("_distance", 0.0)),
                }
            )
        return normalized

    def delete(self, ids: list[str]) -> None:
        for memory_id in ids:
            self._table.delete(f"memory_id = '{memory_id}'")

    def get_stats(self) -> dict[str, Any]:
        try:
            row_count = int(self._table.count_rows())
        except Exception:
            row_count = None
        return {
            "provider": "lancedb",
            "rows": row_count,
            "expected_dimensionality": self.expected_dimensionality,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_metadata_indexing=True)

    def health(self) -> dict[str, Any]:
        return {
            "provider": "lancedb",
            "expected_dimensionality": self.expected_dimensionality,
            "stats": self.get_stats(),
        }

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = (memory_id, ttl_seconds)
        raise NotSupportedError("TTL is not supported by this vector adapter")

    def _validate_dimension(self, vector: list[float], *, operation: str) -> None:
        actual = len(vector)
        expected = self.expected_dimensionality
        if actual != expected:
            raise VectorDimensionError(
                f"Vector dimensionality mismatch during {operation}: expected {expected}, got {actual}"
            )

    def _ensure_index(self) -> None:
        if self._index_ready:
            return
        try:
            self._table.create_index(metric="cosine", index_type="IVF_HNSW_SQ")
            self._index_ready = True
        except RuntimeError as exc:
            logger.warning("LanceDB index creation deferred: %s", exc)


class InMemoryVectorStore:
    """Fallback implementation used for memory config and unit tests."""

    def __init__(self, dimension: int = 32):
        self.dimension = dimension
        self._rows: list[dict[str, Any]] = []

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def insert(self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False) -> None:
        if replace:
            # Avoid duplicates if re-indexing
            self._rows = [row for row in self._rows if row["memory_id"] != metadata_id]
        for item in embeddings:
            vector = [float(v) for v in item["vector"]]
            self._validate_dimension(vector, operation="insert")
            self._rows.append(
                {
                    "memory_id": metadata_id,
                    "chunk_id": str(item.get("chunk_id") or f"{metadata_id}:{int(item['chunk_index'])}"),
                    "chunk_index": int(item["chunk_index"]),
                    "message_hash": str(item.get("message_hash", "")),
                    "role": str(item["role"]),
                    "text": str(item["text"]),
                    "vector": vector,
                }
            )

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        self._validate_dimension(query_vector, operation="search")
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
                    "chunk_id": str(row.get("chunk_id", "")),
                    "chunk_index": int(row["chunk_index"]),
                    "message_hash": str(row.get("message_hash", "")),
                    "role": str(row["role"]),
                    "text": str(row["text"]),
                    "score": float(score),
                }
            )
        return output

    def delete(self, ids: list[str]) -> None:
        id_set = {str(item) for item in ids}
        self._rows = [row for row in self._rows if str(row["memory_id"]) not in id_set]

    def get_stats(self) -> dict[str, Any]:
        return {
            "provider": "memory",
            "rows": len(self._rows),
            "expected_dimensionality": self.expected_dimensionality,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def health(self) -> dict[str, Any]:
        return {
            "provider": "memory",
            "expected_dimensionality": self.expected_dimensionality,
            "stats": self.get_stats(),
        }

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = (memory_id, ttl_seconds)
        raise NotSupportedError("TTL is not supported by this vector adapter")

    def _validate_dimension(self, vector: list[float], *, operation: str) -> None:
        actual = len(vector)
        expected = self.expected_dimensionality
        if actual != expected:
            raise VectorDimensionError(
                f"Vector dimensionality mismatch during {operation}: expected {expected}, got {actual}"
            )


class PGVectorStore:
    _VALID_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    _SCHEMA_VERSION = 1
    _DISTANCE_SQL = {
        "cosine": ("<=>", "vector_cosine_ops"),
        "l2": ("<->", "vector_l2_ops"),
        "inner_product": ("<#>", "vector_ip_ops"),
    }

    def __init__(
        self,
        dsn: str,
        *,
        table_name: str = "memory_vectors",
        dimension: int = 32,
        distance: str = "cosine",
        connect_fn: Callable[[str], Any] | None = None,
    ):
        if not dsn:
            raise ValueError("Postgres DSN is required for providers.vector_db=pgvector")
        if not self._VALID_TABLE_RE.match(table_name):
            raise ValueError("PGVector table_name must be a simple SQL identifier")
        if distance not in self._DISTANCE_SQL:
            raise ValueError("PGVector distance must be one of: cosine, l2, inner_product")
        self._dsn = dsn
        self._connect_fn = connect_fn
        self.table_name = table_name
        self.dimension = dimension
        self.distance = distance
        self._operator, self._opclass = self._DISTANCE_SQL[distance]
        self._init_db()
        self.schema_version = self._read_schema_version()

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def _connect(self):
        if self._connect_fn is not None:
            return self._connect_fn(self._dsn)
        try:
            import importlib
            psycopg = importlib.import_module("psycopg")
        except ImportError as exc:
            raise RuntimeError("psycopg package is required for providers.vector_db=pgvector") from exc
        return psycopg.connect(self._dsn)

    def _init_db(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_vector_schema_version (
                        id SMALLINT PRIMARY KEY CHECK (id = 1),
                        version INTEGER NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    INSERT INTO memory_vector_schema_version (id, version)
                    VALUES (%s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (1, self._SCHEMA_VERSION),
                )
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.table_name} (
                        memory_id TEXT NOT NULL,
                        chunk_id TEXT NOT NULL,
                        chunk_index INTEGER NOT NULL,
                        message_hash TEXT NOT NULL DEFAULT '',
                        role TEXT NOT NULL,
                        text TEXT NOT NULL,
                        vector vector({self.dimension}) NOT NULL,
                        PRIMARY KEY (memory_id, chunk_id)
                    )
                    """
                )
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_{self.table_name}_hnsw
                    ON {self.table_name}
                    USING hnsw (vector {self._opclass})
                    """
                )

    def _read_schema_version(self) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version FROM memory_vector_schema_version WHERE id = %s", (1,))
                row = cur.fetchone()
        if row is None:
            raise RuntimeError("memory_vector_schema_version row missing for id=1")
        version = int(row[0])
        if version != self._SCHEMA_VERSION:
            raise RuntimeError(
                f"Incompatible vector schema version: {version}. Supported version: {self._SCHEMA_VERSION}"
            )
        return version

    def insert(self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False) -> None:
        rows: list[tuple[str, str, int, str, str, str, str]] = []
        for item in embeddings:
            vector = [float(v) for v in item["vector"]]
            self._validate_dimension(vector, operation="insert")
            rows.append(
                (
                    metadata_id,
                    str(item.get("chunk_id") or f"{metadata_id}:{int(item['chunk_index'])}"),
                    int(item["chunk_index"]),
                    str(item.get("message_hash", "")),
                    str(item["role"]),
                    str(item["text"]),
                    _vector_literal(vector),
                )
            )
        with self._connect() as conn:
            with conn.cursor() as cur:
                if replace:
                    cur.execute(f"DELETE FROM {self.table_name} WHERE memory_id = %s", (metadata_id,))
                for row in rows:
                    cur.execute(
                        f"""
                        INSERT INTO {self.table_name}
                            (memory_id, chunk_id, chunk_index, message_hash, role, text, vector)
                        VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                        ON CONFLICT (memory_id, chunk_id) DO UPDATE
                        SET chunk_index = EXCLUDED.chunk_index,
                            message_hash = EXCLUDED.message_hash,
                            role = EXCLUDED.role,
                            text = EXCLUDED.text,
                            vector = EXCLUDED.vector
                        """,
                        row,
                    )

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        self._validate_dimension(query_vector, operation="search")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT memory_id, chunk_id, chunk_index, message_hash, role, text,
                           vector {self._operator} %s::vector AS distance
                    FROM {self.table_name}
                    ORDER BY vector {self._operator} %s::vector
                    LIMIT %s
                    """,
                    (_vector_literal(query_vector), _vector_literal(query_vector), int(top_k)),
                )
                rows = cur.fetchall()
        return [
            {
                "memory_id": str(row[0]),
                "chunk_id": str(row[1]),
                "chunk_index": int(row[2]),
                "message_hash": str(row[3]),
                "role": str(row[4]),
                "text": str(row[5]),
                "score": float(row[6]),
            }
            for row in rows
        ]

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self.table_name} WHERE memory_id = ANY(%s)", ([str(item) for item in ids],))

    def get_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {self.table_name}")
                row = cur.fetchone()
        return {
            "provider": "pgvector",
            "rows": int(row[0]) if row is not None else 0,
            "expected_dimensionality": self.expected_dimensionality,
            "distance": self.distance,
            "schema_version": self.schema_version,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_batch_insert=True,
            supports_metadata_indexing=True,
        )

    def health(self) -> dict[str, Any]:
        return {
            "provider": "pgvector",
            "expected_dimensionality": self.expected_dimensionality,
            "distance": self.distance,
            "schema_version": self.schema_version,
            "stats": self.get_stats(),
        }

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = (memory_id, ttl_seconds)
        raise NotSupportedError("TTL is not supported by this vector adapter")

    def _validate_dimension(self, vector: list[float], *, operation: str) -> None:
        actual = len(vector)
        expected = self.expected_dimensionality
        if actual != expected:
            raise VectorDimensionError(
                f"Vector dimensionality mismatch during {operation}: expected {expected}, got {actual}"
            )


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


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(float(value)) for value in vector) + "]"
