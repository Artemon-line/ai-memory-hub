from __future__ import annotations

import logging
import math
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import pyarrow as pa
from pydantic import BaseModel, ConfigDict

from memory.backend.contracts import ProviderCapabilities
from memory.backend.errors import NotSupportedError, VectorDimensionError
from memory.provider_models import (
    ChromaClientMode,
    ChromaQueryKey,
    QdrantDistanceName,
    VectorPayloadKey,
    VectorProviderName,
    VectorStatsKey,
)

logger = logging.getLogger(__name__)


class ChromaMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    memory_id: str
    chunk_id: str
    chunk_index: int
    message_hash: str = ""
    role: str = ""
    text: str = ""
    owner_id: str = ""
    project_id: str = ""

    @classmethod
    def from_embedding(cls, metadata_id: str, item: dict[str, Any]) -> "ChromaMetadata":
        return cls(
            memory_id=str(metadata_id),
            chunk_id=str(item[VectorPayloadKey.CHUNK_ID.value]),
            chunk_index=int(item.get(VectorPayloadKey.CHUNK_INDEX.value, 0)),
            message_hash=str(item.get(VectorPayloadKey.MESSAGE_HASH.value, "")),
            role=str(item.get(VectorPayloadKey.ROLE.value, "")),
            text=str(item.get(VectorPayloadKey.TEXT.value, "")),
            owner_id=str(item.get(VectorPayloadKey.OWNER_ID.value, "")),
            project_id=str(item.get(VectorPayloadKey.PROJECT_ID.value, "")),
        )

    @classmethod
    def from_query(cls, metadata: dict[str, Any], *, fallback_chunk_id: Any) -> "ChromaMetadata":
        payload = dict(metadata)
        payload[VectorPayloadKey.CHUNK_ID.value] = str(
            payload.get(VectorPayloadKey.CHUNK_ID.value) or fallback_chunk_id
        )
        return cls.model_validate(payload)


class VectorStore(Protocol):
    @property
    def expected_dimensionality(self) -> int: ...

    def insert(self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False) -> None: ...

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]: ...

    def delete(self, ids: list[str]) -> None: ...

    def get_stats(self) -> dict[str, Any]: ...

    def health(self) -> dict[str, Any]: ...


class ChromaDBVectorStore:
    def __init__(
        self,
        *,
        path: str | Path = "./data/chromadb",
        url: str = "",
        host: str = "127.0.0.1",
        port: int = 8000,
        collection_name: str = "memory_vectors",
        dimension: int = 32,
        client: Any | None = None,
    ):
        self.path = Path(path)
        self.url = url
        self.host = host
        self.port = port
        self.collection_name = collection_name
        self.dimension = dimension
        self._client = client or self._create_client()
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def insert(
        self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False
    ) -> None:
        if replace:
            self.delete([metadata_id])
        ids: list[str] = []
        vectors: list[list[float]] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for item in embeddings:
            vector = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            self._validate_dimension(vector, operation="insert")
            metadata = ChromaMetadata.from_embedding(metadata_id, item)
            ids.append(metadata.chunk_id)
            vectors.append(vector)
            documents.append(metadata.text)
            metadatas.append(metadata.model_dump(mode="json"))
        if ids:
            self._collection.upsert(
                ids=ids,
                embeddings=vectors,
                documents=documents,
                metadatas=metadatas,
            )

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        self._validate_dimension(query_vector, operation="search")
        result = self._collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            include=[
                ChromaQueryKey.METADATAS.value,
                ChromaQueryKey.DOCUMENTS.value,
                ChromaQueryKey.DISTANCES.value,
            ],
        )
        return self._normalize_query_result(result)

    def delete(self, ids: list[str]) -> None:
        for memory_id in ids:
            self._collection.delete(
                where={VectorPayloadKey.MEMORY_ID.value: str(memory_id)}
            )

    def get_stats(self) -> dict[str, Any]:
        try:
            rows = int(self._collection.count())
        except Exception:
            rows = None
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.CHROMADB.value,
            VectorStatsKey.ROWS.value: rows,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            VectorStatsKey.MODE.value: (
                ChromaClientMode.HTTP.value
                if self.url
                else ChromaClientMode.PERSISTENT.value
            ),
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_metadata_indexing=True)

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.CHROMADB.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            VectorStatsKey.STATS.value: self.get_stats(),
        }

    def _create_client(self) -> Any:
        try:
            import chromadb  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "chromadb package is required for providers.vector_db=chromadb"
            ) from exc
        if self.url:
            parsed = urlparse(self.url)
            return chromadb.HttpClient(
                host=parsed.hostname or self.host,
                port=parsed.port or self.port,
                ssl=parsed.scheme == "https",
            )
        self.path.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(path=str(self.path))

    def _normalize_query_result(self, result: dict[str, Any]) -> list[dict[str, Any]]:
        ids = _first_query_batch(result.get(ChromaQueryKey.IDS.value, []))
        metadatas = _first_query_batch(result.get(ChromaQueryKey.METADATAS.value, []))
        documents = _first_query_batch(result.get(ChromaQueryKey.DOCUMENTS.value, []))
        distances = _first_query_batch(result.get(ChromaQueryKey.DISTANCES.value, []))
        rows: list[dict[str, Any]] = []
        for index, chunk_id in enumerate(ids):
            metadata_payload = (
                dict(metadatas[index] or {}) if index < len(metadatas) else {}
            )
            metadata = ChromaMetadata.from_query(
                metadata_payload, fallback_chunk_id=chunk_id
            )
            distance = float(distances[index]) if index < len(distances) else 0.0
            text = (
                str(documents[index])
                if index < len(documents) and documents[index] is not None
                else metadata.text
            )
            row = {
                VectorPayloadKey.MEMORY_ID.value: metadata.memory_id,
                VectorPayloadKey.CHUNK_ID.value: metadata.chunk_id,
                VectorPayloadKey.CHUNK_INDEX.value: metadata.chunk_index,
                VectorPayloadKey.MESSAGE_HASH.value: metadata.message_hash,
                VectorPayloadKey.ROLE.value: metadata.role,
                VectorPayloadKey.TEXT.value: text,
                VectorPayloadKey.SCORE.value: 1.0 - distance,
            }
            if metadata.owner_id:
                row[VectorPayloadKey.OWNER_ID.value] = metadata.owner_id
            if metadata.project_id:
                row[VectorPayloadKey.PROJECT_ID.value] = metadata.project_id
            rows.append(row)
        return rows

    def _validate_dimension(self, vector: list[float], *, operation: str) -> None:
        expected = self.expected_dimensionality
        actual = len(vector)
        if actual != expected:
            raise VectorDimensionError(
                f"Vector dimensionality mismatch during {operation}: expected {expected}, got {actual}"
            )


class QdrantVectorStore:
    _DISTANCE_MAP = {
        "cosine": QdrantDistanceName.COSINE.value,
        "l2": QdrantDistanceName.L2.value,
        "inner_product": QdrantDistanceName.INNER_PRODUCT.value,
    }

    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:6333",
        api_key: str = "",
        collection_name: str = "memory_vectors",
        dimension: int = 32,
        distance: str = "cosine",
        prefer_grpc: bool = False,
        client: Any | None = None,
        models: Any | None = None,
    ):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.collection_name = collection_name
        self.dimension = dimension
        self.distance = distance
        self._client: Any
        self._models: Any
        if client is None:
            self._client, self._models = self._create_client(prefer_grpc)
        else:
            if models is None:
                raise ValueError("Qdrant models are required when injecting a Qdrant client")
            self._client = client
            self._models = models
        self._ensure_collection()

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def insert(
        self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False
    ) -> None:
        if replace:
            self.delete([metadata_id])
        points = []
        for item in embeddings:
            vector = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            self._validate_dimension(vector, operation="insert")
            payload = _vector_payload(metadata_id, item)
            points.append(
                self._models.PointStruct(
                    id=_stable_point_id(metadata_id, payload[VectorPayloadKey.CHUNK_ID.value]),
                    vector=vector,
                    payload=payload,
                )
            )
        if points:
            self._client.upsert(collection_name=self.collection_name, points=points)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        self._validate_dimension(query_vector, operation="search")
        points = self._search_points(query_vector=query_vector, top_k=top_k)
        rows: list[dict[str, Any]] = []
        for point in points:
            payload = dict(getattr(point, "payload", {}) or {})
            row = _row_from_vector_payload(payload)
            row[VectorPayloadKey.SCORE.value] = float(getattr(point, "score", 0.0))
            rows.append(row)
        return rows

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        condition = self._models.FieldCondition(
            key=VectorPayloadKey.MEMORY_ID.value,
            match=self._models.MatchAny(any=[str(item) for item in ids]),
        )
        self._client.delete(
            collection_name=self.collection_name,
            points_selector=self._models.FilterSelector(
                filter=self._models.Filter(must=[condition])
            ),
        )

    def get_stats(self) -> dict[str, Any]:
        try:
            info = self._client.get_collection(collection_name=self.collection_name)
            rows = int(getattr(info, "points_count", 0) or 0)
        except Exception:
            rows = None
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.QDRANT.value,
            VectorStatsKey.ROWS.value: rows,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            "distance": self.distance,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_batch_insert=True,
            supports_metadata_indexing=True,
        )

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.QDRANT.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            "distance": self.distance,
            VectorStatsKey.STATS.value: self.get_stats(),
        }

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = (memory_id, ttl_seconds)
        raise NotSupportedError("TTL is not supported by this vector adapter")

    def _create_client(self, prefer_grpc: bool) -> tuple[Any, Any]:
        try:
            from qdrant_client import QdrantClient, models  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "qdrant-client package is required for providers.vector_db=qdrant"
            ) from exc
        kwargs: dict[str, Any] = {"url": self.url, "prefer_grpc": prefer_grpc}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return QdrantClient(**kwargs), models

    def _ensure_collection(self) -> None:
        if self._collection_exists():
            return
        distance = self._DISTANCE_MAP.get(self.distance)
        if distance is None:
            raise ValueError("Qdrant distance must be one of: cosine, l2, inner_product")
        self._client.create_collection(
            collection_name=self.collection_name,
            vectors_config=self._models.VectorParams(
                size=self.expected_dimensionality,
                distance=distance,
            ),
        )

    def _collection_exists(self) -> bool:
        if hasattr(self._client, "collection_exists"):
            return bool(self._client.collection_exists(self.collection_name))
        collections = self._client.get_collections()
        names = {str(item.name) for item in getattr(collections, "collections", [])}
        return self.collection_name in names

    def _search_points(self, *, query_vector: list[float], top_k: int) -> list[Any]:
        if hasattr(self._client, "query_points"):
            result = self._client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=top_k,
                with_payload=True,
            )
            return list(getattr(result, "points", result) or [])
        return list(
            self._client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=top_k,
                with_payload=True,
            )
            or []
        )

    def _validate_dimension(self, vector: list[float], *, operation: str) -> None:
        expected = self.expected_dimensionality
        actual = len(vector)
        if actual != expected:
            raise VectorDimensionError(
                f"Vector dimensionality mismatch during {operation}: expected {expected}, got {actual}"
            )


class MongoDBAtlasVectorStore:
    def __init__(
        self,
        *,
        uri: str,
        database: str = "ai_memory_hub",
        collection_name: str = "memory_vectors",
        index_name: str = "memory_vector_index",
        dimension: int = 32,
        client: Any | None = None,
    ):
        if not uri and client is None:
            raise ValueError("MongoDB Atlas URI is required for providers.vector_db=mongodb_atlas")
        self.uri = uri
        self.database = database
        self.collection_name = collection_name
        self.index_name = index_name
        self.dimension = dimension
        self._client = client or self._create_client()
        self._collection = self._client[self.database][self.collection_name]
        self._ensure_indexes()

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def insert(
        self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False
    ) -> None:
        if replace:
            self.delete([metadata_id])
        docs = []
        for item in embeddings:
            vector = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            self._validate_dimension(vector, operation="insert")
            docs.append(
                {
                    **_vector_payload(metadata_id, item),
                    VectorPayloadKey.VECTOR.value: vector,
                }
            )
        if docs:
            self._collection.insert_many(docs)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        self._validate_dimension(query_vector, operation="search")
        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.index_name,
                    "path": VectorPayloadKey.VECTOR.value,
                    "queryVector": query_vector,
                    "numCandidates": max(top_k * 10, top_k),
                    "limit": top_k,
                }
            },
            {"$set": {VectorPayloadKey.SCORE.value: {"$meta": "vectorSearchScore"}}},
        ]
        return [
            {
                **_row_from_vector_payload(dict(doc)),
                VectorPayloadKey.SCORE.value: float(doc.get(VectorPayloadKey.SCORE.value, 0.0)),
            }
            for doc in self._collection.aggregate(pipeline)
        ]

    def delete(self, ids: list[str]) -> None:
        if ids:
            self._collection.delete_many(
                {VectorPayloadKey.MEMORY_ID.value: {"$in": [str(item) for item in ids]}}
            )

    def get_stats(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.MONGODB_ATLAS.value,
            VectorStatsKey.ROWS.value: int(self._collection.count_documents({})),
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            "index": self.index_name,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_batch_insert=True,
            supports_metadata_indexing=True,
        )

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.MONGODB_ATLAS.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            "index": self.index_name,
            VectorStatsKey.STATS.value: self.get_stats(),
        }

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = (memory_id, ttl_seconds)
        raise NotSupportedError("TTL is not supported by this vector adapter")

    def _create_client(self) -> Any:
        try:
            from pymongo import MongoClient  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "pymongo package is required for providers.vector_db=mongodb_atlas"
            ) from exc
        return MongoClient(self.uri)

    def _ensure_indexes(self) -> None:
        self._collection.create_index(VectorPayloadKey.MEMORY_ID.value)
        self._collection.create_index(VectorPayloadKey.CHUNK_ID.value)

    def _validate_dimension(self, vector: list[float], *, operation: str) -> None:
        expected = self.expected_dimensionality
        actual = len(vector)
        if actual != expected:
            raise VectorDimensionError(
                f"Vector dimensionality mismatch during {operation}: expected {expected}, got {actual}"
            )


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
                pa.field("project_id", pa.string()),
                pa.field("owner_id", pa.string()),
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
            required_fields = {
                "memory_id",
                "project_id",
                "owner_id",
                "chunk_id",
                "chunk_index",
                "message_hash",
                "role",
                "text",
                "vector",
            }
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
                    "project_id": str(item.get("project_id", "")),
                    "owner_id": str(item.get("owner_id", "")),
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
                    "project_id": str(row.get("project_id", "")),
                    "owner_id": str(row.get("owner_id", "")),
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
                    "project_id": str(item.get("project_id", "")),
                    "owner_id": str(item.get("owner_id", "")),
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
                    "project_id": str(row.get("project_id", "")),
                    "owner_id": str(row.get("owner_id", "")),
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
                        project_id TEXT NOT NULL DEFAULT '',
                        owner_id TEXT NOT NULL DEFAULT '',
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
                cur.execute(f"ALTER TABLE {self.table_name} ADD COLUMN IF NOT EXISTS project_id TEXT NOT NULL DEFAULT ''")
                cur.execute(f"ALTER TABLE {self.table_name} ADD COLUMN IF NOT EXISTS owner_id TEXT NOT NULL DEFAULT ''")
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
        rows: list[tuple[str, str, str, str, int, str, str, str, str]] = []
        for item in embeddings:
            vector = [float(v) for v in item["vector"]]
            self._validate_dimension(vector, operation="insert")
            rows.append(
                (
                    metadata_id,
                    str(item.get("project_id", "")),
                    str(item.get("owner_id", "")),
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
                            (memory_id, project_id, owner_id, chunk_id, chunk_index, message_hash, role, text, vector)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                        ON CONFLICT (memory_id, chunk_id) DO UPDATE
                        SET project_id = EXCLUDED.project_id,
                            owner_id = EXCLUDED.owner_id,
                            chunk_index = EXCLUDED.chunk_index,
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
                    SELECT memory_id, chunk_id, project_id, owner_id, chunk_index, message_hash, role, text,
                           vector {self._operator} %s::vector AS distance
                    FROM {self.table_name}
                    ORDER BY vector {self._operator} %s::vector
                    LIMIT %s
                    """,
                    (_vector_literal(query_vector), _vector_literal(query_vector), int(top_k)),
                )
                rows = cur.fetchall()
        return [self._search_row(row) for row in rows]

    def _search_row(self, row: Any) -> dict[str, Any]:
        if len(row) == 7:
            memory_id, chunk_id, chunk_index, message_hash, role, text, score = row
            project_id = ""
            owner_id = ""
        elif len(row) == 8:
            memory_id, chunk_id, project_id, chunk_index, message_hash, role, text, score = row
            owner_id = ""
        else:
            memory_id, chunk_id, project_id, owner_id, chunk_index, message_hash, role, text, score = row
        result = {
            "memory_id": str(memory_id),
            "chunk_id": str(chunk_id),
            "chunk_index": int(chunk_index),
            "message_hash": str(message_hash),
            "role": str(role),
            "text": str(text),
            "score": float(score),
        }
        if project_id:
            result["project_id"] = str(project_id)
        if owner_id:
            result["owner_id"] = str(owner_id)
        return result

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


def _stable_point_id(memory_id: str, chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-memory-hub:{memory_id}:{chunk_id}"))


def _vector_payload(metadata_id: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        VectorPayloadKey.MEMORY_ID.value: metadata_id,
        VectorPayloadKey.PROJECT_ID.value: str(item.get(VectorPayloadKey.PROJECT_ID.value, "")),
        VectorPayloadKey.OWNER_ID.value: str(item.get(VectorPayloadKey.OWNER_ID.value, "")),
        VectorPayloadKey.CHUNK_ID.value: str(
            item.get(VectorPayloadKey.CHUNK_ID.value)
            or f"{metadata_id}:{int(item[VectorPayloadKey.CHUNK_INDEX.value])}"
        ),
        VectorPayloadKey.CHUNK_INDEX.value: int(item[VectorPayloadKey.CHUNK_INDEX.value]),
        VectorPayloadKey.MESSAGE_HASH.value: str(item.get(VectorPayloadKey.MESSAGE_HASH.value, "")),
        VectorPayloadKey.ROLE.value: str(item[VectorPayloadKey.ROLE.value]),
        VectorPayloadKey.TEXT.value: str(item[VectorPayloadKey.TEXT.value]),
    }


def _row_from_vector_payload(payload: dict[str, Any]) -> dict[str, Any]:
    row = {
        VectorPayloadKey.MEMORY_ID.value: str(payload.get(VectorPayloadKey.MEMORY_ID.value, "")),
        VectorPayloadKey.CHUNK_ID.value: str(payload.get(VectorPayloadKey.CHUNK_ID.value, "")),
        VectorPayloadKey.CHUNK_INDEX.value: int(payload.get(VectorPayloadKey.CHUNK_INDEX.value, 0)),
        VectorPayloadKey.MESSAGE_HASH.value: str(payload.get(VectorPayloadKey.MESSAGE_HASH.value, "")),
        VectorPayloadKey.ROLE.value: str(payload.get(VectorPayloadKey.ROLE.value, "")),
        VectorPayloadKey.TEXT.value: str(payload.get(VectorPayloadKey.TEXT.value, "")),
    }
    project_id = str(payload.get(VectorPayloadKey.PROJECT_ID.value, ""))
    owner_id = str(payload.get(VectorPayloadKey.OWNER_ID.value, ""))
    if project_id:
        row[VectorPayloadKey.PROJECT_ID.value] = project_id
    if owner_id:
        row[VectorPayloadKey.OWNER_ID.value] = owner_id
    return row


def _first_query_batch(value: Any) -> list[Any]:
    if isinstance(value, list) and value and isinstance(value[0], list):
        return value[0]
    if isinstance(value, list):
        return value
    return []
