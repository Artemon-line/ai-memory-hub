from __future__ import annotations

import logging
import math
import re
import uuid
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Protocol, cast
from urllib.parse import urlparse

import pyarrow as pa
from pydantic import BaseModel, ConfigDict

from memory.backend.contracts import ProviderCapabilities
from memory.backend.errors import NotSupportedError, VectorDimensionError
from memory.provider_models import (
    ChromaClientMode,
    ChromaQueryKey,
    MilvusMetricType,
    OpenSearchSpaceType,
    QdrantDistanceName,
    RedisVectorField,
    SearchQueryKey,
    SearchSimilarityName,
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


class MilvusVectorStore:
    _METRIC_MAP = {
        "cosine": MilvusMetricType.COSINE.value,
        "l2": MilvusMetricType.L2.value,
        "inner_product": MilvusMetricType.INNER_PRODUCT.value,
    }

    def __init__(
        self,
        *,
        uri: str = "http://127.0.0.1:19530",
        token: str = "",
        collection_name: str = "memory_vectors",
        dimension: int = 32,
        distance: str = "cosine",
        client: Any | None = None,
    ):
        self.uri = uri.rstrip("/")
        self.token = token
        self.collection_name = collection_name
        self.dimension = dimension
        self.distance = distance
        self._client = client or self._create_client()
        self._ensure_collection()

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def insert(
        self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False
    ) -> None:
        if replace:
            self.delete([metadata_id])
        rows = []
        for item in embeddings:
            vector = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            _validate_vector_length(
                vector, expected=self.expected_dimensionality, operation="insert"
            )
            payload = _vector_payload(metadata_id, item)
            rows.append(
                {
                    "id": _stable_point_id(
                        metadata_id, payload[VectorPayloadKey.CHUNK_ID.value]
                    ),
                    **payload,
                    VectorPayloadKey.VECTOR.value: vector,
                }
            )
        if rows:
            self._client.upsert(collection_name=self.collection_name, data=rows)
            self._flush_collection()

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        _validate_vector_length(
            query_vector, expected=self.expected_dimensionality, operation="search"
        )
        result = self._client.search(
            collection_name=self.collection_name,
            data=[query_vector],
            anns_field=VectorPayloadKey.VECTOR.value,
            limit=top_k,
            output_fields=_vector_payload_fields(),
        )
        return self._normalize_search_result(result)

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        quoted = ", ".join(f'"{str(item)}"' for item in ids)
        self._client.delete(
            collection_name=self.collection_name,
            filter=f"{VectorPayloadKey.MEMORY_ID.value} in [{quoted}]",
        )
        self._flush_collection()

    def get_stats(self) -> dict[str, Any]:
        rows = None
        try:
            stats = self._client.get_collection_stats(
                collection_name=self.collection_name
            )
            if isinstance(stats, dict):
                rows = int(stats.get("row_count", stats.get("num_entities", 0)) or 0)
        except Exception:
            rows = None
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.MILVUS.value,
            VectorStatsKey.ROWS.value: rows,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            VectorStatsKey.DISTANCE.value: self.distance,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_batch_insert=True,
            supports_metadata_indexing=True,
        )

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.MILVUS.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            VectorStatsKey.DISTANCE.value: self.distance,
            VectorStatsKey.STATS.value: self.get_stats(),
        }

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = (memory_id, ttl_seconds)
        raise NotSupportedError("TTL is not supported by this vector adapter")

    def _create_client(self) -> Any:
        try:
            from pymilvus import MilvusClient  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "pymilvus package is required for providers.vector_db=milvus"
            ) from exc
        kwargs: dict[str, Any] = {"uri": self.uri}
        if self.token:
            kwargs["token"] = self.token
        return MilvusClient(**kwargs)

    def _ensure_collection(self) -> None:
        if self._client.has_collection(collection_name=self.collection_name):
            return
        metric_type = self._METRIC_MAP.get(self.distance)
        if metric_type is None:
            raise ValueError("Milvus distance must be one of: cosine, l2, inner_product")
        self._client.create_collection(
            collection_name=self.collection_name,
            dimension=self.expected_dimensionality,
            metric_type=metric_type,
            id_type="string",
            max_length=64,
            auto_id=False,
        )

    def _flush_collection(self) -> None:
        flush = getattr(self._client, "flush", None)
        if flush is not None:
            flush(collection_name=self.collection_name)

    def _normalize_search_result(self, result: Any) -> list[dict[str, Any]]:
        hits = _first_query_batch(result)
        rows: list[dict[str, Any]] = []
        for hit in hits:
            entity_value = _mapping_from_object_or_dict(hit, "entity") or {}
            entity = dict(entity_value)
            if not entity:
                entity = dict(hit) if isinstance(hit, dict) else {}
            row = _row_from_vector_payload(entity)
            score = _mapping_from_object_or_dict(hit, "distance")
            if score is None:
                score = _mapping_from_object_or_dict(hit, VectorPayloadKey.SCORE.value)
            if score is None:
                score = 0.0
            row[VectorPayloadKey.SCORE.value] = float(score)
            rows.append(row)
        return rows


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


class ElasticsearchVectorStore:
    _SIMILARITY_MAP = {
        "cosine": SearchSimilarityName.COSINE.value,
        "l2": SearchSimilarityName.L2.value,
        "inner_product": SearchSimilarityName.INNER_PRODUCT.value,
    }

    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:9200",
        username: str = "",
        password: str = "",
        index_name: str = "memory_vectors",
        dimension: int = 32,
        distance: str = "cosine",
        client: Any | None = None,
    ):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.index_name = index_name
        self.dimension = dimension
        self.distance = distance
        self._client = client or self._create_client()
        self._ensure_index()

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def insert(
        self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False
    ) -> None:
        if replace:
            self.delete([metadata_id])
        for item in embeddings:
            vector = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            _validate_vector_length(
                vector, expected=self.expected_dimensionality, operation="insert"
            )
            payload = _vector_payload(metadata_id, item)
            document = {**payload, VectorPayloadKey.VECTOR.value: vector}
            self._client.index(
                index=self.index_name,
                id=_stable_point_id(
                    metadata_id, payload[VectorPayloadKey.CHUNK_ID.value]
                ),
                document=document,
                refresh=True,
            )

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        _validate_vector_length(
            query_vector, expected=self.expected_dimensionality, operation="search"
        )
        result = self._client.search(
            index=self.index_name,
            knn={
                SearchQueryKey.FIELD.value: VectorPayloadKey.VECTOR.value,
                SearchQueryKey.QUERY_VECTOR.value: query_vector,
                SearchQueryKey.K.value: top_k,
                SearchQueryKey.NUM_CANDIDATES.value: max(top_k * 10, top_k),
            },
            size=top_k,
        )
        return _rows_from_search_hits(result)

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        self._client.delete_by_query(
            index=self.index_name,
            query={
                SearchQueryKey.TERMS.value: {
                    VectorPayloadKey.MEMORY_ID.value: [str(item) for item in ids]
                }
            },
            refresh=True,
            conflicts="proceed",
        )

    def get_stats(self) -> dict[str, Any]:
        rows = None
        try:
            result = self._client.count(index=self.index_name)
            rows = int(result.get("count", 0))
        except Exception:
            rows = None
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.ELASTICSEARCH.value,
            VectorStatsKey.ROWS.value: rows,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.INDEX.value: self.index_name,
            VectorStatsKey.DISTANCE.value: self.distance,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_batch_insert=False,
            supports_metadata_indexing=True,
        )

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.ELASTICSEARCH.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.INDEX.value: self.index_name,
            VectorStatsKey.DISTANCE.value: self.distance,
            VectorStatsKey.STATS.value: self.get_stats(),
        }

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = (memory_id, ttl_seconds)
        raise NotSupportedError("TTL is not supported by this vector adapter")

    def _create_client(self) -> Any:
        try:
            from elasticsearch import Elasticsearch  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "elasticsearch package is required for providers.vector_db=elasticsearch"
            ) from exc
        kwargs: dict[str, Any] = {"hosts": [self.url]}
        if self.username or self.password:
            kwargs["basic_auth"] = (self.username, self.password)
        return Elasticsearch(**kwargs)

    def _ensure_index(self) -> None:
        if self._index_exists():
            return
        similarity = self._SIMILARITY_MAP.get(self.distance)
        if similarity is None:
            raise ValueError(
                "Elasticsearch distance must be one of: cosine, l2, inner_product"
            )
        self._client.indices.create(
            index=self.index_name,
            mappings={
                "properties": {
                    VectorPayloadKey.VECTOR.value: {
                        "type": "dense_vector",
                        "dims": self.expected_dimensionality,
                        "index": True,
                        "similarity": similarity,
                    },
                    VectorPayloadKey.MEMORY_ID.value: {"type": "keyword"},
                    VectorPayloadKey.PROJECT_ID.value: {"type": "keyword"},
                    VectorPayloadKey.OWNER_ID.value: {"type": "keyword"},
                    VectorPayloadKey.CHUNK_ID.value: {"type": "keyword"},
                    VectorPayloadKey.MESSAGE_HASH.value: {"type": "keyword"},
                    VectorPayloadKey.ROLE.value: {"type": "keyword"},
                    VectorPayloadKey.TEXT.value: {"type": "text"},
                }
            },
        )

    def _index_exists(self) -> bool:
        try:
            return bool(self._client.indices.exists(index=self.index_name))
        except Exception as exc:
            if _exception_status(exc) == 400:
                return False
            raise


class OpenSearchVectorStore:
    _SPACE_TYPE_MAP = {
        "cosine": OpenSearchSpaceType.COSINE.value,
        "l2": OpenSearchSpaceType.L2.value,
        "inner_product": OpenSearchSpaceType.INNER_PRODUCT.value,
    }

    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:9200",
        username: str = "",
        password: str = "",
        index_name: str = "memory_vectors",
        dimension: int = 32,
        distance: str = "cosine",
        client: Any | None = None,
    ):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.index_name = index_name
        self.dimension = dimension
        self.distance = distance
        self._client = client or self._create_client()
        self._ensure_index()

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def insert(
        self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False
    ) -> None:
        if replace:
            self.delete([metadata_id])
        for item in embeddings:
            vector = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            _validate_vector_length(
                vector, expected=self.expected_dimensionality, operation="insert"
            )
            payload = _vector_payload(metadata_id, item)
            document = {**payload, VectorPayloadKey.VECTOR.value: vector}
            self._client.index(
                index=self.index_name,
                id=_stable_point_id(
                    metadata_id, payload[VectorPayloadKey.CHUNK_ID.value]
                ),
                body=document,
                refresh=True,
            )

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        _validate_vector_length(
            query_vector, expected=self.expected_dimensionality, operation="search"
        )
        result = self._client.search(
            index=self.index_name,
            body={
                SearchQueryKey.SIZE.value: top_k,
                SearchQueryKey.QUERY.value: {
                    SearchQueryKey.KNN.value: {
                        VectorPayloadKey.VECTOR.value: {
                            VectorPayloadKey.VECTOR.value: query_vector,
                            SearchQueryKey.K.value: top_k,
                        }
                    }
                },
            },
        )
        return _rows_from_search_hits(result)

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        self._client.delete_by_query(
            index=self.index_name,
            body={
                SearchQueryKey.QUERY.value: {
                    SearchQueryKey.TERMS.value: {
                        VectorPayloadKey.MEMORY_ID.value: [str(item) for item in ids]
                    }
                }
            },
            refresh=True,
            conflicts="proceed",
        )

    def get_stats(self) -> dict[str, Any]:
        rows = None
        try:
            result = self._client.count(index=self.index_name)
            rows = int(result.get("count", 0))
        except Exception:
            rows = None
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.OPENSEARCH.value,
            VectorStatsKey.ROWS.value: rows,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.INDEX.value: self.index_name,
            VectorStatsKey.DISTANCE.value: self.distance,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_batch_insert=False,
            supports_metadata_indexing=True,
        )

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.OPENSEARCH.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.INDEX.value: self.index_name,
            VectorStatsKey.DISTANCE.value: self.distance,
            VectorStatsKey.STATS.value: self.get_stats(),
        }

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = (memory_id, ttl_seconds)
        raise NotSupportedError("TTL is not supported by this vector adapter")

    def _create_client(self) -> Any:
        try:
            from opensearchpy import OpenSearch  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "opensearch-py package is required for providers.vector_db=opensearch"
            ) from exc
        parsed = urlparse(self.url)
        kwargs: dict[str, Any] = {
            "hosts": [{"host": parsed.hostname, "port": parsed.port or 9200}],
            "use_ssl": parsed.scheme == "https",
        }
        if self.username or self.password:
            kwargs["http_auth"] = (self.username, self.password)
        return OpenSearch(**kwargs)

    def _ensure_index(self) -> None:
        if self._client.indices.exists(index=self.index_name):
            return
        space_type = self._SPACE_TYPE_MAP.get(self.distance)
        if space_type is None:
            raise ValueError(
                "OpenSearch distance must be one of: cosine, l2, inner_product"
            )
        self._client.indices.create(
            index=self.index_name,
            body={
                "settings": {"index": {"knn": True}},
                "mappings": {
                    "properties": {
                        VectorPayloadKey.VECTOR.value: {
                            "type": "knn_vector",
                            "dimension": self.expected_dimensionality,
                            "method": {
                                "name": "hnsw",
                                "space_type": space_type,
                                "engine": "lucene",
                            },
                        },
                        VectorPayloadKey.MEMORY_ID.value: {"type": "keyword"},
                        VectorPayloadKey.PROJECT_ID.value: {"type": "keyword"},
                        VectorPayloadKey.OWNER_ID.value: {"type": "keyword"},
                        VectorPayloadKey.CHUNK_ID.value: {"type": "keyword"},
                        VectorPayloadKey.MESSAGE_HASH.value: {"type": "keyword"},
                        VectorPayloadKey.ROLE.value: {"type": "keyword"},
                        VectorPayloadKey.TEXT.value: {"type": "text"},
                    }
                },
            },
        )


class RedisVectorStore:
    _DISTANCE_MAP = {
        "cosine": "COSINE",
        "l2": "L2",
        "inner_product": "IP",
    }

    def __init__(
        self,
        *,
        url: str = "redis://127.0.0.1:6379/0",
        index_name: str = "memory_vectors",
        key_prefix: str = "memory_vectors:",
        dimension: int = 32,
        distance: str = "cosine",
        client: Any | None = None,
    ) -> None:
        self.index_name = index_name
        self.key_prefix = key_prefix
        self.expected_dimensionality = dimension
        self.distance = distance
        self._algorithm = self._DISTANCE_MAP.get(distance, "COSINE")
        self._client = client or self._create_client(url)
        self._ensure_index()

    def _create_client(self, url: str) -> Any:
        try:
            redis_module = import_module("redis")
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Redis vector support requires installing the 'redis' extra"
            ) from exc
        return redis_module.Redis.from_url(url)

    def _ensure_index(self) -> None:
        try:
            self._client.ft(self.index_name).info()
            return
        except Exception as exc:
            if not _is_redis_missing_index_error(exc):
                raise
        self._client.execute_command(
            "FT.CREATE",
            self.index_name,
            "ON",
            "HASH",
            "PREFIX",
            "1",
            self.key_prefix,
            "SCHEMA",
            VectorPayloadKey.MEMORY_ID.value,
            "TAG",
            VectorPayloadKey.PROJECT_ID.value,
            "TAG",
            VectorPayloadKey.OWNER_ID.value,
            "TAG",
            VectorPayloadKey.CHUNK_ID.value,
            "TEXT",
            VectorPayloadKey.CHUNK_INDEX.value,
            "NUMERIC",
            VectorPayloadKey.MESSAGE_HASH.value,
            "TAG",
            VectorPayloadKey.ROLE.value,
            "TAG",
            VectorPayloadKey.TEXT.value,
            "TEXT",
            RedisVectorField.VECTOR.value,
            "VECTOR",
            "HNSW",
            "6",
            "TYPE",
            "FLOAT32",
            "DIM",
            str(self.expected_dimensionality),
            "DISTANCE_METRIC",
            self._algorithm,
        )

    def insert(
        self,
        metadata_id: str,
        chunks: list[dict[str, Any]],
        *,
        replace: bool = False,
    ) -> None:
        if replace:
            self.delete([metadata_id])
        for item in chunks:
            embedding = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            _validate_vector_length(
                embedding, expected=self.expected_dimensionality, operation="insert"
            )
            payload = _vector_payload(metadata_id, item)
            self._client.hset(
                self._key_for_payload(payload),
                mapping={
                    **payload,
                    RedisVectorField.VECTOR.value: _float32_vector_bytes(embedding),
                },
            )

    def replace(self, metadata_id: str, chunks: list[dict[str, Any]]) -> None:
        self.delete([metadata_id])
        self.insert(metadata_id, chunks)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        _validate_vector_length(
            query_vector, expected=self.expected_dimensionality, operation="search"
        )
        query = (
            f"*=>[KNN {int(top_k)} @{RedisVectorField.VECTOR.value} "
            f"$vector AS {RedisVectorField.DISTANCE.value}]"
        )
        result = self._client.ft(self.index_name).search(
            _redis_search_query(query),
            query_params={
                RedisVectorField.VECTOR.value: _float32_vector_bytes(query_vector)
            },
        )
        rows = [_row_from_redis_document(document) for document in result.docs]
        rows.sort(key=lambda row: float(row.get(RedisVectorField.DISTANCE.value, 0.0)))
        return rows[:top_k]

    def delete(self, ids: list[str]) -> None:
        for memory_id in ids:
            self._delete_memory_id(str(memory_id))

    def get_stats(self) -> dict[str, Any]:
        rows = None
        try:
            rows = _redis_info_int(self._client.ft(self.index_name).info(), "num_docs")
        except Exception:
            rows = None
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.REDIS.value,
            VectorStatsKey.ROWS.value: rows,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.INDEX.value: self.index_name,
            VectorStatsKey.DISTANCE.value: self.distance,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_metadata_indexing=True)

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.REDIS.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.INDEX.value: self.index_name,
            VectorStatsKey.DISTANCE.value: self.distance,
            VectorStatsKey.STATS.value: self.get_stats(),
        }

    def _delete_memory_id(self, memory_id: str) -> None:
        query = f"@{VectorPayloadKey.MEMORY_ID.value}:{{{_redis_tag_escape(memory_id)}}}"
        result = self._client.ft(self.index_name).search(query)
        for document in result.docs:
            self._client.delete(document.id)

    def _key_for_payload(self, payload: dict[str, Any]) -> str:
        return self.key_prefix + _stable_point_id(
            str(payload[VectorPayloadKey.MEMORY_ID.value]),
            str(payload[VectorPayloadKey.CHUNK_ID.value]),
        )


class PineconeVectorStore:
    _METRIC_MAP = {
        "cosine": "cosine",
        "l2": "euclidean",
        "inner_product": "dotproduct",
    }

    def __init__(
        self,
        *,
        api_key: str = "",
        index_name: str = "memory-vectors",
        namespace: str = "default",
        dimension: int = 32,
        distance: str = "cosine",
        cloud: str = "aws",
        region: str = "us-east-1",
        create_index: bool = False,
        client: Any | None = None,
        index: Any | None = None,
        serverless_spec: Any | None = None,
    ) -> None:
        self.index_name = index_name
        self.namespace = namespace
        self.expected_dimensionality = dimension
        self.distance = distance
        self.metric = self._METRIC_MAP.get(distance, "cosine")
        self._client = client or self._create_client(api_key)
        self._serverless_spec = serverless_spec
        if create_index:
            self._ensure_index(cloud=cloud, region=region)
        self._index = index or self._client.Index(index_name)

    def _create_client(self, api_key: str) -> Any:
        try:
            pinecone_module = import_module("pinecone")
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Pinecone vector support requires installing the 'pinecone' extra"
            ) from exc
        return pinecone_module.Pinecone(api_key=api_key)

    def _ensure_index(self, *, cloud: str, region: str) -> None:
        if self.index_name in _pinecone_index_names(self._client.list_indexes()):
            return
        spec = self._serverless_spec or _pinecone_serverless_spec(
            cloud=cloud, region=region
        )
        self._client.create_index(
            name=self.index_name,
            dimension=self.expected_dimensionality,
            metric=self.metric,
            spec=spec,
        )

    def insert(
        self,
        metadata_id: str,
        chunks: list[dict[str, Any]],
        *,
        replace: bool = False,
    ) -> None:
        if replace:
            self.delete([metadata_id])
        vectors = []
        for item in chunks:
            embedding = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            _validate_vector_length(
                embedding, expected=self.expected_dimensionality, operation="insert"
            )
            payload = _vector_payload(metadata_id, item)
            vectors.append(
                {
                    "id": _stable_point_id(
                        str(payload[VectorPayloadKey.MEMORY_ID.value]),
                        str(payload[VectorPayloadKey.CHUNK_ID.value]),
                    ),
                    "values": embedding,
                    "metadata": payload,
                }
            )
        if vectors:
            self._index.upsert(vectors=vectors, namespace=self.namespace)

    def replace(self, metadata_id: str, chunks: list[dict[str, Any]]) -> None:
        self.delete([metadata_id])
        self.insert(metadata_id, chunks)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        _validate_vector_length(
            query_vector, expected=self.expected_dimensionality, operation="search"
        )
        result = self._index.query(
            vector=query_vector,
            top_k=top_k,
            namespace=self.namespace,
            include_metadata=True,
        )
        return [_row_from_pinecone_match(match) for match in _pinecone_matches(result)]

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        self._index.delete(
            filter={VectorPayloadKey.MEMORY_ID.value: {"$in": [str(item) for item in ids]}},
            namespace=self.namespace,
        )

    def get_stats(self) -> dict[str, Any]:
        rows = None
        try:
            rows = _pinecone_namespace_count(
                self._index.describe_index_stats(), namespace=self.namespace
            )
        except Exception:
            rows = None
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.PINECONE.value,
            VectorStatsKey.ROWS.value: rows,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.INDEX.value: self.index_name,
            "namespace": self.namespace,
            VectorStatsKey.DISTANCE.value: self.distance,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_metadata_indexing=True)

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.PINECONE.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.INDEX.value: self.index_name,
            "namespace": self.namespace,
            VectorStatsKey.DISTANCE.value: self.distance,
            VectorStatsKey.STATS.value: self.get_stats(),
        }


class TurbopufferVectorStore:
    _METRIC_MAP = {
        "cosine": "cosine_distance",
        "l2": "euclidean_squared",
        "inner_product": "cosine_distance",
    }

    def __init__(
        self,
        *,
        api_key: str = "",
        namespace: str = "memory-vectors",
        region: str = "gcp-us-central1",
        dimension: int = 32,
        distance: str = "cosine",
        client: Any | None = None,
        namespace_client: Any | None = None,
    ) -> None:
        self.namespace = namespace
        self.region = region
        self.expected_dimensionality = dimension
        self.distance = distance
        self.metric = self._METRIC_MAP.get(distance, "cosine_distance")
        self._client = client or self._create_client(api_key=api_key, region=region)
        self._namespace = namespace_client or self._client.namespace(namespace)

    def _create_client(self, *, api_key: str, region: str) -> Any:
        try:
            turbopuffer_module = import_module("turbopuffer")
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Turbopuffer vector support requires installing the 'turbopuffer' extra"
            ) from exc
        return turbopuffer_module.Turbopuffer(api_key=api_key, region=region)

    def insert(
        self,
        metadata_id: str,
        chunks: list[dict[str, Any]],
        *,
        replace: bool = False,
    ) -> None:
        if replace:
            self.delete([metadata_id])
        rows = []
        for item in chunks:
            embedding = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            _validate_vector_length(
                embedding, expected=self.expected_dimensionality, operation="insert"
            )
            payload = _vector_payload(metadata_id, item)
            rows.append(
                {
                    "id": _stable_point_id(
                        str(payload[VectorPayloadKey.MEMORY_ID.value]),
                        str(payload[VectorPayloadKey.CHUNK_ID.value]),
                    ),
                    VectorPayloadKey.VECTOR.value: embedding,
                    **payload,
                }
            )
        if rows:
            self._namespace.write(
                upsert_rows=rows,
                distance_metric=self.metric,
                schema={
                    VectorPayloadKey.VECTOR.value: {
                        "type": f"[{self.expected_dimensionality}]f32",
                        "ann": True,
                    }
                },
            )

    def replace(self, metadata_id: str, chunks: list[dict[str, Any]]) -> None:
        self.delete([metadata_id])
        self.insert(metadata_id, chunks)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        _validate_vector_length(
            query_vector, expected=self.expected_dimensionality, operation="search"
        )
        result = self._namespace.query(
            rank_by=(VectorPayloadKey.VECTOR.value, "ANN", query_vector),
            limit=top_k,
            include_attributes=_vector_payload_fields(),
        )
        return [_row_from_turbopuffer_match(match) for match in _turbopuffer_rows(result)]

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        self._namespace.write(
            delete_by_filter=(
                VectorPayloadKey.MEMORY_ID.value,
                "In",
                [str(item) for item in ids],
            ),
            delete_by_filter_allow_partial=True,
        )

    def get_stats(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.TURBOPUFFER.value,
            VectorStatsKey.ROWS.value: _turbopuffer_approx_row_count(self._namespace),
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            "namespace": self.namespace,
            "region": self.region,
            VectorStatsKey.DISTANCE.value: self.distance,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_metadata_indexing=True)

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.TURBOPUFFER.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            "namespace": self.namespace,
            "region": self.region,
            VectorStatsKey.DISTANCE.value: self.distance,
            VectorStatsKey.STATS.value: self.get_stats(),
        }


class TypesenseVectorStore:
    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:8108",
        api_key: str = "",
        collection_name: str = "memory_vectors",
        dimension: int = 32,
        distance: str = "cosine",
        client: Any | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.collection_name = collection_name
        self.expected_dimensionality = dimension
        self.distance = distance
        self._client = client or self._create_client()
        self._ensure_collection()

    def _create_client(self) -> Any:
        try:
            typesense_module = import_module("typesense")
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "Typesense vector support requires installing the 'typesense' extra"
            ) from exc
        parsed = urlparse(self.url)
        return typesense_module.Client(
            {
                "nodes": [
                    {
                        "host": parsed.hostname or "127.0.0.1",
                        "port": str(parsed.port or (443 if parsed.scheme == "https" else 80)),
                        "protocol": parsed.scheme or "http",
                    }
                ],
                "api_key": self.api_key,
                "connection_timeout_seconds": 5,
            }
        )

    def insert(
        self,
        metadata_id: str,
        chunks: list[dict[str, Any]],
        *,
        replace: bool = False,
    ) -> None:
        if replace:
            self.delete([metadata_id])
        documents = []
        for item in chunks:
            vector = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            _validate_vector_length(
                vector, expected=self.expected_dimensionality, operation="insert"
            )
            payload = _vector_payload(metadata_id, item)
            documents.append(
                {
                    "id": _stable_point_id(
                        str(payload[VectorPayloadKey.MEMORY_ID.value]),
                        str(payload[VectorPayloadKey.CHUNK_ID.value]),
                    ),
                    **payload,
                    VectorPayloadKey.VECTOR.value: vector,
                }
            )
        if not documents:
            return
        collection = self._collection()
        import_method = getattr(collection.documents, "import_", None)
        if callable(import_method):
            import_method(documents, {"action": "upsert"})
            return
        for document in documents:
            collection.documents.upsert(document)

    def replace(self, metadata_id: str, chunks: list[dict[str, Any]]) -> None:
        self.delete([metadata_id])
        self.insert(metadata_id, chunks)

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        _validate_vector_length(
            query_vector, expected=self.expected_dimensionality, operation="search"
        )
        vector = ",".join(str(float(value)) for value in query_vector)
        result = self._collection().documents.search(
            {
                "q": "*",
                "query_by": VectorPayloadKey.TEXT.value,
                "vector_query": (
                    f"{VectorPayloadKey.VECTOR.value}:([{vector}], k:{int(top_k)})"
                ),
                "per_page": int(top_k),
                "exclude_fields": VectorPayloadKey.VECTOR.value,
            }
        )
        return [_row_from_typesense_hit(hit) for hit in result.get("hits", [])]

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        filter_values = ",".join(_typesense_filter_escape(str(item)) for item in ids)
        self._collection().documents.delete(
            {"filter_by": f"{VectorPayloadKey.MEMORY_ID.value}:=[{filter_values}]"}
        )

    def get_stats(self) -> dict[str, Any]:
        rows = None
        try:
            rows = int(self._collection().retrieve().get("num_documents", 0))
        except Exception:
            rows = None
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.TYPESENSE.value,
            VectorStatsKey.ROWS.value: rows,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            VectorStatsKey.DISTANCE.value: self.distance,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(supports_metadata_indexing=True)

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.TYPESENSE.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            VectorStatsKey.DISTANCE.value: self.distance,
            VectorStatsKey.STATS.value: self.get_stats(),
        }

    def _ensure_collection(self) -> None:
        if self._collection_exists():
            return
        self._client.collections.create(
            {
                "name": self.collection_name,
                "fields": [
                    {"name": VectorPayloadKey.MEMORY_ID.value, "type": "string", "facet": True},
                    {"name": VectorPayloadKey.PROJECT_ID.value, "type": "string", "facet": True},
                    {"name": VectorPayloadKey.OWNER_ID.value, "type": "string", "facet": True},
                    {"name": VectorPayloadKey.CHUNK_ID.value, "type": "string"},
                    {"name": VectorPayloadKey.CHUNK_INDEX.value, "type": "int32"},
                    {"name": VectorPayloadKey.MESSAGE_HASH.value, "type": "string"},
                    {"name": VectorPayloadKey.ROLE.value, "type": "string", "facet": True},
                    {"name": VectorPayloadKey.TEXT.value, "type": "string"},
                    {
                        "name": VectorPayloadKey.VECTOR.value,
                        "type": "float[]",
                        "num_dim": self.expected_dimensionality,
                    },
                ],
            }
        )

    def _collection_exists(self) -> bool:
        try:
            self._collection().retrieve()
        except Exception as exc:
            if _is_typesense_not_found(exc):
                return False
            raise
        return True

    def _collection(self) -> Any:
        return self._client.collections[self.collection_name]


class WeaviateVectorStore:
    def __init__(
        self,
        *,
        url: str = "http://127.0.0.1:8080",
        api_key: str = "",
        collection_name: str = "MemoryVector",
        dimension: int = 32,
        client: Any | None = None,
        classes: Any | None = None,
    ):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.collection_name = collection_name
        self.dimension = dimension
        if client is None:
            self._client, self._classes = self._create_client()
        else:
            self._client = client
            self._classes = classes
        self._collection = self._ensure_collection()

    @property
    def expected_dimensionality(self) -> int:
        return self.dimension

    def insert(
        self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False
    ) -> None:
        if replace:
            self.delete([metadata_id])
        for item in embeddings:
            vector = [float(value) for value in item[VectorPayloadKey.VECTOR.value]]
            _validate_vector_length(
                vector, expected=self.expected_dimensionality, operation="insert"
            )
            payload = _vector_payload(metadata_id, item)
            self._collection.data.insert(
                uuid=_stable_point_id(
                    metadata_id, payload[VectorPayloadKey.CHUNK_ID.value]
                ),
                properties=payload,
                vector=vector,
            )

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        _validate_vector_length(
            query_vector, expected=self.expected_dimensionality, operation="search"
        )
        kwargs: dict[str, Any] = {"near_vector": query_vector, "limit": top_k}
        metadata_query = self._metadata_query()
        if metadata_query is not None:
            kwargs["return_metadata"] = metadata_query
        result = self._collection.query.near_vector(**kwargs)
        rows: list[dict[str, Any]] = []
        for obj in getattr(result, "objects", result) or []:
            properties = dict(getattr(obj, "properties", {}) or {})
            row = _row_from_vector_payload(properties)
            metadata = getattr(obj, "metadata", None)
            score = getattr(metadata, "certainty", None)
            if score is None:
                distance = getattr(metadata, "distance", 0.0)
                score = 1.0 - float(distance)
            row[VectorPayloadKey.SCORE.value] = float(score)
            rows.append(row)
        return rows

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        where = self._memory_id_filter([str(item) for item in ids])
        self._collection.data.delete_many(where=where)

    def get_stats(self) -> dict[str, Any]:
        rows = None
        try:
            result = self._collection.aggregate.over_all(total_count=True)
            rows = int(getattr(result, "total_count", 0) or 0)
        except Exception:
            rows = None
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.WEAVIATE.value,
            VectorStatsKey.ROWS.value: rows,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
        }

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_batch_insert=False,
            supports_metadata_indexing=True,
        )

    def health(self) -> dict[str, Any]:
        return {
            VectorStatsKey.PROVIDER.value: VectorProviderName.WEAVIATE.value,
            VectorStatsKey.EXPECTED_DIMENSIONALITY.value: self.expected_dimensionality,
            VectorStatsKey.COLLECTION.value: self.collection_name,
            VectorStatsKey.STATS.value: self.get_stats(),
        }

    def set_ttl(self, memory_id: str, ttl_seconds: int) -> None:
        _ = (memory_id, ttl_seconds)
        raise NotSupportedError("TTL is not supported by this vector adapter")

    def _create_client(self) -> tuple[Any, Any]:
        try:
            import weaviate  # type: ignore
            from weaviate.classes import config as wvc_config  # type: ignore
            from weaviate.classes import query as wvc_query  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "weaviate-client package is required for providers.vector_db=weaviate"
            ) from exc
        parsed = urlparse(self.url)
        auth = None
        if self.api_key:
            auth = weaviate.auth.AuthApiKey(self.api_key)
        client = weaviate.connect_to_custom(
            http_host=parsed.hostname or "127.0.0.1",
            http_port=parsed.port or (443 if parsed.scheme == "https" else 8080),
            http_secure=parsed.scheme == "https",
            grpc_host=parsed.hostname or "127.0.0.1",
            grpc_port=50051,
            grpc_secure=parsed.scheme == "https",
            auth_credentials=auth,
        )
        return client, {"config": wvc_config, "query": wvc_query}

    def _ensure_collection(self) -> Any:
        if self._client.collections.exists(self.collection_name):
            return self._client.collections.get(self.collection_name)
        config_classes = self._classes.get("config") if isinstance(self._classes, dict) else None
        if config_classes is None:
            return self._client.collections.create(self.collection_name)
        return self._client.collections.create(
            name=self.collection_name,
            vectorizer_config=config_classes.Configure.Vectorizer.none(),
            properties=[
                config_classes.Property(
                    name=VectorPayloadKey.MEMORY_ID.value,
                    data_type=config_classes.DataType.TEXT,
                ),
                config_classes.Property(
                    name=VectorPayloadKey.PROJECT_ID.value,
                    data_type=config_classes.DataType.TEXT,
                ),
                config_classes.Property(
                    name=VectorPayloadKey.OWNER_ID.value,
                    data_type=config_classes.DataType.TEXT,
                ),
                config_classes.Property(
                    name=VectorPayloadKey.CHUNK_ID.value,
                    data_type=config_classes.DataType.TEXT,
                ),
                config_classes.Property(
                    name=VectorPayloadKey.CHUNK_INDEX.value,
                    data_type=config_classes.DataType.INT,
                ),
                config_classes.Property(
                    name=VectorPayloadKey.MESSAGE_HASH.value,
                    data_type=config_classes.DataType.TEXT,
                ),
                config_classes.Property(
                    name=VectorPayloadKey.ROLE.value,
                    data_type=config_classes.DataType.TEXT,
                ),
                config_classes.Property(
                    name=VectorPayloadKey.TEXT.value,
                    data_type=config_classes.DataType.TEXT,
                ),
            ],
        )

    def _metadata_query(self) -> Any | None:
        query_classes = self._classes.get("query") if isinstance(self._classes, dict) else None
        if query_classes is None:
            return None
        return query_classes.MetadataQuery(distance=True, certainty=True)

    def _memory_id_filter(self, ids: list[str]) -> Any:
        query_classes = self._classes.get("query") if isinstance(self._classes, dict) else None
        if query_classes is None:
            return {VectorPayloadKey.MEMORY_ID.value: ids}
        return query_classes.Filter.by_property(
            VectorPayloadKey.MEMORY_ID.value
        ).contains_any(ids)


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


def _exception_status(exc: Exception) -> int | None:
    meta = getattr(exc, "meta", None)
    status = getattr(meta, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status is None:
        return None
    return int(status)


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


def _float32_vector_bytes(vector: list[float]) -> bytes:
    return pa.array(vector, type=pa.float32()).to_numpy(zero_copy_only=False).tobytes()


def _row_from_redis_document(document: Any) -> dict[str, Any]:
    payload = {
        field: _decode_redis_value(getattr(document, field, ""))
        for field in _vector_payload_fields()
    }
    row = _row_from_vector_payload(payload)
    distance = float(
        _decode_redis_value(getattr(document, RedisVectorField.DISTANCE.value, 0.0))
    )
    row[RedisVectorField.DISTANCE.value] = distance
    row[VectorPayloadKey.SCORE.value] = 1.0 - distance
    return row


def _decode_redis_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _is_redis_missing_index_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "unknown index" in message or "no such index" in message


def _redis_info_int(info: Any, key: str) -> int | None:
    value = info.get(key) if isinstance(info, dict) else getattr(info, key, None)
    if value is None:
        return None
    return int(value)


def _redis_tag_escape(value: str) -> str:
    return re.sub(r"([,{}\\])", r"\\\1", value)


def _redis_search_query(query: str) -> Any:
    try:
        query_module = import_module("redis.commands.search.query")
    except ImportError:
        return query
    return (
        query_module.Query(query)
        .return_fields(*_vector_payload_fields(), RedisVectorField.DISTANCE.value)
        .sort_by(RedisVectorField.DISTANCE.value)
        .dialect(2)
    )


def _pinecone_serverless_spec(*, cloud: str, region: str) -> Any:
    pinecone_module = import_module("pinecone")
    return pinecone_module.ServerlessSpec(cloud=cloud, region=region)


def _pinecone_index_names(indexes: Any) -> set[str]:
    names_method = getattr(indexes, "names", None)
    if callable(names_method):
        return {str(name) for name in cast(Any, names_method)()}
    names: set[str] = set()
    for item in indexes or []:
        name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
        if name:
            names.add(str(name))
    return names


def _pinecone_matches(result: Any) -> list[Any]:
    if isinstance(result, dict):
        return list(result.get("matches", []) or [])
    return list(getattr(result, "matches", []) or [])


def _row_from_pinecone_match(match: Any) -> dict[str, Any]:
    metadata = _mapping_from_object_or_dict(match, "metadata") or {}
    row = _row_from_vector_payload(dict(metadata))
    row[VectorPayloadKey.SCORE.value] = float(
        _mapping_from_object_or_dict(match, "score") or 0.0
    )
    return row


def _pinecone_namespace_count(stats: Any, *, namespace: str) -> int | None:
    namespaces = _mapping_from_object_or_dict(stats, "namespaces") or {}
    namespace_stats = namespaces.get(namespace) if isinstance(namespaces, dict) else None
    if namespace_stats is not None:
        count = _mapping_from_object_or_dict(namespace_stats, "vector_count")
        if count is not None:
            return int(count)
    total = _mapping_from_object_or_dict(stats, "total_vector_count")
    if total is None:
        return None
    return int(total)


def _turbopuffer_rows(result: Any) -> list[Any]:
    rows = _mapping_from_object_or_dict(result, "rows")
    if rows is None:
        rows = _mapping_from_object_or_dict(result, "results")
    if rows is None and isinstance(result, list):
        rows = result
    return list(rows or [])


def _row_from_turbopuffer_match(match: Any) -> dict[str, Any]:
    payload = {
        field: _mapping_from_object_or_dict(match, field)
        for field in _vector_payload_fields()
    }
    row = _row_from_vector_payload(payload)
    score = _mapping_from_object_or_dict(match, VectorPayloadKey.SCORE.value)
    if score is None:
        distance = _mapping_from_object_or_dict(match, "$dist")
        score = 1.0 - float(distance or 0.0)
    row[VectorPayloadKey.SCORE.value] = float(score or 0.0)
    return row


def _turbopuffer_approx_row_count(namespace: Any) -> int | None:
    metadata_method = getattr(namespace, "metadata", None)
    if not callable(metadata_method):
        return None
    try:
        metadata = metadata_method()
    except Exception:
        return None
    count = _mapping_from_object_or_dict(metadata, "approx_row_count")
    if count is None:
        count = _mapping_from_object_or_dict(metadata, "row_count")
    if count is None:
        return None
    return int(count)


def _row_from_typesense_hit(hit: dict[str, Any]) -> dict[str, Any]:
    document = dict(hit.get("document", {}) or {})
    row = _row_from_vector_payload(document)
    distance = hit.get("vector_distance", hit.get("_vector_distance"))
    if distance is not None:
        row["vector_distance"] = float(distance)
        row[VectorPayloadKey.SCORE.value] = 1.0 - float(distance)
    else:
        row[VectorPayloadKey.SCORE.value] = float(hit.get("text_match", 0.0) or 0.0)
    return row


def _typesense_filter_escape(value: str) -> str:
    return "`" + value.replace("`", "\\`") + "`"


def _is_typesense_not_found(exc: Exception) -> bool:
    if exc.__class__.__name__ == "ObjectNotFound":
        return True
    status = getattr(exc, "status_code", None)
    if status is not None and int(status) == 404:
        return True
    return "not found" in str(exc).lower()


def _vector_payload_fields() -> list[str]:
    return [
        VectorPayloadKey.MEMORY_ID.value,
        VectorPayloadKey.PROJECT_ID.value,
        VectorPayloadKey.OWNER_ID.value,
        VectorPayloadKey.CHUNK_ID.value,
        VectorPayloadKey.CHUNK_INDEX.value,
        VectorPayloadKey.MESSAGE_HASH.value,
        VectorPayloadKey.ROLE.value,
        VectorPayloadKey.TEXT.value,
    ]


def _validate_vector_length(
    vector: list[float], *, expected: int, operation: str
) -> None:
    actual = len(vector)
    if actual != expected:
        raise VectorDimensionError(
            f"Vector dimensionality mismatch during {operation}: expected {expected}, got {actual}"
        )


def _rows_from_search_hits(result: dict[str, Any]) -> list[dict[str, Any]]:
    hits_wrapper = result.get(SearchQueryKey.HITS.value, {})
    hits = hits_wrapper.get(SearchQueryKey.HITS.value, [])
    rows: list[dict[str, Any]] = []
    for hit in hits:
        source = dict(hit.get(SearchQueryKey.SOURCE.value, {}) or {})
        row = _row_from_vector_payload(source)
        row[VectorPayloadKey.SCORE.value] = float(
            hit.get(SearchQueryKey.SCORE.value, 0.0)
        )
        rows.append(row)
    return rows


def _mapping_from_object_or_dict(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _first_query_batch(value: Any) -> list[Any]:
    if isinstance(value, list) and value and isinstance(value[0], list):
        return value[0]
    if isinstance(value, list):
        return value
    return []
