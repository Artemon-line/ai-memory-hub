from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence
from uuid import uuid4

from memory.advanced_memory import extract_memory_graph
from memory.backend.dry_run import DryRunMetadataStore, DryRunVectorStore
from memory.backend.errors import SchemaVersionError, VectorDimensionError
from memory.backend.log_safety import redact_secrets
from memory.backend.metadata_store import (
    LOCAL_DEFAULT_PROJECT_ID,
    PROJECT_ROLE_READER,
    PROJECT_ROLE_WRITER,
    SQLiteMetadataStore,
    _default_project_id,
    _validate_owner_id,
    _validate_project_id,
)
from memory.backend.mongodb_metadata_store import MongoDBMetadataStore
from memory.backend.postgres_metadata_store import PostgresMetadataStore
from memory.backend.vector_store import (
    ChromaDBVectorStore,
    ElasticsearchVectorStore,
    InMemoryVectorStore,
    LanceDBVectorStore,
    MilvusVectorStore,
    MongoDBAtlasVectorStore,
    OpenSearchVectorStore,
    PGVectorStore,
    PineconeVectorStore,
    QdrantVectorStore,
    RedisVectorStore,
    TurbopufferVectorStore,
    TypesenseVectorStore,
    VespaVectorStore,
    WeaviateVectorStore,
)
from memory.config import HubConfig, ensure_token_hash_secret, load_config, parse_config
from memory.ingestion.summary_models import (
    GeneratedSummary,
    GeneratedSummaryProvenance,
    SummaryBasis,
    SummaryProvenanceStatus,
    SummaryType,
)
from memory.ingestion.thread_models import (
    SearchResultMode,
    ThreadMetadataKey,
    ThreadResultKey,
    result_mode_error_message,
    result_mode_values,
    thread_metadata_from_mapping,
)
from memory.ingestion.tokenizer import (
    count_tokens,
    split_token_windows,
    tokenizer_used,
    truncate_to_tokens,
)
from memory.ingestion.validate import (
    load_schema,
    set_schema_path,
    validate_conversation,
    validate_schema_compatibility,
)
from memory.observability.metrics import metrics
from memory.observability.tracing import start_observability_span
from memory.provider_models import VectorProviderAlias, VectorProviderName

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    dimension: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class LocalEmbeddingProvider:
    """Deterministic local embedding provider for offline/local-first mode."""

    def __init__(self, dimensions: int = 32):
        self.dimension = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_to_vector(text, self.dimension) for text in texts]


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        embedding_model: str = "text-embedding-3-small",
        dimension: int = 1536,
        base_url: str = "https://api.openai.com",
        api_key: str | None = None,
    ):
        from openai import OpenAI

        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            logger.warning(
                "OPENAI_API_KEY is required when providers.embeddings=openai"
            )

        self.client = OpenAI(base_url=base_url, api_key=key)
        self.embedding_model = embedding_model
        self.dimension = dimension

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = self.client.embeddings.create(
            model=self.embedding_model, input=texts, dimensions=self.dimension
        )
        return [list(item.embedding) for item in response.data]


@dataclass
class RuntimeDependencies:
    embedding_provider: EmbeddingProvider
    metadata_store: Any
    vector_store: Any
    health_state: dict[str, Any]
    allow_trusted_appends: bool = False
    tokenizer_enabled: bool = False
    tokenizer_encoding: str = "cl100k_base"
    ask_max_context_tokens: int = 2000
    chunking_strategy: str = "message"
    chunking_max_tokens: int = 800
    chunking_overlap_tokens: int = 80
    retrieval_vector_score_threshold: float = 7.5
    retrieval_keyword_enabled: bool = True
    retrieval_keyword_candidate_limit: int = 50
    retrieval_keyword_weight: float = 0.25
    retrieval_metadata_weight: float = 0.15
    retrieval_candidate_multiplier: int = 3
    fact_extractor: Any | None = None
    graph_enabled: bool = False


_VECTOR_COMPATIBILITY_METADATA_PREFIX = "vector_index_compatibility:"
_FALLBACK_POLICY_WARNED: set[str] = set()
_PRODUCTION_FALLBACK_POLICY_WARNED: set[str] = set()
_MEMORY_STATUS_ACTIVE = "active"
_MEMORY_STATUS_PENDING_REVIEW = "pending_review"
_MEMORY_STATUS_REJECTED = "rejected"
_MEMORY_STATUS_ALL = "all"
_MEMORY_STATUS_VALUES = {
    _MEMORY_STATUS_ACTIVE,
    _MEMORY_STATUS_PENDING_REVIEW,
    _MEMORY_STATUS_REJECTED,
    _MEMORY_STATUS_ALL,
}


@dataclass(frozen=True)
class ConversationFilters:
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    tags: tuple[str, ...] = ()
    thread_id: str | None = None

    @classmethod
    def from_options(
        cls,
        *,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        tags: Sequence[str] | None = None,
        thread_id: str | None = None,
    ) -> "ConversationFilters":
        return cls(
            source=str(source) if source else None,
            date_from=str(date_from) if date_from else None,
            date_to=str(date_to) if date_to else None,
            tags=tuple(str(tag) for tag in tags or () if str(tag)),
            thread_id=str(thread_id) if thread_id else None,
        )

    @property
    def has_filters(self) -> bool:
        return bool(
            self.source or self.date_from or self.date_to or self.tags or self.thread_id
        )


@dataclass(frozen=True)
class FactFilters:
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    confidence: str | None = None
    status: str = "active"
    source_quality: str | None = None
    save_intent: str | None = None
    save_intent_source: str | None = None
    freshness_from: str | None = None
    freshness_to: str | None = None

    @classmethod
    def from_options(
        cls,
        *,
        source: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        confidence: str | None = None,
        status: str | None = None,
        source_quality: str | None = None,
        save_intent: str | None = None,
        save_intent_source: str | None = None,
        freshness_from: str | None = None,
        freshness_to: str | None = None,
    ) -> "FactFilters":
        normalized_status = str(status or "active").lower()
        if normalized_status not in {"active", "superseded", "all"}:
            raise ValueError("status must be one of: active, superseded, all")
        return cls(
            source=str(source) if source else None,
            date_from=str(date_from) if date_from else None,
            date_to=str(date_to) if date_to else None,
            confidence=str(confidence) if confidence else None,
            status=normalized_status,
            source_quality=str(source_quality) if source_quality else None,
            save_intent=str(save_intent) if save_intent else None,
            save_intent_source=str(save_intent_source) if save_intent_source else None,
            freshness_from=str(freshness_from) if freshness_from else None,
            freshness_to=str(freshness_to) if freshness_to else None,
        )

    @property
    def include_superseded(self) -> bool:
        return self.status in {"superseded", "all"}


_RUNTIME: RuntimeDependencies | None = None
_SEARCH_CANDIDATE_MULTIPLIER = 3
_CONVERSATION_GROUP_SCORE_WINDOW = 0.25
_MAX_MESSAGES = 10_000
_MAX_MESSAGE_BYTES = 1_000_000
_MAX_PAYLOAD_BYTES = 25_000_000
_MAX_RAW_TRANSCRIPT_BYTES = 5_000_000
_MAX_METADATA_BYTES = 1_000_000
_MAX_METADATA_SUMMARY_CHARS = 2_000
_MAX_AUTO_TAGS = 24
_ROLE_ALIASES = {
    "human": "user",
    "user_message": "user",
    "ai": "assistant",
    "bot": "assistant",
    "assistant_message": "assistant",
}
_TRANSCRIPT_LINE_RE = re.compile(r"^(User|Assistant):\s?(.*)$", re.IGNORECASE)
_TOPIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("python", re.compile(r"\bpython\b", re.IGNORECASE)),
    (
        "javascript",
        re.compile(r"\b(javascript|js|node\.?js|typescript|ts)\b", re.IGNORECASE),
    ),
    ("sql", re.compile(r"\b(sql|sqlite|postgres|mysql|database|db)\b", re.IGNORECASE)),
    ("api", re.compile(r"\b(api|rest|endpoint|http|json-rpc)\b", re.IGNORECASE)),
    ("mcp", re.compile(r"\b(mcp|model context protocol)\b", re.IGNORECASE)),
    ("docker", re.compile(r"\b(docker|container|kubernetes|k8s)\b", re.IGNORECASE)),
    (
        "testing",
        re.compile(
            r"\b(test|tests|pytest|unit test|integration test)\b", re.IGNORECASE
        ),
    ),
    (
        "machine-learning",
        re.compile(r"\b(ai|llm|rag|embedding|vector)\b", re.IGNORECASE),
    ),
    (
        "frontend",
        re.compile(r"\b(frontend|ui|css|html|react|vue|angular)\b", re.IGNORECASE),
    ),
    ("backend", re.compile(r"\b(backend|fastapi|flask|server)\b", re.IGNORECASE)),
]
_RESULT_MODES = result_mode_values()


@contextmanager
def _ingestion_stage(stage: str, **attributes: Any) -> Iterator[None]:
    started = time.perf_counter()
    with start_observability_span(
        f"ingestion.{stage}",
        attributes={
            "ingestion.stage": stage,
            **attributes,
        },
    ) as span:
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            if span is not None:
                span.set_attribute("memory.duration_ms", elapsed_ms)
            metrics.observe(
                "memory_ingestion_stage_duration_ms",
                elapsed_ms,
                stage=stage,
            )
_FACT_CORRECTION_RE = re.compile(
    r"\bactually,\s+my\s+(?P<item>[A-Za-z0-9][A-Za-z0-9 _-]{1,80})\s+is\s+(?P<new>[^,.]+),\s+not\s+(?P<old>[^.]+)",
    re.IGNORECASE,
)
_FACT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("own", re.compile(r"\bI\s+(?:have|own)\s+(?P<object>[^.?!\n]+)", re.IGNORECASE)),
    (
        "favorite",
        re.compile(
            r"\bMy\s+favorite\s+(?P<name>[A-Za-z0-9 _-]+?)\s+is\s+(?P<object>[^.?!\n]+)",
            re.IGNORECASE,
        ),
    ),
    ("creator", re.compile(r"\bThe\s+creator\s+is\s+(?P<object>[^.?!\n]+)", re.IGNORECASE)),
    (
        "subject_creator",
        re.compile(
            r"\b(?P<subject>[A-Z][A-Za-z0-9 _-]{1,80})\s+creator\s+is\s+(?P<object>[^.?!\n]+)",
            re.IGNORECASE,
        ),
    ),
    (
        "command_name",
        re.compile(r"\bThe\s+command\s+name\s+is\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
    (
        "indexing_strategy",
        re.compile(r"\bThe\s+indexing\s+strategy\s+is\s+(?P<object>[^.?!\n]+)", re.IGNORECASE),
    ),
    (
        "project_attribute",
        re.compile(
            r"\b(?P<subject>[A-Z][A-Za-z0-9 _-]{1,80})\s+is\s+(?P<object>[^.?!\n]+)",
            re.IGNORECASE,
        ),
    ),
    ("profile_name", re.compile(r"\bMy\s+name\s+is\s+(?P<object>[^.?!\n]+)", re.IGNORECASE)),
    ("profile_identity", re.compile(r"\bI\s+am\s+(?P<object>[^.?!\n]+)", re.IGNORECASE)),
    ("profile_identity", re.compile(r"\bI'm\s+(?P<object>[^.?!\n]+)", re.IGNORECASE)),
    ("profile_role", re.compile(r"\bI\s+work\s+as\s+(?P<object>[^.?!\n]+)", re.IGNORECASE)),
    ("profile_location", re.compile(r"\bI\s+live\s+in\s+(?P<object>[^.?!\n]+)", re.IGNORECASE)),
]
_COMMON_FACT_SPELLING_CORRECTIONS = {
    "aniversary": "anniversary",
    "anniversery": "anniversary",
}
_MONTH_NAMES = {
    "january": "January",
    "february": "February",
    "march": "March",
    "april": "April",
    "may": "May",
    "june": "June",
    "july": "July",
    "august": "August",
    "september": "September",
    "october": "October",
    "november": "November",
    "december": "December",
}
_NAME_LIKE_PREDICATES = {
    "creator",
    "profile_name",
}


def _redacted_startup_error(component: str, exc: Exception) -> RuntimeError:
    message = redact_secrets(f"{component} initialization failed: {type(exc).__name__}: {exc}")
    return RuntimeError(message)


def build_runtime(
    config: HubConfig | dict[str, Any] | None = None,
) -> RuntimeDependencies:
    cfg = (
        parse_config(config) if isinstance(config, dict) else (config or load_config())
    )
    ensure_token_hash_secret(cfg)
    set_schema_path(cfg.schema_config.file)
    try:
        validate_schema_compatibility(load_schema())
    except Exception:
        set_schema_path(None)
        raise
    data_dir = Path(cfg.paths.data_dir)
    try:
        if cfg.providers.metadata_db == "postgres":
            metadata_store = PostgresMetadataStore(
                cfg.storage.metadata_providers.postgres.url
            )
        elif cfg.providers.metadata_db == "mongodb":
            mongodb_config = cfg.storage.metadata_providers.mongodb
            metadata_store = MongoDBMetadataStore(
                uri=mongodb_config.uri,
                database=mongodb_config.database,
                conversations_collection=mongodb_config.conversations_collection,
                facts_collection=mongodb_config.facts_collection,
                generated_summaries_collection=mongodb_config.generated_summaries_collection,
                schema_collection=mongodb_config.schema_collection,
            )
        else:
            metadata_store = SQLiteMetadataStore(data_dir / "metadata.sqlite3")
    except Exception as exc:
        raise _redacted_startup_error("Metadata provider", exc) from exc
    _validate_metadata_schema(
        metadata_store=metadata_store,
        supported_versions=cfg.storage.metadata_schema_versions,
    )

    if cfg.providers.embeddings == "openai":
        embedding_provider: EmbeddingProvider = OpenAIEmbeddingProvider(
            cfg.providers.embedding_model,
            cfg.providers.embedding_dimension,
            cfg.openai.base_url,
            cfg.openai.api_key,
        )
    else:
        embedding_provider = LocalEmbeddingProvider()

    expected_dimension = embedding_provider.dimension
    requested_vector_provider = (
        VectorProviderName.MEMORY.value
        if cfg.providers.vector_db == VectorProviderAlias.IN_MEMORY.value
        else cfg.providers.vector_db
    )
    _log_startup_policy(cfg=cfg, requested_vector_provider=requested_vector_provider)
    vector_fallback_active = False
    fallback_reasons: list[str] = []
    if requested_vector_provider == VectorProviderName.MEMORY.value:
        vector_store = InMemoryVectorStore(dimension=expected_dimension)
    else:
        try:
            vector_store = _build_vector_store(
                provider=requested_vector_provider,
                cfg=cfg,
                data_dir=data_dir,
                expected_dimension=expected_dimension,
            )
        except Exception as exc:
            if not cfg.storage.vector.allow_fallback:
                raise _redacted_startup_error("Vector provider", exc) from exc
            logger.warning(
                "Vector store initialization failed (%s); falling back to in-memory provider",
                redact_secrets(f"{type(exc).__name__}: {exc}"),
                extra={
                    "event": "vector_fallback_activated",
                    "requested_vector_provider": requested_vector_provider,
                    "effective_vector_provider": VectorProviderName.MEMORY.value,
                    "error_type": type(exc).__name__,
                },
            )
            vector_store = InMemoryVectorStore(dimension=expected_dimension)
            vector_fallback_active = True
            fallback_reasons.append(type(exc).__name__)

    _validate_vector_dimension(
        embedding_dimension=expected_dimension, vector_store=vector_store
    )
    actual_vector_provider = (
        VectorProviderName.MEMORY.value if vector_fallback_active else requested_vector_provider
    )
    vector_health = vector_store.health() if hasattr(vector_store, "health") else {}
    embedding_index_metadata = _embedding_index_metadata(
        cfg=cfg,
        embedding_provider=embedding_provider,
        vector_provider=actual_vector_provider,
        vector_store=vector_store,
        vector_health=vector_health,
    )
    embedding_index_mismatch = _validate_embedding_index_metadata(
        metadata_store=metadata_store,
        vector_provider=actual_vector_provider,
        vector_store=vector_store,
        vector_health=vector_health,
        embedding_index_metadata=embedding_index_metadata,
    )

    if cfg.storage.dry_run:
        logger.warning("DRY-RUN enabled: write operations will be skipped")
        metadata_store = DryRunMetadataStore(metadata_store)
        vector_store = DryRunVectorStore(vector_store)

    mode = "ok"
    if vector_fallback_active:
        mode = "degraded"
    if cfg.storage.dry_run:
        mode = "dry_run"
    health_state = {
        "mode": mode,
        "metadata_provider": cfg.providers.metadata_db,
        "vector_provider": actual_vector_provider,
        "requested_vector_provider": requested_vector_provider,
        "vector_fallback_active": vector_fallback_active,
        "reasons": fallback_reasons,
        "embedding": embedding_index_metadata["embedding"],
        "embedding_health": _embedding_readiness(
            cfg=cfg,
            embedding_provider=embedding_provider,
            live_probe=cfg.observability.embedding_readiness_probe,
        ),
        "tokenizer_enabled": cfg.tokenizer.enabled,
        "vector_index": embedding_index_metadata["vector_index"],
        "embedding_index_mismatch": embedding_index_mismatch,
    }

    return RuntimeDependencies(
        embedding_provider=embedding_provider,
        metadata_store=metadata_store,
        vector_store=vector_store,
        health_state=health_state,
        allow_trusted_appends=cfg.storage.allow_trusted_appends,
        tokenizer_enabled=cfg.tokenizer.enabled,
        tokenizer_encoding=cfg.tokenizer.encoding,
        ask_max_context_tokens=cfg.ask.max_context_tokens,
        chunking_strategy=cfg.chunking.strategy,
        chunking_max_tokens=cfg.chunking.max_tokens,
        chunking_overlap_tokens=cfg.chunking.overlap_tokens,
        retrieval_vector_score_threshold=cfg.retrieval.vector_score_threshold,
        retrieval_keyword_enabled=cfg.retrieval.keyword_enabled,
        retrieval_keyword_candidate_limit=cfg.retrieval.keyword_candidate_limit,
        retrieval_keyword_weight=cfg.retrieval.keyword_weight,
        retrieval_metadata_weight=cfg.retrieval.metadata_weight,
        retrieval_candidate_multiplier=cfg.retrieval.candidate_multiplier,
        graph_enabled=cfg.memory.graph_enabled,
    )


def _build_vector_store(
    *,
    provider: str,
    cfg: HubConfig,
    data_dir: Path,
    expected_dimension: int,
) -> Any:
    if provider == VectorProviderName.LANCEDB.value:
        return LanceDBVectorStore(
            data_dir / "lancedb", dimension=expected_dimension
        )
    if provider == VectorProviderName.CHROMADB.value:
        chroma_config = cfg.storage.vector_providers.chromadb
        return ChromaDBVectorStore(
            path=chroma_config.path,
            url=chroma_config.url,
            host=chroma_config.host,
            port=chroma_config.port,
            collection_name=chroma_config.collection,
            dimension=expected_dimension,
        )
    if provider == VectorProviderName.QDRANT.value:
        qdrant_config = cfg.storage.vector_providers.qdrant
        return QdrantVectorStore(
            url=qdrant_config.url,
            api_key=qdrant_config.api_key,
            collection_name=qdrant_config.collection,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
            prefer_grpc=qdrant_config.prefer_grpc,
        )
    if provider == VectorProviderName.MILVUS.value:
        milvus_config = cfg.storage.vector_providers.milvus
        return MilvusVectorStore(
            uri=milvus_config.uri,
            token=milvus_config.token,
            collection_name=milvus_config.collection,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.WEAVIATE.value:
        weaviate_config = cfg.storage.vector_providers.weaviate
        return WeaviateVectorStore(
            url=weaviate_config.url,
            api_key=weaviate_config.api_key,
            collection_name=weaviate_config.collection,
            dimension=expected_dimension,
        )
    if provider == VectorProviderName.MONGODB_ATLAS.value:
        atlas_config = cfg.storage.vector_providers.mongodb_atlas
        return MongoDBAtlasVectorStore(
            uri=atlas_config.uri,
            database=atlas_config.database,
            collection_name=atlas_config.collection,
            index_name=atlas_config.index,
            dimension=expected_dimension,
        )
    if provider == VectorProviderName.ELASTICSEARCH.value:
        elastic_config = cfg.storage.vector_providers.elasticsearch
        return ElasticsearchVectorStore(
            url=elastic_config.url,
            username=elastic_config.username,
            password=elastic_config.password,
            index_name=elastic_config.index,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.OPENSEARCH.value:
        opensearch_config = cfg.storage.vector_providers.opensearch
        return OpenSearchVectorStore(
            url=opensearch_config.url,
            username=opensearch_config.username,
            password=opensearch_config.password,
            index_name=opensearch_config.index,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.REDIS.value:
        redis_config = cfg.storage.vector_providers.redis
        return RedisVectorStore(
            url=redis_config.url,
            index_name=redis_config.index,
            key_prefix=redis_config.key_prefix,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.PINECONE.value:
        pinecone_config = cfg.storage.vector_providers.pinecone
        return PineconeVectorStore(
            api_key=pinecone_config.api_key,
            index_name=pinecone_config.index,
            namespace=pinecone_config.namespace,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
            cloud=pinecone_config.cloud,
            region=pinecone_config.region,
            create_index=pinecone_config.create_index,
        )
    if provider == VectorProviderName.TURBOPUFFER.value:
        turbopuffer_config = cfg.storage.vector_providers.turbopuffer
        return TurbopufferVectorStore(
            api_key=turbopuffer_config.api_key,
            namespace=turbopuffer_config.namespace,
            region=turbopuffer_config.region,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.VESPA.value:
        vespa_config = cfg.storage.vector_providers.vespa
        return VespaVectorStore(
            url=vespa_config.url,
            token=vespa_config.token,
            namespace=vespa_config.namespace,
            schema=vespa_config.schema_name,
            rank_profile=vespa_config.rank_profile,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.TYPESENSE.value:
        typesense_config = cfg.storage.vector_providers.typesense
        return TypesenseVectorStore(
            url=typesense_config.url,
            api_key=typesense_config.api_key,
            collection_name=typesense_config.collection,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    if provider == VectorProviderName.PGVECTOR.value:
        pgvector_config = cfg.storage.vector_providers.pgvector
        return PGVectorStore(
            pgvector_config.url,
            table_name=pgvector_config.table_name,
            dimension=expected_dimension,
            distance=cfg.storage.vector.distance,
        )
    raise ValueError(f"Unsupported vector provider: {provider}")


def configure_runtime(
    *,
    runtime: RuntimeDependencies | None = None,
    config: HubConfig | dict[str, Any] | None = None,
) -> RuntimeDependencies:
    global _RUNTIME
    _RUNTIME = runtime or build_runtime(config)
    return _RUNTIME


def _runtime() -> RuntimeDependencies:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = build_runtime()
    return _RUNTIME


def validate_json(obj: dict[str, Any]) -> None:
    validate_conversation(obj)


def infer_topics(messages: list[dict[str, Any]]) -> list[str]:
    text_blob = " ".join(str(message.get("text", "")) for message in messages)
    topics: list[str] = []
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(text_blob):
            topics.append(topic)
    return topics


def enrich_topics(obj: dict[str, Any]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        return

    existing = metadata.get("topics")
    inferred = infer_topics(obj.get("messages", []))
    if not inferred and not isinstance(existing, list):
        return

    merged: list[str] = []
    for topic in existing if isinstance(existing, list) else []:
        if isinstance(topic, str) and topic not in merged:
            merged.append(topic)
    for topic in inferred:
        if topic not in merged:
            merged.append(topic)

    metadata["topics"] = merged


def enrich_auto_tags(obj: dict[str, Any]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        return

    tag_sources: dict[str, list[str]] = {}
    for tag in _auto_tags_from_source(obj):
        _add_auto_tag(tag_sources, tag, "source")
    for tag in _auto_tags_from_topics(metadata):
        _add_auto_tag(tag_sources, tag, "topic")
    for tag in _auto_tags_from_entities(obj):
        _add_auto_tag(tag_sources, tag, "entity")
    for tag in _auto_tags_from_fact_predicates(obj):
        _add_auto_tag(tag_sources, tag, "fact_predicate")

    manual_tags = _manual_tag_set(metadata)
    auto_tags = [
        tag
        for tag in tag_sources
        if tag not in manual_tags
    ][:_MAX_AUTO_TAGS]
    metadata["auto_tags"] = auto_tags
    metadata["tag_sources"] = {
        tag: tag_sources[tag]
        for tag in auto_tags
    }


def _add_auto_tag(tag_sources: dict[str, list[str]], tag: str, source: str) -> None:
    normalized = _normalize_auto_tag(tag)
    if not normalized:
        return
    sources = tag_sources.setdefault(normalized, [])
    if source not in sources:
        sources.append(source)


def _auto_tags_from_source(obj: dict[str, Any]) -> list[str]:
    source = str(obj.get("source", "")).strip()
    return [f"source:{source}"] if source else []


def _auto_tags_from_topics(metadata: dict[str, Any]) -> list[str]:
    topics = metadata.get("topics")
    if not isinstance(topics, list):
        return []
    return [str(topic) for topic in topics if isinstance(topic, str)]


def _auto_tags_from_entities(obj: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    title = obj.get("title")
    if isinstance(title, str):
        candidates.append(title)
    for message in obj.get("messages", []):
        if not isinstance(message, dict):
            continue
        text = message.get("text")
        if not isinstance(text, str):
            continue
        candidates.extend(match.group(0) for match in re.finditer(r"\b[A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){1,4}\b", text))
    return candidates


def _auto_tags_from_fact_predicates(obj: dict[str, Any]) -> list[str]:
    predicates = {
        str(fact.get("predicate", ""))
        for fact in extract_facts(obj)
        if isinstance(fact, dict) and fact.get("predicate")
    }
    return [f"fact:{predicate}" for predicate in sorted(predicates)]


def _manual_tag_set(metadata: dict[str, Any]) -> set[str]:
    tags = metadata.get("tags")
    if not isinstance(tags, list):
        return set()
    return {_normalize_auto_tag(str(tag)) for tag in tags if isinstance(tag, str)}


def _normalize_auto_tag(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9:_-]+", "-", value.strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_:")
    return normalized[:80]


def chunk_messages(obj: dict[str, Any]) -> list[dict[str, Any]]:
    return chunk_selected_messages(obj, obj.get("messages", []), start_index=0)


def chunk_selected_messages(
    obj: dict[str, Any], messages: list[dict[str, Any]], *, start_index: int
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    if not isinstance(messages, list):
        raise ValueError("Missing or invalid 'messages' key in conversation object")
    runtime = _runtime()
    conversation_id = str(obj["id"])
    for offset, message in enumerate(messages):
        message_hash = str(message["hash"])
        role = str(message["role"])
        text = str(message["text"])
        token_windows = _message_token_windows(
            text,
            strategy=runtime.chunking_strategy,
            max_tokens=runtime.chunking_max_tokens,
            overlap_tokens=runtime.chunking_overlap_tokens,
            encoding=runtime.tokenizer_encoding,
        )
        for token_window_index, chunk_text in enumerate(token_windows):
            chunk_index = start_index + len(chunks)
            chunks.append(
                {
                    "chunk_id": f"{conversation_id}:{chunk_index}:{message_hash}",
                    "chunk_index": chunk_index,
                    "conversation_id": conversation_id,
                    "message_hash": message_hash,
                    "message_index": start_index + offset,
                    "token_window_index": token_window_index,
                    "role": role,
                    "text": chunk_text,
                    "index_state": "pending_index",
                }
            )
    return chunks


def _message_token_windows(
    text: str,
    *,
    strategy: str,
    max_tokens: int,
    overlap_tokens: int,
    encoding: str,
) -> list[str]:
    if strategy == "message":
        return [text]
    if overlap_tokens >= max_tokens:
        raise ValueError("chunking.overlap_tokens must be less than chunking.max_tokens")
    return split_token_windows(
        text,
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
        encoding=encoding,
    )


def _attach_index_chunks(obj: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        obj["metadata"] = metadata
    metadata["index_chunks"] = [
        {
            "chunk_id": str(chunk["chunk_id"]),
            "chunk_index": int(chunk["chunk_index"]),
            "message_hash": str(chunk["message_hash"]),
            "role": str(chunk["role"]),
            "text": str(chunk["text"]),
            "index_state": str(chunk.get("index_state", "pending_index")),
        }
        for chunk in chunks
    ]


def embed_chunks(
    chunks: list[dict[str, Any]], *, project_id: str | None = None, owner_id: str | None = None
) -> list[dict[str, Any]]:
    runtime = _runtime()
    texts = [chunk["text"] for chunk in chunks]
    provider = str(runtime.health_state.get("embedding", {}).get("provider") or "unknown")
    model = str(runtime.health_state.get("embedding", {}).get("model") or "unknown")
    started = time.perf_counter()
    with _ingestion_stage("embedding", provider=provider, model=model, chunk_count=len(chunks)):
        try:
            vectors = runtime.embedding_provider.embed_texts(texts)
        except Exception as exc:
            metrics.increment(
                "memory_embedding_failures_total",
                provider=provider,
                error_type=type(exc).__name__,
            )
            raise
        finally:
            metrics.observe(
                "memory_embedding_duration_ms",
                (time.perf_counter() - started) * 1000,
                provider=provider,
                model=model,
            )
    if len(vectors) != len(chunks):
        raise ValueError("Embedding provider must return one vector per chunk")

    embeddings: list[dict[str, Any]] = []
    for chunk, vector in zip(chunks, vectors):
        embeddings.append(
            {
                "chunk_id": chunk.get("chunk_id"),
                "chunk_index": chunk["chunk_index"],
                "conversation_id": chunk.get("conversation_id"),
                "project_id": project_id,
                "owner_id": owner_id,
                "message_hash": chunk.get("message_hash"),
                "index_state": "indexed",
                "role": chunk["role"],
                "text": chunk["text"],
                "vector": [float(v) for v in vector],
            }
        )
    return embeddings


def store_metadata(obj: dict[str, Any]) -> str:
    runtime = _runtime()
    return runtime.metadata_store.insert(obj)


def store_vectors(metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False) -> None:
    runtime = _runtime()
    provider = str(runtime.health_state.get("vector_provider") or "unknown")
    with _ingestion_stage(
        "vector_insert",
        vector_provider=provider,
        embedding_count=len(embeddings),
        replace=replace,
    ):
        runtime.vector_store.insert(metadata_id, embeddings, replace=replace)


def _lookup_by_conversation_hash(
    conversation_hash: str | None, *, project_id: str | None = None
) -> dict[str, Any] | None:
    if conversation_hash is None:
        return None
    store = _runtime().metadata_store
    if hasattr(store, "get_by_conversation_hash"):
        return store.get_by_conversation_hash(conversation_hash, project_id=project_id)
    for attr in ("by_id", "rows"):
        rows = getattr(store, attr, None)
        if isinstance(rows, dict):
            for conversation in rows.values():
                if (
                    _conversation_hash(conversation) == conversation_hash
                    and _conversation_project_matches(conversation, project_id)
                ):
                    return conversation
    return None


def _lookup_same_thread(
    incoming: dict[str, Any], *, owner_id: str | None = None, project_id: str | None = None
) -> dict[str, Any] | None:
    if not _runtime().allow_trusted_appends:
        return None
    store = _runtime().metadata_store
    memory_id = str(incoming.get("id", ""))
    if memory_id:
        existing = store.get(memory_id) if hasattr(store, "get") else None
        if isinstance(existing, dict):
            return existing if _conversation_allowed(existing, owner_id, project_id) else None

    metadata = incoming.get("metadata", {})
    upstream_thread_id = (
        metadata.get(ThreadMetadataKey.UPSTREAM_THREAD_ID) if isinstance(metadata, dict) else None
    )
    if isinstance(upstream_thread_id, str) and upstream_thread_id:
        source = str(incoming.get("source", ""))
        if hasattr(store, "get_by_upstream_thread"):
            candidate = store.get_by_upstream_thread(source, upstream_thread_id, project_id=project_id)
            if isinstance(candidate, dict) and _conversation_allowed(candidate, owner_id, project_id):
                return candidate
            return None
        for attr in ("by_id", "rows"):
            rows = getattr(store, attr, None)
            if isinstance(rows, dict):
                for conversation in rows.values():
                    conversation_metadata = conversation.get("metadata", {})
                    if (
                        isinstance(conversation_metadata, dict)
                        and conversation.get("source") == source
                        and conversation_metadata.get(ThreadMetadataKey.UPSTREAM_THREAD_ID)
                        == upstream_thread_id
                    ):
                        if _conversation_allowed(conversation, owner_id, project_id):
                            return conversation
    return None


def _insert_new_conversation(obj: dict[str, Any]) -> tuple[str, bool]:
    store = _runtime().metadata_store
    provider = str(_runtime().health_state.get("metadata_provider") or "unknown")
    with _ingestion_stage("metadata_insert", metadata_provider=provider):
        if hasattr(store, "insert_new"):
            return store.insert_new(obj)
        duplicate = _lookup_by_conversation_hash(_conversation_hash(obj))
        if duplicate is not None:
            return str(duplicate["id"]), False
        if hasattr(store, "get"):
            existing = store.get(str(obj.get("id", "")))
            if isinstance(existing, dict):
                raise ValueError("unauthorized_update: conversation id already exists")
        return store.insert(obj), True


def _append_conversation(
    existing: dict[str, Any], incoming: dict[str, Any], new_messages: list[dict[str, Any]]
) -> dict[str, Any]:
    updated = dict(existing)
    metadata = dict(updated.get("metadata", {}))
    incoming_metadata = incoming.get("metadata", {})
    messages = list(updated.get("messages", [])) + new_messages
    metadata["updated_at"] = incoming["metadata"]["updated_at"]
    metadata["message_hashes"] = [str(message["hash"]) for message in messages]
    metadata["conversation_hash"] = hash_ordered_messages(messages)
    if isinstance(incoming_metadata, dict):
        _merge_thread_metadata(metadata, incoming_metadata)
    updated["messages"] = messages
    updated["metadata"] = metadata
    store = _runtime().metadata_store
    provider = str(_runtime().health_state.get("metadata_provider") or "unknown")
    with _ingestion_stage("metadata_insert", metadata_provider=provider, append=True):
        if hasattr(store, "append_messages"):
            store.append_messages(updated, new_messages)
        else:
            store.insert(updated)
    return updated


def _conversation_hash(conversation: dict[str, Any]) -> str | None:
    metadata = conversation.get("metadata", {})
    if isinstance(metadata, dict):
        value = metadata.get("conversation_hash")
        if isinstance(value, str):
            return value
    return None


def _stamp_owner(conversation: dict[str, Any], *, owner_id: str | None) -> None:
    if owner_id is None:
        return
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        conversation["metadata"] = metadata
    metadata["owner_id"] = _validate_owner_id(owner_id)


def _stamp_project(conversation: dict[str, Any], *, project_id: str) -> None:
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        conversation["metadata"] = metadata
    metadata["project_id"] = _validate_project_id(project_id)


def _scope_conversation_hash(
    conversation: dict[str, Any], *, project_id: str | None
) -> None:
    if project_id is None:
        return
    metadata = conversation.get("metadata", {})
    if not isinstance(metadata, dict):
        return
    conversation_hash = metadata.get("conversation_hash")
    if not isinstance(conversation_hash, str):
        return
    digest = hashlib.sha256(f"{project_id}\n{conversation_hash}".encode("utf-8")).hexdigest()
    metadata["conversation_hash"] = f"sha256:{digest}"


def _normalize_thread_metadata(conversation: dict[str, Any]) -> None:
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        return
    thread_metadata = thread_metadata_from_mapping(metadata)
    if thread_metadata.thread_id is None and thread_metadata.upstream_thread_id:
        thread_metadata.thread_id = _derived_thread_id(
            str(conversation.get("source", "")), thread_metadata.upstream_thread_id
        )
    for key in ThreadMetadataKey:
        metadata.pop(key, None)
    metadata.update(thread_metadata.to_metadata_update())


def _merge_thread_metadata(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    for key in (
        ThreadMetadataKey.THREAD_ID,
        ThreadMetadataKey.UPSTREAM_THREAD_ID,
        ThreadMetadataKey.PARENT_CONVERSATION_ID,
    ):
        if key not in target and isinstance(incoming.get(key), str):
            target[key] = str(incoming[key])
    existing_related = target.get(ThreadMetadataKey.RELATED_CONVERSATION_IDS)
    incoming_related = incoming.get(ThreadMetadataKey.RELATED_CONVERSATION_IDS)
    related: list[str] = []
    if isinstance(existing_related, list):
        related.extend(str(item) for item in existing_related if isinstance(item, str))
    if isinstance(incoming_related, list):
        related.extend(str(item) for item in incoming_related if isinstance(item, str))
    if related:
        target[ThreadMetadataKey.RELATED_CONVERSATION_IDS] = _unique_strings(related)


def _derived_thread_id(source: str, upstream_thread_id: str) -> str:
    source_part = source.strip() or "unknown"
    return f"{source_part}:{upstream_thread_id}"


def _owner_id_from_conversation(conversation: Any) -> str | None:
    if not isinstance(conversation, dict):
        return None
    metadata = conversation.get("metadata", {})
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("owner_id")
    return str(value) if value is not None else None


def _conversation_owner_matches(conversation: Any, owner_id: str | None) -> bool:
    if owner_id is None:
        return True
    return _owner_id_from_conversation(conversation) == owner_id


def _project_id_from_conversation(conversation: Any) -> str | None:
    if not isinstance(conversation, dict):
        return None
    metadata = conversation.get("metadata", {})
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("project_id")
    return str(value) if value is not None else None


def _conversation_project_matches(conversation: Any, project_id: str | None) -> bool:
    if project_id is None:
        return True
    conversation_project_id = _project_id_from_conversation(conversation)
    if conversation_project_id is None and project_id == LOCAL_DEFAULT_PROJECT_ID:
        return True
    return conversation_project_id == project_id


def _memory_status(conversation: Any) -> str:
    if not isinstance(conversation, dict):
        return _MEMORY_STATUS_ACTIVE
    metadata = conversation.get("metadata", {})
    if not isinstance(metadata, dict):
        return _MEMORY_STATUS_ACTIVE
    value = metadata.get("memory_status")
    return str(value) if value is not None else _MEMORY_STATUS_ACTIVE


def _conversation_is_active(conversation: Any) -> bool:
    return _memory_status(conversation) == _MEMORY_STATUS_ACTIVE


def _validate_memory_status_filter(memory_status: str | None) -> str:
    normalized = str(memory_status or _MEMORY_STATUS_ACTIVE).lower()
    if normalized not in _MEMORY_STATUS_VALUES:
        raise ValueError(
            "memory_status must be one of: " + ", ".join(sorted(_MEMORY_STATUS_VALUES))
        )
    return normalized


def _conversation_authorized(
    conversation: Any, owner_id: str | None, project_id: str | None
) -> bool:
    if project_id is not None:
        return _conversation_project_matches(conversation, project_id)
    return _conversation_owner_matches(conversation, owner_id) and _conversation_project_matches(
        conversation, project_id
    )


def _conversation_allowed(
    conversation: Any, owner_id: str | None, project_id: str | None
) -> bool:
    return _conversation_authorized(
        conversation, owner_id, project_id
    ) and _conversation_is_active(conversation)


def _conversation_visible(
    conversation: Any, owner_id: str | None, project_id: str | None, memory_status: str | None
) -> bool:
    if not _conversation_authorized(conversation, owner_id, project_id):
        return False
    status_filter = _validate_memory_status_filter(memory_status)
    if status_filter == _MEMORY_STATUS_ALL:
        return True
    return _memory_status(conversation) == status_filter


def _conversation_matches_filters(conversation: Any, filters: ConversationFilters) -> bool:
    if not isinstance(conversation, dict):
        return False
    if filters.source and str(conversation.get("source", "")) != filters.source:
        return False
    metadata = conversation.get("metadata", {})
    if filters.thread_id:
        if (
            not isinstance(metadata, dict)
            or str(metadata.get(ThreadMetadataKey.THREAD_ID, "")) != filters.thread_id
        ):
            return False
    if not _datetime_in_range(
        str(conversation.get("timestamp", "")),
        date_from=filters.date_from,
        date_to=filters.date_to,
        field_name="conversation.timestamp",
    ):
        return False
    if not filters.tags:
        return True
    conversation_tags = metadata.get("tags", []) if isinstance(metadata, dict) else []
    if not isinstance(conversation_tags, list):
        return False
    tag_set = {str(tag) for tag in conversation_tags if isinstance(tag, str)}
    return set(filters.tags).issubset(tag_set)


def _fact_owner_matches(fact: Any, owner_id: str | None) -> bool:
    if owner_id is None:
        return True
    if not isinstance(fact, dict):
        return False
    return str(fact.get("owner_id", "")) == owner_id


def _fact_project_matches(fact: Any, project_id: str | None) -> bool:
    if project_id is None:
        return True
    if not isinstance(fact, dict):
        return False
    fact_project_id = fact.get("project_id")
    if fact_project_id is None and project_id == LOCAL_DEFAULT_PROJECT_ID:
        return True
    return str(fact_project_id) == project_id


def _fact_allowed(fact: Any, owner_id: str | None, project_id: str | None) -> bool:
    if project_id is not None:
        return _fact_project_matches(fact, project_id)
    return _fact_owner_matches(fact, owner_id) and _fact_project_matches(fact, project_id)


def _resolve_project(
    *,
    owner_id: str | None,
    project_id: str | None,
    required_role: str,
) -> str:
    store = _runtime().metadata_store
    if project_id is None:
        if hasattr(store, "ensure_default_project"):
            project = store.ensure_default_project(owner_id)
            return str(project["id"])
        return _default_project_id(owner_id) if owner_id is not None else LOCAL_DEFAULT_PROJECT_ID

    project = _validate_project_id(project_id)
    if owner_id is None:
        if project != LOCAL_DEFAULT_PROJECT_ID:
            raise PermissionError("project access denied")
        return project
    if hasattr(store, "project_has_role"):
        if not store.project_has_role(project_id=project, user_id=owner_id, role=required_role):
            raise PermissionError("project access denied")
    return project


def _detect_new_messages(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> list[dict[str, Any]]:
    existing_messages = existing.get("messages", [])
    incoming_messages = incoming.get("messages", [])
    if not isinstance(existing_messages, list) or not isinstance(incoming_messages, list):
        raise ValueError("duplicate_conflict: stored or incoming messages are invalid")
    existing_hashes = [str(message.get("hash", "")) for message in existing_messages]
    incoming_hashes = [str(message.get("hash", "")) for message in incoming_messages]
    common_prefix_len = min(len(existing_hashes), len(incoming_hashes))
    if incoming_hashes[:common_prefix_len] != existing_hashes[:common_prefix_len]:
        raise ValueError("duplicate_conflict: same thread has conflicting message history")
    if len(incoming_hashes) <= len(existing_hashes):
        return []
    seen = set(existing_hashes)
    new_messages: list[dict[str, Any]] = []
    for message in incoming_messages[len(existing_hashes) :]:
        message_hash = str(message["hash"])
        if message_hash not in seen:
            new_messages.append(message)
            seen.add(message_hash)
    return new_messages


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_message(role: str, text: str) -> str:
    digest = hashlib.sha256(f"{role}\n{text}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def hash_ordered_messages(messages: list[dict[str, Any]]) -> str:
    joined = "\n".join(str(message["hash"]) for message in messages)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def normalize_conversation_json(
    payload: Any, *, source: str | None = None, strict_transcript: bool = False
) -> dict[str, Any]:
    """Normalize a conversation JSON object with default values."""
    now = _utc_now_iso()
    payload = _coerce_payload(payload, strict_transcript=strict_transcript)
    _enforce_payload_limits(payload)
    normalized = dict(payload)
    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    top_level_tags = normalized.pop("tags", None)
    if top_level_tags is not None and "tags" not in metadata:
        metadata["tags"] = top_level_tags

    if "messages" not in normalized and isinstance(
        normalized.get("conversation"), list
    ):
        normalized["messages"] = normalized["conversation"]
    normalized.pop("conversation", None)

    messages = normalized.get("messages")
    if isinstance(messages, list):
        normalized["messages"] = _normalize_messages(messages)

    normalized.setdefault("id", str(uuid4()))
    normalized.setdefault("source", source or "unknown")
    normalized["source"] = _normalize_source(normalized.get("source"))
    normalized.setdefault("timestamp", now)
    normalized["timestamp"] = _validate_datetime_string(
        normalized["timestamp"], field_name="timestamp"
    )
    if "imported_at" not in metadata and isinstance(metadata.get("saved_at"), str):
        metadata["imported_at"] = metadata["saved_at"]
    _normalize_metadata_summary(metadata)
    metadata.setdefault("imported_at", now)
    metadata["imported_at"] = _validate_datetime_string(
        metadata["imported_at"], field_name="metadata.imported_at"
    )
    metadata["updated_at"] = now
    if not isinstance(normalized.get("messages"), list):
        raise ValueError("ambiguous input: messages must be an array")
    metadata["message_hashes"] = [
        str(message["hash"]) for message in normalized["messages"]
    ]
    metadata["conversation_hash"] = hash_ordered_messages(normalized["messages"])
    normalized["metadata"] = metadata
    _normalize_thread_metadata(normalized)
    _enforce_payload_limits(normalized)
    return normalized


def _coerce_payload(payload: Any, *, strict_transcript: bool) -> dict[str, Any]:
    if isinstance(payload, dict):
        if any(key in payload for key in ("messages", "conversation", "content", "tags")):
            if "content" in payload and "messages" not in payload and "conversation" not in payload:
                content = payload["content"]
                if isinstance(content, list):
                    normalized = dict(payload)
                    normalized["messages"] = content
                    normalized.pop("content", None)
                    return normalized
            return payload
        raise ValueError("ambiguous input: object must include messages, conversation, content, or tags")
    if isinstance(payload, str):
        if not strict_transcript:
            raise ValueError("raw transcript input requires strict_transcript=True")
        return {"messages": _parse_strict_transcript(payload)}
    raise ValueError("ambiguous input: expected object or strict raw transcript string")


def _normalize_metadata_summary(metadata: dict[str, Any]) -> None:
    if "summary" not in metadata:
        return
    summary = metadata["summary"]
    if not isinstance(summary, str):
        raise ValueError("metadata.summary must be a string")
    summary = " ".join(summary.strip().split())
    if not summary:
        metadata.pop("summary", None)
        return
    if len(summary) > _MAX_METADATA_SUMMARY_CHARS:
        raise ValueError(
            f"metadata.summary exceeds max length of {_MAX_METADATA_SUMMARY_CHARS} characters"
        )
    metadata["summary"] = summary


def _parse_strict_transcript(text: str) -> list[dict[str, str]]:
    if len(text.encode("utf-8")) > _MAX_RAW_TRANSCRIPT_BYTES:
        raise ValueError("raw transcript exceeds max_raw_transcript_bytes")
    messages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    lines = text.splitlines()
    for line in lines:
        match = _TRANSCRIPT_LINE_RE.match(line)
        if match:
            role = _normalize_role(match.group(1))
            current = {"role": role, "text": match.group(2)}
            messages.append(current)
            continue
        if current is None:
            if line.strip():
                raise ValueError("ambiguous raw transcript before first speaker boundary")
            continue
        current["text"] += "\n" + line
    if not messages:
        raise ValueError("raw transcript did not contain any speaker boundaries")
    return messages


def _normalize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    normalized_messages: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for index, item in enumerate(messages):
        message = _normalize_message(item, index=index)
        if message["hash"] in seen_hashes:
            continue
        seen_hashes.add(message["hash"])
        normalized_messages.append(message)
    if not normalized_messages:
        raise ValueError("messages must contain at least one non-empty message")
    return normalized_messages


def _normalize_message(message: Any, *, index: int) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise ValueError(f"messages[{index}] must be an object")
    normalized = dict(message)
    if "text" not in normalized and "content" in normalized:
        normalized["text"] = normalized["content"]
    normalized.pop("content", None)
    role = _normalize_role(normalized.get("role"))
    text = _normalize_text(normalized.get("text"), index=index)
    computed_hash = sha256_message(role, text)
    supplied_hash = normalized.get("hash")
    if supplied_hash is not None and supplied_hash != computed_hash:
        raise ValueError(f"messages[{index}].hash does not match server-computed hash")
    normalized["role"] = role
    normalized["text"] = text
    normalized["hash"] = computed_hash
    return normalized


def _normalize_role(role: Any) -> str:
    value = str(role).strip().lower()
    value = _ROLE_ALIASES.get(value, value)
    if value not in {"user", "assistant"}:
        raise ValueError(f"unknown message role: {role}")
    return value


def _normalize_source(source: Any) -> str:
    if not isinstance(source, str):
        raise ValueError("source must be a string")
    value = source.strip()
    if not value:
        raise ValueError("source must be non-empty")
    if len(value) > 128:
        raise ValueError("source exceeds max length 128")
    return value


def _validate_datetime_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 datetime string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    return value


def _parse_filter_datetime(value: str | None, *, field_name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    return _aware_utc(parsed)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _datetime_in_range(
    value: str | None,
    *,
    date_from: str | None,
    date_to: str | None,
    field_name: str,
) -> bool:
    lower = _parse_filter_datetime(date_from, field_name="date_from")
    upper = _parse_filter_datetime(date_to, field_name="date_to")
    if lower is None and upper is None:
        return True
    if not value:
        return False
    parsed = _parse_filter_datetime(value, field_name=field_name)
    if parsed is None:
        return False
    if lower is not None and parsed < lower:
        return False
    return not (upper is not None and parsed > upper)


def _normalize_text(text: Any, *, index: int) -> str:
    if not isinstance(text, str):
        raise ValueError(f"messages[{index}].text must be a string")
    value = text.strip("\ufeff\r\n")
    if not value.strip():
        raise ValueError(f"messages[{index}].text must be non-empty")
    if len(value.encode("utf-8")) > _MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds max_message_bytes")
    return value


def _enforce_payload_limits(payload: dict[str, Any]) -> None:
    payload_bytes = len(json_dumps(payload).encode("utf-8"))
    if payload_bytes > _MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds max_payload_bytes")
    messages = payload.get("messages")
    if isinstance(messages, list) and len(messages) > _MAX_MESSAGES:
        raise ValueError("payload exceeds max_messages")
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata_bytes = len(json_dumps(metadata).encode("utf-8"))
        if metadata_bytes > _MAX_METADATA_BYTES:
            raise ValueError("metadata exceeds max_metadata_bytes")


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def ingest_messages(
    conversation_json: Any,
    *,
    strict_transcript: bool = False,
    owner_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_WRITER
    )
    # 0. Normalize
    with _ingestion_stage("normalize"):
        conversation_json = normalize_conversation_json(
            conversation_json, strict_transcript=strict_transcript
        )
        _stamp_owner(conversation_json, owner_id=owner_id)
        _stamp_project(conversation_json, project_id=effective_project_id)
        _scope_conversation_hash(conversation_json, project_id=effective_project_id)

    # 1. Validate JSON against schema
    with _ingestion_stage("validate_schema"):
        validate_json(conversation_json)

    # 2. Enrich metadata topics from message text
    with _ingestion_stage("normalize", enrichment=True):
        enrich_topics(conversation_json)
        enrich_auto_tags(conversation_json)

    with _ingestion_stage("dedupe_lookup"):
        conversation_hash = conversation_json["metadata"]["conversation_hash"]
        duplicate = _lookup_by_conversation_hash(
            conversation_hash, project_id=effective_project_id
        )
        duplicate_metadata_id = None
        if duplicate is not None and _conversation_allowed(
            duplicate, owner_id, effective_project_id
        ):
            duplicate_metadata_id = str(duplicate["id"])
            # We proceed to re-index even if it's a duplicate by hash,
            # to ensure the vector store is in sync with the metadata.
            conversation_json["id"] = duplicate_metadata_id

    _attach_generated_summaries(
        conversation_json,
        owner_id=owner_id,
        project_id=effective_project_id,
    )

    existing_by_id = None
    store = _runtime().metadata_store
    if hasattr(store, "get"):
        existing_by_id = store.get(str(conversation_json.get("id", "")))
    if existing_by_id is not None and not _conversation_allowed(
        existing_by_id, owner_id, effective_project_id
    ):
        raise ValueError("unauthorized_update: conversation id already exists")
    if existing_by_id is not None and not _runtime().allow_trusted_appends:
        # If it's a duplicate by ID but not by hash, it's an unauthorized update.
        # But if it's the SAME hash (we already checked), it's fine.
        if _conversation_hash(existing_by_id) != conversation_hash:
            raise ValueError("unauthorized_update: conversation id already exists")

    same_thread = _lookup_same_thread(
        conversation_json, owner_id=owner_id, project_id=effective_project_id
    )
    if same_thread is not None:
        new_messages = _detect_new_messages(same_thread, conversation_json)
        if not new_messages:
            # Re-index check for same_thread
            # If it's already fully indexed, we COULD skip, 
            # but for consistency we fall through to ensure vectors are there.
            start_index = 0
            updated = same_thread
        else:
            start_index = len(same_thread.get("messages", []))
            updated = _append_conversation(same_thread, conversation_json, new_messages)
            enrich_topics(updated)
            enrich_auto_tags(updated)
            _attach_generated_summaries(
                updated,
                owner_id=owner_id,
                project_id=effective_project_id,
            )
        
        # If we are here, we either have new messages or we are re-indexing existing
        indexing_messages = new_messages if new_messages else updated.get("messages", [])
        chunk_start_index = (
            _next_chunk_index(updated, fallback_start_index=start_index)
            if new_messages
            else 0
        )
        with _ingestion_stage("chunk", message_count=len(indexing_messages)):
            chunks = chunk_selected_messages(
                updated, indexing_messages, start_index=chunk_start_index
            )
        if new_messages:
            _extend_index_chunks(updated, chunks)
        else:
            _attach_index_chunks(updated, chunks)
        if new_messages or chunks:
            provider = str(_runtime().health_state.get("metadata_provider") or "unknown")
            with _ingestion_stage("metadata_insert", metadata_provider=provider):
                store.insert(updated)
        try:
            embeddings = embed_chunks(
                chunks, project_id=effective_project_id, owner_id=owner_id
            )
            store_vectors(str(updated["id"]), embeddings, replace=(start_index == 0))
            _mark_chunks_indexed(str(updated["id"]), chunks)
        except Exception:
            _mark_chunks_indexing_failed(str(updated["id"]), chunks)
            raise
        if new_messages:
            with _ingestion_stage("fact_extract", message_count=len(new_messages)):
                _store_facts_for_messages(
                    updated,
                    new_messages,
                    start_message_index=start_index,
                )
                graph_counts = _store_graph_for_conversation(updated)
        else:
            graph_counts = {"entities": 0, "relationships": 0}
        logger.info(
            "Conversation inserted",
            extra={
                "event": "conversation_inserted",
                "operation": "memory_insert",
                "conversation_id": str(updated["id"]),
                "deduplicated": not bool(new_messages),
                "appended_messages": len(new_messages),
                "embedded_chunks": len(chunks),
            },
        )
        result = {
            "status": "ok",
            "id": str(updated["id"]),
            "deduplicated": not bool(new_messages),
            "appended_messages": len(new_messages),
            "embedded_chunks": len(chunks),
            "chunks": len(chunks),
        }
        return _with_graph_counts(result, graph_counts)

    # 3. Chunk messages
    with _ingestion_stage("chunk", message_count=len(conversation_json.get("messages", []))):
        chunks = chunk_messages(conversation_json)
    _attach_index_chunks(conversation_json, chunks)

    # 4. Store metadata with pending chunks before embedding. Exact duplicates
    # already have metadata; retries should re-index without attempting a second
    # primary-key insert.
    if duplicate_metadata_id is None:
        metadata_id, inserted = _insert_new_conversation(conversation_json)
    else:
        metadata_id, inserted = duplicate_metadata_id, False

    # 5. Embed and write vectors
    try:
        embeddings = embed_chunks(chunks, project_id=effective_project_id, owner_id=owner_id)
        store_vectors(metadata_id, embeddings, replace=not inserted)
        _mark_chunks_indexed(metadata_id, chunks)
    except Exception:
        _mark_chunks_indexing_failed(metadata_id, chunks)
        raise
    if inserted:
        with _ingestion_stage("fact_extract"):
            _store_facts_for_conversation(conversation_json)
            graph_counts = _store_graph_for_conversation(conversation_json)
    else:
        graph_counts = {"entities": 0, "relationships": 0}

    logger.info(
        "Conversation inserted",
        extra={
            "event": "conversation_inserted",
            "operation": "memory_insert",
            "conversation_id": metadata_id,
            "deduplicated": not inserted,
            "embedded_chunks": len(chunks),
        },
    )

    # 6. Return stored object
    result = {
        "status": "ok",
        "id": metadata_id,
        "deduplicated": not inserted,
        "appended_messages": 0,
        "embedded_chunks": len(chunks),
        "chunks": len(chunks),
    }
    return _with_graph_counts(result, graph_counts)


def store_pending_review_memory(
    conversation_json: Any,
    *,
    strict_transcript: bool = False,
    owner_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_WRITER
    )
    conversation_json = normalize_conversation_json(
        conversation_json, strict_transcript=strict_transcript
    )
    _stamp_owner(conversation_json, owner_id=owner_id)
    _stamp_project(conversation_json, project_id=effective_project_id)
    _scope_conversation_hash(conversation_json, project_id=effective_project_id)
    validate_json(conversation_json)
    enrich_topics(conversation_json)
    enrich_auto_tags(conversation_json)
    metadata = _ensure_metadata(conversation_json)
    metadata["memory_status"] = _MEMORY_STATUS_PENDING_REVIEW
    metadata["pending_review_received_at"] = _utc_now_iso()

    store = _runtime().metadata_store
    existing_by_id = store.get(str(conversation_json.get("id", ""))) if hasattr(store, "get") else None
    if existing_by_id is not None and not _conversation_authorized(
        existing_by_id, owner_id, effective_project_id
    ):
        raise ValueError("unauthorized_update: conversation id already exists")
    if existing_by_id is not None and _conversation_hash(existing_by_id) != metadata["conversation_hash"]:
        raise ValueError("unauthorized_update: conversation id already exists")

    memory_id, inserted = _insert_new_conversation(conversation_json)
    return {
        "status": "pending_review",
        "id": memory_id,
        "memory_status": _MEMORY_STATUS_PENDING_REVIEW,
        "inserted": inserted,
        "chunks": 0,
    }


def approve_pending_memory(
    memory_id: str, *, owner_id: str | None = None, project_id: str | None = None
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_WRITER
    )
    conversation = _runtime().metadata_store.get(memory_id)
    if not _conversation_authorized(conversation, owner_id, effective_project_id):
        return {"status": "not_found", "id": memory_id}
    if _memory_status(conversation) != _MEMORY_STATUS_PENDING_REVIEW:
        return {
            "status": "error",
            "error_code": "memory_not_pending_review",
            "error_message": "memory is not pending review",
            "id": memory_id,
            "memory_status": _memory_status(conversation),
        }
    assert isinstance(conversation, dict)
    metadata = _ensure_metadata(conversation)
    metadata["memory_status"] = _MEMORY_STATUS_ACTIVE
    metadata["approved_at"] = _utc_now_iso()
    metadata.setdefault("save_intent", "user_confirmed")
    _runtime().metadata_store.insert(conversation)
    result = ingest_messages(conversation, owner_id=owner_id, project_id=effective_project_id)
    _store_facts_for_conversation(conversation)
    _store_graph_for_conversation(conversation)
    result["memory_status"] = _MEMORY_STATUS_ACTIVE
    return result


def reject_pending_memory(
    memory_id: str, *, owner_id: str | None = None, project_id: str | None = None
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_WRITER
    )
    conversation = _runtime().metadata_store.get(memory_id)
    if not _conversation_authorized(conversation, owner_id, effective_project_id):
        return {"status": "not_found", "id": memory_id}
    if _memory_status(conversation) != _MEMORY_STATUS_PENDING_REVIEW:
        return {
            "status": "error",
            "error_code": "memory_not_pending_review",
            "error_message": "memory is not pending review",
            "id": memory_id,
            "memory_status": _memory_status(conversation),
        }
    assert isinstance(conversation, dict)
    metadata = _ensure_metadata(conversation)
    metadata["memory_status"] = _MEMORY_STATUS_REJECTED
    metadata["rejected_at"] = _utc_now_iso()
    _runtime().metadata_store.insert(conversation)
    return {"status": "ok", "id": memory_id, "memory_status": _MEMORY_STATUS_REJECTED}


def _is_fully_indexed(metadata_id: str) -> bool:
    runtime = _runtime()
    # Always re-index for in-memory store to ensure consistency after restart
    if runtime.health_state.get("vector_provider") == "memory":
        return False

    store = runtime.metadata_store
    if hasattr(store, "is_fully_indexed"):
        return store.is_fully_indexed(metadata_id)
    # Default to False to ensure indexing if we can't check
    return False


def _mark_chunks_indexed(metadata_id: str, chunks: list[dict[str, Any]]) -> None:
    store = _runtime().metadata_store
    if hasattr(store, "mark_chunks_indexed"):
        store.mark_chunks_indexed(metadata_id, [str(chunk["chunk_id"]) for chunk in chunks])


def _mark_chunks_indexing_failed(metadata_id: str, chunks: list[dict[str, Any]]) -> None:
    store = _runtime().metadata_store
    if hasattr(store, "mark_chunks_indexing_failed"):
        store.mark_chunks_indexing_failed(
            metadata_id, [str(chunk["chunk_id"]) for chunk in chunks]
        )


def search(
    query: str,
    top_k: int = 5,
    result_mode: str = SearchResultMode.CHUNKS.value,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_status: str = _MEMORY_STATUS_ACTIVE,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    tags: Sequence[str] | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    _validate_result_mode(result_mode)
    status_filter = _validate_memory_status_filter(memory_status)
    filters = ConversationFilters.from_options(
        source=source,
        date_from=date_from,
        date_to=date_to,
        tags=tags,
        thread_id=thread_id,
    )
    runtime = _runtime()
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    query_vector = runtime.embedding_provider.embed_texts([query])[0]
    candidate_multiplier = max(
        1, int(getattr(runtime, "retrieval_candidate_multiplier", _SEARCH_CANDIDATE_MULTIPLIER))
    )
    candidate_k = 100 if filters.has_filters else max(top_k, min(top_k * candidate_multiplier, 100))
    vector_provider = str(runtime.health_state.get("vector_provider") or "unknown")
    started = time.perf_counter()
    matches = runtime.vector_store.search(query_vector, top_k=candidate_k)
    metrics.observe(
        "memory_vector_search_duration_ms",
        (time.perf_counter() - started) * 1000,
        provider=vector_provider,
    )

    ids = [str(match["memory_id"]) for match in matches]
    keyword_conversations = _keyword_conversations(
        runtime.metadata_store,
        query,
        enabled=runtime.retrieval_keyword_enabled,
        limit=runtime.retrieval_keyword_candidate_limit,
        owner_id=owner_id,
        project_id=effective_project_id,
        memory_status=status_filter,
        filters=filters,
    )
    ids.extend(str(conversation["id"]) for conversation in keyword_conversations if "id" in conversation)
    ids = _unique_strings(ids)
    conversations = runtime.metadata_store.get_many(ids)
    conversations = {
        memory_id: enriched
        for memory_id, conversation in conversations.items()
        if (enriched := _with_generated_summary_metadata(conversation)) is not None
    }
    for conversation in keyword_conversations:
        memory_id = str(conversation.get("id", ""))
        if memory_id and memory_id not in conversations:
            conversations[memory_id] = _with_generated_summary_metadata(conversation) or conversation

    results: list[dict[str, Any]] = []
    for match in matches:
        memory_id = str(match["memory_id"])
        score = float(match["score"])
        conversation = conversations.get(memory_id)
        if not _conversation_visible(conversation, owner_id, effective_project_id, status_filter):
            continue
        if not _conversation_matches_filters(conversation, filters):
            continue
        row = {
            "id": memory_id,
            "score": score,
            "chunk_index": int(match["chunk_index"]),
            "role": match["role"],
            "text": match["text"],
            "conversation": conversation,
        }
        if _passes_retrieval_threshold(
            query=query,
            row=row,
            threshold=runtime.retrieval_vector_score_threshold,
        ):
            results.append(row)

    existing_keys = {
        (str(row["id"]), int(row["chunk_index"]), str(row["text"])) for row in results
    }
    for conversation in keyword_conversations:
        memory_id = str(conversation.get("id", ""))
        if not memory_id:
            continue
        if not _conversation_visible(conversation, owner_id, effective_project_id, status_filter):
            continue
        if not _conversation_matches_filters(conversation, filters):
            continue
        row = _keyword_result_row(
            conversation,
            score=runtime.retrieval_vector_score_threshold,
        )
        key = (str(row["id"]), int(row["chunk_index"]), str(row["text"]))
        if key not in existing_keys:
            results.append(row)
            existing_keys.add(key)

    ranked = _rank_retrieval_results(
        query,
        results,
        keyword_weight=runtime.retrieval_keyword_weight,
        metadata_weight=runtime.retrieval_metadata_weight,
    )
    grouped = group_conversation_results(ranked)
    return {"status": "ok", "results": _apply_result_mode(grouped, result_mode)[:top_k]}


def _validate_result_mode(result_mode: str) -> None:
    if result_mode not in _RESULT_MODES:
        raise ValueError(result_mode_error_message())


def _apply_result_mode(rows: list[dict[str, Any]], result_mode: str) -> list[dict[str, Any]]:
    if result_mode == SearchResultMode.CHUNKS:
        return rows
    if result_mode == SearchResultMode.THREADS:
        return _thread_result_rows(rows)
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        memory_id = str(row.get("id", ""))
        if memory_id not in grouped:
            grouped[memory_id] = []
            order.append(memory_id)
        grouped[memory_id].append(row)

    compacted: list[dict[str, Any]] = []
    for memory_id in order:
        chunk_rows = grouped[memory_id]
        best = dict(chunk_rows[0])
        evidence = [_citation_from_row(row) for row in chunk_rows]
        best["matching_chunks"] = len(chunk_rows)
        best["evidence_chunks"] = evidence
        best["used_in_answer"] = False
        if result_mode == SearchResultMode.CONVERSATIONS:
            best["chunk_index"] = 0
            best["role"] = ""
            best["text"] = _conversation_result_text(best.get("conversation"), evidence)
        compacted.append(best)
    return compacted


def _thread_result_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        thread_id = _thread_id_from_conversation(row.get("conversation"))
        if thread_id not in grouped:
            grouped[thread_id] = []
            order.append(thread_id)
        grouped[thread_id].append(row)

    thread_rows: list[dict[str, Any]] = []
    for thread_id in order:
        thread_matches = grouped[thread_id]
        best = dict(thread_matches[0])
        conversation_ids = _unique_strings(
            [str(row.get("id", "")) for row in thread_matches if row.get("id")]
        )
        best[ThreadResultKey.THREAD_ID] = thread_id
        best[ThreadResultKey.THREAD_CONVERSATION_IDS] = conversation_ids
        best[ThreadResultKey.THREAD_CONVERSATION_COUNT] = len(conversation_ids)
        best[ThreadResultKey.MATCHING_CONVERSATIONS] = len(conversation_ids)
        best[ThreadResultKey.MATCHING_CHUNKS] = sum(
            int(row.get("matching_chunks", 1)) for row in thread_matches
        )
        best[ThreadResultKey.EVIDENCE_CHUNKS] = [_citation_from_row(row) for row in thread_matches]
        best[ThreadResultKey.USED_IN_ANSWER] = False
        best["chunk_index"] = 0
        best["role"] = ""
        best["text"] = _thread_result_text(thread_id, conversation_ids, thread_matches)
        return_row = _strip_internal_ranking_fields(best)
        thread_rows.append(return_row)
    return thread_rows


def _thread_id_from_conversation(conversation: Any) -> str:
    if not isinstance(conversation, dict):
        return "thread:unknown"
    metadata = conversation.get("metadata")
    if isinstance(metadata, dict) and metadata.get(ThreadMetadataKey.THREAD_ID):
        return str(metadata[ThreadMetadataKey.THREAD_ID])
    return f"conversation:{conversation.get('id', 'unknown')}"


def _thread_result_text(
    thread_id: str, conversation_ids: list[str], thread_matches: list[dict[str, Any]]
) -> str:
    return (
        f"{thread_id}: {len(conversation_ids)} conversation(s), "
        f"{len(thread_matches)} matching chunk(s)"
    )


def _strip_internal_ranking_fields(row: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(row)
    cleaned.pop("_grouped_score", None)
    cleaned.pop("_original_rank", None)
    cleaned.pop("_ranking_score", None)
    cleaned.pop("_ranking_boost", None)
    return cleaned


def _conversation_result_text(conversation: Any, evidence: list[dict[str, Any]]) -> str:
    if isinstance(conversation, dict):
        title = str(conversation.get("title") or conversation.get("source") or conversation.get("id"))
        return f"{title}: {len(evidence)} matching chunk(s)"
    return f"{len(evidence)} matching chunk(s)"


def group_conversation_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    group_scores: dict[str, float] = {}
    group_counts: dict[str, int] = {}
    for row in rows:
        memory_id = str(row.get("id", ""))
        if not memory_id:
            continue
        score = float(row.get("_ranking_score", row.get("score", 0.0)))
        group_scores[memory_id] = min(score, group_scores.get(memory_id, score))
        group_counts[memory_id] = group_counts.get(memory_id, 0) + 1

    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        memory_id = str(row.get("id", ""))
        row_score = float(row.get("_ranking_score", row.get("score", 0.0)))
        conversation_score = group_scores.get(memory_id, row_score)
        grouped_score = row_score
        if row_score - conversation_score <= _CONVERSATION_GROUP_SCORE_WINDOW:
            grouped_score = conversation_score
        enriched_row = dict(row)
        enriched_row["conversation_score"] = conversation_score
        enriched_row["conversation_match_count"] = group_counts.get(memory_id, 1)
        enriched_row["_original_rank"] = index
        enriched_row["_grouped_score"] = grouped_score
        enriched.append(enriched_row)

    grouped = sorted(
        enriched,
        key=lambda row: (
            float(row["_grouped_score"]),
            -float(row.get("_ranking_boost", 0.0)),
            str(row.get("id", "")),
            float(row.get("score", 0.0)),
            int(row.get("chunk_index", 0)),
            int(row["_original_rank"]),
        ),
    )
    for row in grouped:
        row.pop("_grouped_score", None)
        row.pop("_original_rank", None)
        row.pop("_ranking_score", None)
        row.pop("_ranking_boost", None)
    return grouped


def _keyword_conversations(
    metadata_store: Any,
    query: str,
    *,
    enabled: bool,
    limit: int,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_status: str = _MEMORY_STATUS_ACTIVE,
    filters: ConversationFilters | None = None,
) -> list[dict[str, Any]]:
    if not enabled:
        return []
    status_filter = _validate_memory_status_filter(memory_status)
    filters = filters or ConversationFilters()
    if hasattr(metadata_store, "search_text"):
        try:
            candidates = metadata_store.search_text(query, limit=limit, project_id=project_id)
        except TypeError:
            candidates = metadata_store.search_text(query, limit=limit)
        return [
            item
            for item in candidates
            if isinstance(item, dict)
            and _conversation_visible(item, owner_id, project_id, status_filter)
            and _conversation_matches_filters(item, filters)
        ]
    rows = getattr(metadata_store, "by_id", None) or getattr(metadata_store, "rows", None)
    if not isinstance(rows, dict):
        return []
    tokens = _query_tokens(query)
    if not tokens:
        return []
    matches: list[dict[str, Any]] = []
    for conversation in rows.values():
        if not isinstance(conversation, dict):
            continue
        if not _conversation_visible(conversation, owner_id, project_id, status_filter):
            continue
        if not _conversation_matches_filters(conversation, filters):
            continue
        text = _conversation_search_text(conversation).lower()
        if all(token in text for token in tokens):
            matches.append(conversation)
    return matches[:limit]


def _passes_retrieval_threshold(*, query: str, row: dict[str, Any], threshold: float) -> bool:
    score = float(row.get("score", 0.0))
    if score <= threshold:
        return True
    return _keyword_overlap(query, row) > 0 or _metadata_overlap(query, row.get("conversation")) > 0


def _rank_retrieval_results(
    query: str,
    rows: list[dict[str, Any]],
    *,
    keyword_weight: float,
    metadata_weight: float,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        keyword_overlap = _keyword_overlap(query, row)
        metadata_overlap = _metadata_overlap(query, row.get("conversation"))
        ranking_score = float(row.get("score", 0.0))
        ranking_boost = (
            min(keyword_overlap, 3) * keyword_weight
            + min(metadata_overlap, 3) * metadata_weight
        )
        ranking_score -= ranking_boost
        enriched = dict(row)
        enriched["_ranking_score"] = max(0.0, ranking_score)
        enriched["_ranking_boost"] = ranking_boost
        enriched["_original_rank"] = index
        ranked.append(enriched)
    return sorted(
        ranked,
        key=lambda row: (
            float(row["_ranking_score"]),
            str(row.get("id", "")),
            int(row.get("chunk_index", 0)),
            int(row["_original_rank"]),
        ),
    )


def _keyword_result_row(conversation: dict[str, Any], *, score: float) -> dict[str, Any]:
    messages = conversation.get("messages", [])
    first_message = messages[0] if isinstance(messages, list) and messages else {}
    if not isinstance(first_message, dict):
        first_message = {}
    return {
        "id": str(conversation["id"]),
        "score": float(score),
        "chunk_index": 0,
        "role": str(first_message.get("role", "")),
        "text": str(first_message.get("text", "")),
        "conversation": conversation,
    }


def _keyword_overlap(query: str, row: dict[str, Any]) -> int:
    tokens = set(_query_tokens(query))
    if not tokens:
        return 0
    text = str(row.get("text", "")).lower()
    conversation = row.get("conversation")
    if isinstance(conversation, dict):
        text += " " + _conversation_search_text(conversation).lower()
    return sum(1 for token in tokens if token in text)


def _metadata_overlap(query: str, conversation: Any) -> int:
    if not isinstance(conversation, dict):
        return 0
    tokens = set(_query_tokens(query))
    if not tokens:
        return 0
    metadata = conversation.get("metadata", {})
    metadata_values: list[str] = [str(conversation.get("source", "")), str(conversation.get("title", ""))]
    if isinstance(metadata, dict):
        metadata_values.extend(_metadata_search_values(metadata))
    text = " ".join(metadata_values).lower()
    return sum(1 for token in tokens if token in text)


def _conversation_search_text(conversation: dict[str, Any]) -> str:
    parts = [
        str(conversation.get("id", "")),
        str(conversation.get("source", "")),
        str(conversation.get("title", "")),
    ]
    messages = conversation.get("messages", [])
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                parts.append(str(message.get("role", "")))
                parts.append(str(message.get("text", "")))
    metadata = conversation.get("metadata", {})
    if isinstance(metadata, dict):
        parts.extend(_metadata_search_values(metadata))
    return " ".join(parts)


def _metadata_search_values(metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("tags", "auto_tags", "tag_sources", "topics", "summary", "generated_summary"):
        value = metadata.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif isinstance(value, dict):
            for item in value.values():
                if isinstance(item, list):
                    values.extend(str(child) for child in item)
                elif item is not None:
                    values.append(str(item))
        elif value is not None:
            values.append(str(value))
    return values


def _query_tokens(query: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", query)
        if len(token) >= 2
    ][:8]


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def retrieve(
    memory_id: str,
    *,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_status: str = _MEMORY_STATUS_ACTIVE,
) -> dict[str, Any] | None:
    runtime = _runtime()
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    status_filter = _validate_memory_status_filter(memory_status)
    conversation = runtime.metadata_store.get(memory_id)
    if not _conversation_visible(conversation, owner_id, effective_project_id, status_filter):
        return None
    return _with_generated_summary_metadata(conversation)


def ask(
    question: str,
    top_k: int = 5,
    max_context_tokens: int | None = None,
    result_mode: str = SearchResultMode.CHUNKS.value,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_status: str = _MEMORY_STATUS_ACTIVE,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    tags: Sequence[str] | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    _validate_result_mode(result_mode)
    filters = ConversationFilters.from_options(
        source=source,
        date_from=date_from,
        date_to=date_to,
        tags=tags,
        thread_id=thread_id,
    )
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    status_filter = _validate_memory_status_filter(memory_status)
    fact_answer = _answer_from_facts(
        question,
        top_k=top_k,
        result_mode=result_mode,
        owner_id=owner_id,
        project_id=effective_project_id,
        filters=filters,
    )
    if fact_answer is not None and status_filter == _MEMORY_STATUS_ACTIVE:
        return fact_answer

    search_result = _search_for_ask(
        question=question,
        top_k=top_k,
        result_mode=result_mode,
        owner_id=owner_id,
        project_id=effective_project_id,
        memory_status=status_filter,
        filters=filters,
    )
    matches = search_result.get("results", [])
    if not matches:
        return {
            "status": "ok",
            "results": [],
            "answer": "I could not find relevant memory for that question.",
            "citations": [],
            "confidence": "none",
            "confidence_reason": "No matching memory or facts were found.",
            "answer_basis": "not_found",
            "provenance": [],
            "evidence": [],
            "structured_evidence": {"facts": [], "results": []},
        }

    runtime = _runtime()
    budget_enabled = runtime.tokenizer_enabled or max_context_tokens is not None
    if not budget_enabled:
        return _ask_from_matches(matches, top_k=top_k)

    token_budget = (
        max_context_tokens
        if max_context_tokens is not None
        else runtime.ask_max_context_tokens
    )
    selected_matches, citations, context_lines, tokens_used, chunks_dropped = (
        _select_ask_context(
            matches=matches[:top_k],
            max_context_tokens=token_budget,
            encoding=runtime.tokenizer_encoding,
        )
    )
    answer = (
        "Based on stored memory:\n" + "\n".join(context_lines)
        if context_lines
        else "I could not fit relevant memory within the context budget."
    )
    return {
        "status": "ok",
        "results": selected_matches,
        "answer": answer,
        "citations": citations,
        "confidence": _confidence_from_matches(selected_matches),
        "confidence_reason": _confidence_reason_from_matches(selected_matches),
        "answer_basis": "direct_memory",
        "provenance": _provenance_from_matches(selected_matches, citations),
        "evidence": _chunk_evidence_from_matches(selected_matches),
        "structured_evidence": {
            "facts": [],
            "results": selected_matches,
        },
        "context_tokens_used": tokens_used,
        "chunks_selected": len(selected_matches),
        "chunks_dropped": chunks_dropped,
        "tokenizer_used": tokenizer_used(runtime.tokenizer_encoding),
    }


def _search_for_ask(
    question: str,
    *,
    top_k: int,
    result_mode: str,
    owner_id: str | None = None,
    project_id: str | None = None,
    memory_status: str = _MEMORY_STATUS_ACTIVE,
    filters: ConversationFilters | None = None,
) -> dict[str, Any]:
    filters = filters or ConversationFilters()
    try:
        return search(
            query=question,
            top_k=top_k,
            result_mode=result_mode,
            owner_id=owner_id,
            project_id=project_id,
            memory_status=memory_status,
            source=filters.source,
            date_from=filters.date_from,
            date_to=filters.date_to,
            tags=filters.tags,
            thread_id=filters.thread_id,
        )
    except TypeError:
        if result_mode != SearchResultMode.CHUNKS:
            raise
        return search(query=question, top_k=top_k)


def _next_chunk_index(
    conversation: dict[str, Any], *, fallback_start_index: int
) -> int:
    metadata = conversation.get("metadata", {})
    index_chunks = metadata.get("index_chunks") if isinstance(metadata, dict) else None
    if isinstance(index_chunks, list):
        indexes = [
            int(chunk.get("chunk_index", -1))
            for chunk in index_chunks
            if isinstance(chunk, dict)
        ]
        if indexes:
            return max(indexes) + 1
    return fallback_start_index


def _extend_index_chunks(obj: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
    metadata = obj.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        obj["metadata"] = metadata
    existing = metadata.get("index_chunks")
    if not isinstance(existing, list):
        existing = []
    metadata["index_chunks"] = existing + [
        {
            "chunk_id": str(chunk["chunk_id"]),
            "chunk_index": int(chunk["chunk_index"]),
            "message_hash": str(chunk["message_hash"]),
            "role": str(chunk["role"]),
            "text": str(chunk["text"]),
            "index_state": str(chunk.get("index_state", "pending_index")),
        }
        for chunk in chunks
    ]


def _ask_from_matches(matches: list[dict[str, Any]], *, top_k: int) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    context_lines: list[str] = []
    for row in matches:
        citation = _citation_from_row(row)
        citations.append(citation)
        context_lines.append(
            f"- [{citation['id']}#{citation['chunk_index']}] {citation['text']}"
        )

    answer = "Based on stored memory:\n" + "\n".join(context_lines[:top_k])
    selected = matches[:top_k]
    selected_citations = citations[:top_k]
    return {
        "status": "ok",
        "results": selected,
        "answer": answer,
        "citations": selected_citations,
        "confidence": _confidence_from_matches(selected),
        "confidence_reason": _confidence_reason_from_matches(selected),
        "answer_basis": "direct_memory",
        "provenance": _provenance_from_matches(selected, selected_citations),
        "evidence": _chunk_evidence_from_matches(selected),
        "structured_evidence": {
            "facts": [],
            "results": selected,
        },
    }


def _citation_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "chunk_index": int(row.get("chunk_index", 0)),
        "score": float(row.get("score", 0.0)),
        "text": row.get("text", ""),
    }


def _select_ask_context(
    *,
    matches: list[dict[str, Any]],
    max_context_tokens: int,
    encoding: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], int, int]:
    selected_matches: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    context_lines: list[str] = []
    tokens_used = 0
    chunks_dropped = 0

    for row in matches:
        citation_id = row.get("id")
        chunk_index = int(row.get("chunk_index", 0))
        prefix = f"- [{citation_id}#{chunk_index}] "
        prefix_tokens = count_tokens(prefix, encoding)
        remaining = max_context_tokens - tokens_used - prefix_tokens
        if remaining <= 0:
            chunks_dropped += 1
            continue

        text = str(row.get("text", ""))
        text_tokens = count_tokens(text, encoding)
        selected_text = text
        if text_tokens > remaining:
            selected_text = truncate_to_tokens(text, remaining, encoding)
            text_tokens = count_tokens(selected_text, encoding)
        if not selected_text:
            chunks_dropped += 1
            continue

        line = prefix + selected_text
        line_tokens = prefix_tokens + text_tokens
        selected_row = dict(row)
        selected_row["text"] = selected_text
        citation = _citation_from_row({**row, "text": selected_text})
        selected_matches.append(selected_row)
        citations.append(citation)
        context_lines.append(line)
        tokens_used += line_tokens

    return selected_matches, citations, context_lines, tokens_used, chunks_dropped


def _confidence_from_matches(matches: list[dict[str, Any]]) -> str:
    if not matches:
        return "none"
    best = min(float(row.get("score", 0.0)) for row in matches)
    if best <= 1.0:
        return "high"
    if best <= 4.0:
        return "medium"
    return "low"


def _provenance_from_matches(
    matches: list[dict[str, Any]], citations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    used = {(str(item.get("id")), int(item.get("chunk_index", 0))) for item in citations}
    grouped: dict[str, dict[str, Any]] = {}
    for row in matches:
        memory_id = str(row.get("id", ""))
        if not memory_id:
            continue
        conversation = row.get("conversation")
        item = grouped.setdefault(
            memory_id,
            {
                "conversation_id": memory_id,
                "source": conversation.get("source") if isinstance(conversation, dict) else None,
                "title": conversation.get("title") if isinstance(conversation, dict) else None,
                "stored_at": conversation.get("timestamp") if isinstance(conversation, dict) else None,
                "matching_chunks": 0,
                "used_in_answer": False,
            },
        )
        item["matching_chunks"] += int(row.get("matching_chunks", 1))
        if (memory_id, int(row.get("chunk_index", 0))) in used:
            item["used_in_answer"] = True
    return list(grouped.values())


def _chunk_evidence_from_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "chunk",
            "conversation_id": row.get("id"),
            "chunk_index": int(row.get("chunk_index", 0)),
            "role": row.get("role"),
            "text": row.get("text"),
            "score": row.get("score"),
            "used_in_answer": True,
        }
        for row in matches
    ]


def _confidence_reason_from_matches(matches: list[dict[str, Any]]) -> str:
    if not matches:
        return "No retrieved chunks were used."
    return "Answer built from ranked retrieved conversation chunks."


def _store_facts_for_conversation(conversation: dict[str, Any]) -> None:
    facts = extract_facts(conversation)
    _store_facts(facts)


def _store_facts_for_messages(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    start_message_index: int,
) -> None:
    facts = extract_facts_from_messages(
        conversation,
        messages,
        start_message_index=start_message_index,
    )
    _store_facts(facts)


def _store_facts(facts: list[dict[str, Any]]) -> None:
    if not facts:
        return
    store = _runtime().metadata_store
    if hasattr(store, "insert_facts"):
        store.insert_facts(facts)
        return
    existing = getattr(store, "_facts", [])
    if not isinstance(existing, list):
        existing = []
    active = [fact for fact in existing if fact.get("deleted_at") is None]
    for fact in facts:
        _apply_in_memory_fact_supersession(active, fact)
        active.append(fact)
    setattr(store, "_facts", active)


def _store_graph_for_conversation(conversation: dict[str, Any]) -> dict[str, int]:
    runtime = _runtime()
    if not runtime.graph_enabled:
        return {"entities": 0, "relationships": 0}
    graph = extract_memory_graph(conversation)
    entities = graph["entities"]
    relationships = graph["relationships"]
    if not entities and not relationships:
        return {"entities": 0, "relationships": 0}
    store = runtime.metadata_store
    if hasattr(store, "upsert_graph_records"):
        store.upsert_graph_records(entities=entities, relationships=relationships)
    else:
        _store_graph_in_memory(store, entities=entities, relationships=relationships)
    return {"entities": len(entities), "relationships": len(relationships)}


def _with_graph_counts(result: dict[str, Any], graph_counts: dict[str, int]) -> dict[str, Any]:
    if graph_counts["entities"] or graph_counts["relationships"]:
        result["graph"] = graph_counts
    return result


def _store_graph_in_memory(
    store: Any, *, entities: list[dict[str, Any]], relationships: list[dict[str, Any]]
) -> None:
    existing_entities = {
        str(entity["id"]): dict(entity)
        for entity in getattr(store, "_graph_entities", [])
        if isinstance(entity, dict) and "id" in entity
    }
    existing_relationships = {
        str(relationship["id"]): dict(relationship)
        for relationship in getattr(store, "_graph_relationships", [])
        if isinstance(relationship, dict) and "id" in relationship
    }
    for entity in entities:
        existing_entities[str(entity["id"])] = dict(entity)
    for relationship in relationships:
        _apply_in_memory_relationship_supersession(existing_relationships, relationship)
        existing_relationships[str(relationship["id"])] = dict(relationship)
    setattr(store, "_graph_entities", list(existing_entities.values()))
    setattr(store, "_graph_relationships", list(existing_relationships.values()))


def _apply_in_memory_relationship_supersession(
    existing: dict[str, dict[str, Any]], relationship: dict[str, Any]
) -> None:
    conflict_group = relationship.get("conflict_group")
    if not conflict_group:
        return
    for current in existing.values():
        if (
            current.get("conflict_group") == conflict_group
            and current.get("id") != relationship.get("id")
            and current.get("object") != relationship.get("object")
            and current.get("owner_id") == relationship.get("owner_id")
            and current.get("project_id") == relationship.get("project_id")
            and not current.get("superseded_by")
        ):
            current["superseded_by"] = relationship["id"]
            current["updated_at"] = relationship.get("updated_at")


def extract_facts(conversation: dict[str, Any]) -> list[dict[str, Any]]:
    messages = conversation.get("messages", [])
    return extract_facts_from_messages(
        conversation,
        messages if isinstance(messages, list) else [],
        start_message_index=0,
    )


def extract_facts_from_messages(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    start_message_index: int,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for offset, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        message_index = start_message_index + offset
        text = str(message.get("text", ""))
        for fact in _extract_message_facts(
            text,
            conversation=conversation,
            message_index=message_index,
            source_role=str(message.get("role", "")),
        ):
            facts.append(fact)
    facts.extend(_topic_facts(conversation, messages, start_message_index=start_message_index))
    facts.extend(_external_extracted_facts(conversation, messages, start_message_index=start_message_index))
    return _dedupe_facts(facts)


def _extract_message_facts(
    text: str, *, conversation: dict[str, Any], message_index: int, source_role: str
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    correction = _FACT_CORRECTION_RE.search(text)
    if correction:
        item = correction.group("item").strip()
        new_value = correction.group("new").strip()
        old_value = correction.group("old").strip()
        facts.append(
            _fact(
                subject="user",
                predicate=_owned_item_predicate(item),
                object_value=f"{item} is {new_value}",
                conversation=conversation,
                message_index=message_index,
                qualifiers={"item": item, "corrects": old_value},
                source_role=source_role,
            )
        )
    for rule_name, pattern in _FACT_RULES:
        for match in pattern.finditer(text):
            if rule_name == "own":
                object_value = _clean_fact_object(match.group("object"))
                facts.append(
                    _fact(
                        subject="user",
                        predicate=_owned_item_predicate(object_value),
                        object_value=object_value,
                        conversation=conversation,
                        message_index=message_index,
                        qualifiers=_owned_item_qualifiers(object_value),
                        source_role=source_role,
                    )
                )
            elif rule_name == "favorite":
                name = _normalize_predicate_part(match.group("name"))
                facts.append(
                    _fact(
                        subject="user",
                        predicate=f"favorite_{name}",
                        object_value=_clean_fact_object(match.group("object")),
                        conversation=conversation,
                        message_index=message_index,
                        source_role=source_role,
                    )
                )
            elif rule_name == "subject_creator":
                facts.append(
                    _fact(
                        subject=match.group("subject").strip(),
                        predicate="creator",
                        object_value=_clean_fact_object(match.group("object")),
                        conversation=conversation,
                        message_index=message_index,
                        source_role=source_role,
                    )
                )
            elif rule_name in {"creator", "command_name", "indexing_strategy"}:
                facts.append(
                    _fact(
                        subject=_project_subject(conversation),
                        predicate=rule_name,
                        object_value=_clean_fact_object(match.group("object")),
                        conversation=conversation,
                        message_index=message_index,
                        source_role=source_role,
                    )
                )
            elif rule_name in {"profile_name", "profile_identity", "profile_role", "profile_location"}:
                facts.append(
                    _fact(
                        subject="user",
                        predicate=rule_name,
                        object_value=_clean_fact_object(match.group("object")),
                        conversation=conversation,
                        message_index=message_index,
                        source_role=source_role,
                    )
                )
            elif rule_name == "project_attribute":
                subject = match.group("subject").strip()
                if subject.lower() in {"i", "my", "the", "this"}:
                    continue
                facts.append(
                    _fact(
                        subject=subject,
                        predicate="description",
                        object_value=_clean_fact_object(match.group("object")),
                        conversation=conversation,
                        message_index=message_index,
                        source_role=source_role,
                    )
                )
    return _dedupe_facts(facts)


def _topic_facts(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    start_message_index: int,
) -> list[dict[str, Any]]:
    topics = infer_topics(messages)
    facts: list[dict[str, Any]] = []
    for topic in topics:
        facts.append(
            _fact(
                subject="user",
                predicate="recurring_topic",
                object_value=topic,
                conversation=conversation,
                message_index=start_message_index,
                qualifiers={"topic": topic},
            )
        )
    return facts


def _external_extracted_facts(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    start_message_index: int,
) -> list[dict[str, Any]]:
    extractor = _runtime().fact_extractor
    if not callable(extractor):
        return []
    raw_facts = extractor(conversation=conversation, messages=messages)
    if not isinstance(raw_facts, list):
        return []
    facts: list[dict[str, Any]] = []
    for raw in raw_facts:
        if not isinstance(raw, dict):
            continue
        subject = str(raw.get("subject", "")).strip()
        predicate = str(raw.get("predicate", "")).strip()
        object_value = str(raw.get("object", "")).strip()
        if not subject or not predicate or not object_value:
            continue
        indexes = raw.get("source_message_indexes")
        if isinstance(indexes, list) and indexes:
            message_index = int(indexes[0])
        else:
            message_index = start_message_index
        facts.append(
            _fact(
                subject=subject,
                predicate=predicate,
                object_value=object_value,
                conversation=conversation,
                message_index=message_index,
                qualifiers=raw.get("qualifiers") if isinstance(raw.get("qualifiers"), dict) else None,
            )
        )
    return facts


def _fact(
    *,
    subject: str,
    predicate: str,
    object_value: str,
    conversation: dict[str, Any],
    message_index: int,
    qualifiers: dict[str, Any] | None = None,
    source_role: str | None = None,
) -> dict[str, Any]:
    now = _utc_now_iso()
    source_id = str(conversation.get("id", ""))
    identity = f"{subject}\n{predicate}\n{object_value}\n{source_id}\n{message_index}"
    fact_id = "fact-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    normalized_qualifiers = dict(qualifiers or {})
    if source_role in {"user", "assistant"}:
        normalized_qualifiers.setdefault("source_role", source_role)
    normalized_qualifiers.update(_save_intent_qualifiers(conversation))
    object_normalized, normalization = _normalize_fact_object(
        predicate=predicate, object_value=object_value
    )
    if normalization:
        normalized_qualifiers["normalization"] = normalization
    fact = {
        "id": fact_id,
        "subject": subject,
        "predicate": predicate,
        "object": object_value,
        "qualifiers": normalized_qualifiers,
        "confidence": "medium"
        if normalized_qualifiers.get("save_intent") == "client_auto_save"
        else "high",
        "last_confirmed_at": now,
        "source_conversation_id": source_id,
        "owner_id": _owner_id_from_conversation(conversation),
        "project_id": _project_id_from_conversation(conversation),
        "source_message_indexes": [message_index],
        "created_at": now,
        "updated_at": now,
        "superseded_by": None,
        "superseded_at": None,
        "deleted_at": None,
    }
    fact["object_raw"] = object_value
    fact["object_normalized"] = object_normalized
    fact["source_quality"] = _source_quality_for_fact(fact)
    fact["confidence_reason"] = _confidence_reason_for_fact(fact)
    return fact


def _save_intent_qualifiers(conversation: dict[str, Any]) -> dict[str, str]:
    metadata = conversation.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    qualifiers: dict[str, str] = {}
    for key in ("save_intent", "save_intent_source", "save_intent_evidence"):
        value = metadata.get(key)
        if value is not None and str(value):
            qualifiers[key] = str(value)
    return qualifiers


def _fact_qualifier_value(fact: dict[str, Any], key: str) -> str | None:
    direct = fact.get(key)
    if direct is not None and str(direct):
        return str(direct)
    qualifiers = fact.get("qualifiers")
    if not isinstance(qualifiers, dict):
        return None
    value = qualifiers.get(key)
    return str(value) if value is not None and str(value) else None


def _fact_save_intent_fields(fact: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key in ("save_intent", "save_intent_source"):
        value = _fact_qualifier_value(fact, key)
        if value:
            fields[key] = value
    return fields


def _answer_from_facts(
    question: str,
    *,
    top_k: int,
    result_mode: str,
    owner_id: str | None = None,
    project_id: str | None = None,
    filters: ConversationFilters | None = None,
) -> dict[str, Any] | None:
    filters = filters or ConversationFilters()
    query = _fact_query(question)
    if query is None:
        return None
    facts = _search_facts(
        subject=query.get("subject") if query.get("subject") == "user" else None,
        predicate=query.get("predicate"),
        owner_id=owner_id,
        project_id=project_id,
        conversation_filters=filters,
    )
    facts = _filter_facts_for_question(facts, question, query)
    active = [fact for fact in facts if not fact.get("superseded_by") and not fact.get("deleted_at")]
    if not active:
        return None
    objects = {str(fact.get("object_normalized") or fact.get("object", "")) for fact in active}
    needs_context = _fact_question_needs_context(question)
    basis = "conflict" if len(objects) > 1 else ("mixed" if needs_context else "fact_layer")
    confidence = "low" if basis == "conflict" else str(active[0].get("confidence", "medium"))
    superseded = _superseded_facts_for_active(active, facts)
    public_active = [_public_fact(fact) for fact in active]
    public_superseded = [_public_fact(fact) for fact in superseded]
    fact_evidence = [_fact_evidence(fact, used_in_answer=True) for fact in public_active]
    fact_evidence.extend(_fact_evidence(fact, used_in_answer=False) for fact in public_superseded)
    answer = _fact_answer_text(
        question,
        public_active,
        superseded=public_superseded,
        conflict=basis == "conflict",
    )
    citations = [_fact_citation(fact) for fact in public_active]
    results: list[dict[str, Any]] = []
    if basis == "mixed":
        search_result = _search_for_ask(
            question=question,
            top_k=top_k,
            result_mode=result_mode,
            owner_id=owner_id,
            project_id=project_id,
            filters=filters,
        )
        results = search_result.get("results", [])
        if isinstance(results, list):
            citations.extend(_citation_from_row(row) for row in results[:top_k] if isinstance(row, dict))
            context = "\n".join(
                f"- [{row.get('id')}#{int(row.get('chunk_index', 0))}] {row.get('text', '')}"
                for row in results[:top_k]
                if isinstance(row, dict)
            )
            if context:
                answer = answer + "\nContext from memory:\n" + context
    return {
        "status": "ok",
        "results": results,
        "answer": answer,
        "citations": citations,
        "confidence": confidence,
        "confidence_reason": _confidence_reason_for_facts(public_active, basis),
        "answer_basis": basis,
        "provenance": _provenance_from_facts(public_active),
        "facts": public_active,
        "evidence": fact_evidence,
        "structured_evidence": {
            "facts": fact_evidence,
            "results": results,
        },
    }


def _fact_query(question: str) -> dict[str, str] | None:
    lowered = question.lower()
    subject = _question_project_subject(question)
    if "guitar" in lowered and any(term in lowered for term in ("own", "have", "my")):
        return {"subject": "user", "predicate": "owns_guitar"}
    if "who am i" in lowered or "what do you know about me" in lowered:
        return {"subject": "user", "predicate": "profile_identity"}
    if "my name" in lowered or "what is my name" in lowered:
        return {"subject": "user", "predicate": "profile_name"}
    if "where do i live" in lowered:
        return {"subject": "user", "predicate": "profile_location"}
    if "what do i work as" in lowered or "my job" in lowered:
        return {"subject": "user", "predicate": "profile_role"}
    favorite_match = re.search(r"favorite\s+(?P<name>[A-Za-z0-9 _-]+)", question, re.IGNORECASE)
    if favorite_match:
        return {"subject": "user", "predicate": f"favorite_{_normalize_predicate_part(favorite_match.group('name'))}"}
    if "topic" in lowered or "recurring" in lowered:
        return {"subject": "user", "predicate": "recurring_topic"}
    if "command name" in lowered:
        query = {"predicate": "command_name"}
        if subject:
            query["subject"] = subject
        return query
    if "indexing strategy" in lowered:
        query = {"predicate": "indexing_strategy"}
        if subject:
            query["subject"] = subject
        return query
    favorite = re.search(r"favorite\s+([A-Za-z0-9 _-]+)", lowered)
    if favorite:
        return {"subject": "user", "predicate": f"favorite_{_normalize_predicate_part(favorite.group(1))}"}
    if "creator" in lowered or "who created" in lowered:
        query = {"predicate": "creator"}
        if subject:
            query["subject"] = subject
        return query
    return None


def _question_project_subject(question: str) -> str | None:
    patterns = [
        r"\b(?:who created|creator of|for|about)\s+(?P<subject>[A-Z][A-Za-z0-9 _-]{1,80})",
        r"\b(?P<subject>[A-Z][A-Za-z0-9 _-]{1,80})\?",
    ]
    for pattern in patterns:
        match = re.search(pattern, question)
        if match:
            return match.group("subject").strip(" ?.")
    return None


def _filter_facts_for_question(
    facts: list[dict[str, Any]], question: str, query: dict[str, str]
) -> list[dict[str, Any]]:
    subject = query.get("subject")
    if not subject:
        return facts
    subject_tokens = set(_query_tokens(subject))
    if not subject_tokens:
        return facts
    filtered = [
        fact
        for fact in facts
        if subject_tokens.issubset(set(_query_tokens(str(fact.get("subject", "")))))
    ]
    if filtered:
        return filtered
    question_tokens = set(_query_tokens(question))
    return [
        fact
        for fact in facts
        if set(_query_tokens(str(fact.get("subject", "")))).intersection(question_tokens)
    ] or facts


def _fact_question_needs_context(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in ("context", "source", "why", "when", "where did", "discuss"))


def _superseded_facts_for_active(
    active: list[dict[str, Any]], all_facts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    active_ids = {str(fact.get("id")) for fact in active}
    return [
        fact
        for fact in all_facts
        if str(fact.get("superseded_by")) in active_ids and not fact.get("deleted_at")
    ]


def _search_facts(
    subject: str | None = None,
    predicate: str | None = None,
    owner_id: str | None = None,
    project_id: str | None = None,
    conversation_filters: ConversationFilters | None = None,
    fact_filters: FactFilters | None = None,
) -> list[dict[str, Any]]:
    store = _runtime().metadata_store
    conversation_filters = conversation_filters or ConversationFilters()
    fact_filters = fact_filters or FactFilters()
    if hasattr(store, "search_facts"):
        facts = store.search_facts(
            subject=subject,
            predicate=predicate,
            include_superseded=fact_filters.include_superseded,
            project_id=project_id,
        )
        return [
            fact
            for fact in facts
            if _fact_allowed(fact, owner_id, project_id)
            and _fact_matches_filters(fact, conversation_filters, fact_filters)
        ]
    facts = getattr(store, "_facts", [])
    if not isinstance(facts, list):
        return []
    return [
        fact
        for fact in facts
        if isinstance(fact, dict)
        and (subject is None or str(fact.get("subject")) == subject)
        and (predicate is None or str(fact.get("predicate")) == predicate)
        and _fact_allowed(fact, owner_id, project_id)
        and _fact_matches_filters(fact, conversation_filters, fact_filters)
        and not fact.get("deleted_at")
    ]


def _fact_matches_filters(
    fact: dict[str, Any],
    conversation_filters: ConversationFilters,
    fact_filters: FactFilters,
) -> bool:
    if fact_filters.status == "active" and fact.get("superseded_by"):
        return False
    if fact_filters.status == "superseded" and not fact.get("superseded_by"):
        return False
    if fact_filters.confidence and str(fact.get("confidence")) != fact_filters.confidence:
        return False
    if fact_filters.source_quality and str(fact.get("source_quality")) != fact_filters.source_quality:
        return False
    if fact_filters.save_intent and _fact_qualifier_value(fact, "save_intent") != fact_filters.save_intent:
        return False
    if (
        fact_filters.save_intent_source
        and _fact_qualifier_value(fact, "save_intent_source") != fact_filters.save_intent_source
    ):
        return False
    if not _datetime_in_range(
        str(fact.get("created_at", "")),
        date_from=fact_filters.date_from,
        date_to=fact_filters.date_to,
        field_name="fact.created_at",
    ):
        return False
    freshness = fact.get("last_confirmed_at") or fact.get("updated_at") or fact.get("created_at")
    if not _datetime_in_range(
        str(freshness or ""),
        date_from=fact_filters.freshness_from,
        date_to=fact_filters.freshness_to,
        field_name="fact.last_confirmed_at",
    ):
        return False
    if conversation_filters.has_filters or fact_filters.source:
        source_filters = conversation_filters
        if fact_filters.source and fact_filters.source != conversation_filters.source:
            source_filters = ConversationFilters.from_options(
                source=fact_filters.source,
                date_from=conversation_filters.date_from,
                date_to=conversation_filters.date_to,
                tags=conversation_filters.tags,
                thread_id=conversation_filters.thread_id,
            )
        conversation = _runtime().metadata_store.get(str(fact.get("source_conversation_id", "")))
        if not _conversation_matches_filters(conversation, source_filters):
            return False
    return True


def fact_search(
    *,
    subject: str | None = None,
    predicate: str | None = None,
    include_superseded: bool = False,
    owner_id: str | None = None,
    project_id: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    confidence: str | None = None,
    status: str | None = None,
    source_quality: str | None = None,
    save_intent: str | None = None,
    save_intent_source: str | None = None,
    freshness_from: str | None = None,
    freshness_to: str | None = None,
) -> dict[str, Any]:
    fact_filters = FactFilters.from_options(
        source=source,
        date_from=date_from,
        date_to=date_to,
        confidence=confidence,
        status=status or ("all" if include_superseded else "active"),
        source_quality=source_quality,
        save_intent=save_intent,
        save_intent_source=save_intent_source,
        freshness_from=freshness_from,
        freshness_to=freshness_to,
    )
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    store = _runtime().metadata_store
    if hasattr(store, "search_facts"):
        facts = store.search_facts(
            subject=subject,
            predicate=predicate,
            include_superseded=fact_filters.include_superseded,
            project_id=effective_project_id,
        )
        facts = [
            fact
            for fact in facts
            if _fact_allowed(fact, owner_id, effective_project_id)
            and _fact_matches_filters(fact, ConversationFilters(), fact_filters)
        ]
    else:
        facts = getattr(store, "_facts", [])
        if not isinstance(facts, list):
            facts = []
        facts = [
            fact for fact in facts
            if isinstance(fact, dict)
            and (subject is None or str(fact.get("subject")) == subject)
            and (predicate is None or str(fact.get("predicate")) == predicate)
            and _fact_allowed(fact, owner_id, effective_project_id)
            and _fact_matches_filters(fact, ConversationFilters(), fact_filters)
            and not fact.get("deleted_at")
        ]
    return {"status": "ok", "results": [_public_fact(fact) for fact in facts]}


def graph_entity_search(
    *,
    entity_type: str | None = None,
    name: str | None = None,
    owner_id: str | None = None,
    project_id: str | None = None,
    include_inactive: bool = False,
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    store = _runtime().metadata_store
    if hasattr(store, "search_graph_entities"):
        entities = store.search_graph_entities(
            entity_type=entity_type,
            name=name,
            owner_id=owner_id,
            project_id=effective_project_id,
            include_inactive=include_inactive,
        )
    else:
        entities = [
            entity
            for entity in getattr(store, "_graph_entities", [])
            if isinstance(entity, dict)
            and (entity_type is None or str(entity.get("entity_type")) == entity_type)
            and (name is None or str(entity.get("normalized_name")) == " ".join(name.strip().casefold().split()))
            and _record_allowed(entity, owner_id, effective_project_id)
            and (include_inactive or str(entity.get("review_status")) in {"active", "approved", "needs_review"})
        ]
    return {"status": "ok", "results": entities}


def graph_relationship_search(
    *,
    subject: str | None = None,
    predicate: str | None = None,
    owner_id: str | None = None,
    project_id: str | None = None,
    include_superseded: bool = False,
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    store = _runtime().metadata_store
    if hasattr(store, "search_graph_relationships"):
        relationships = store.search_graph_relationships(
            subject=subject,
            predicate=predicate,
            owner_id=owner_id,
            project_id=effective_project_id,
            include_superseded=include_superseded,
        )
    else:
        relationships = [
            relationship
            for relationship in getattr(store, "_graph_relationships", [])
            if isinstance(relationship, dict)
            and (subject is None or str(relationship.get("subject")) == subject)
            and (predicate is None or str(relationship.get("predicate")) == predicate)
            and _record_allowed(relationship, owner_id, effective_project_id)
            and (include_superseded or not relationship.get("superseded_by"))
        ]
    return {"status": "ok", "results": relationships}


def profile_get(
    subject: str = "user",
    *,
    owner_id: str | None = None,
    project_id: str | None = None,
    source: str | None = None,
    predicate: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    confidence: str | None = None,
    status: str | None = None,
    source_quality: str | None = None,
    save_intent: str | None = None,
    save_intent_source: str | None = None,
    freshness_from: str | None = None,
    freshness_to: str | None = None,
) -> dict[str, Any]:
    facts = fact_search(
        subject=subject,
        predicate=predicate,
        owner_id=owner_id,
        project_id=project_id,
        source=source,
        date_from=date_from,
        date_to=date_to,
        confidence=confidence,
        status=status,
        source_quality=source_quality,
        save_intent=save_intent,
        save_intent_source=save_intent_source,
        freshness_from=freshness_from,
        freshness_to=freshness_to,
    )["results"]
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_READER
    )
    summary = _profile_summary(
        subject=subject,
        facts=facts,
        owner_id=owner_id,
        project_id=effective_project_id,
        filters={
            "predicate": predicate,
            "source": source,
            "date_from": date_from,
            "date_to": date_to,
            "confidence": confidence,
            "status": status or "active",
            "source_quality": source_quality,
            "save_intent": save_intent,
            "save_intent_source": save_intent_source,
            "freshness_from": freshness_from,
            "freshness_to": freshness_to,
        },
    )
    return {
        "status": "ok",
        "subject": subject,
        "summary": summary,
        "facts": facts,
    }


def fact_supersede(
    fact_id: str,
    superseded_by: str,
    *,
    owner_id: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    effective_project_id = _resolve_project(
        owner_id=owner_id, project_id=project_id, required_role=PROJECT_ROLE_WRITER
    )
    existing = fact_search(
        include_superseded=True, owner_id=owner_id, project_id=effective_project_id
    )["results"]
    if not any(fact.get("id") == fact_id for fact in existing):
        return {"status": "not_found", "id": fact_id, "superseded_by": superseded_by}
    store = _runtime().metadata_store
    if hasattr(store, "supersede_fact"):
        updated = store.supersede_fact(fact_id, superseded_by, project_id=effective_project_id)
    else:
        updated = False
        for fact in getattr(store, "_facts", []):
            if (
                isinstance(fact, dict)
                and fact.get("id") == fact_id
                and _fact_project_matches(fact, effective_project_id)
            ):
                now = _utc_now_iso()
                fact["superseded_by"] = superseded_by
                fact["superseded_at"] = now
                fact["updated_at"] = now
                updated = True
    return {"status": "ok" if updated else "not_found", "id": fact_id, "superseded_by": superseded_by}


def _record_allowed(record: dict[str, Any], owner_id: str | None, project_id: str | None) -> bool:
    if owner_id is not None and record.get("owner_id") != owner_id:
        return False
    return _fact_project_matches(record, project_id)


def project_list(*, owner_id: str | None = None) -> dict[str, Any]:
    store = _runtime().metadata_store
    if hasattr(store, "ensure_default_project"):
        store.ensure_default_project(owner_id)
    if hasattr(store, "list_projects"):
        return {"status": "ok", "results": store.list_projects(user_id=owner_id)}
    return {"status": "ok", "results": [_fallback_project(owner_id)]}


def project_default_get(*, owner_id: str | None = None) -> dict[str, Any]:
    store = _runtime().metadata_store
    if hasattr(store, "ensure_default_project"):
        project = _normalize_project_record(store.ensure_default_project(owner_id), owner_id=owner_id)
    else:
        project = _fallback_project(owner_id)
    return {"status": "ok", "project": project}


def project_get(project_id: str, *, owner_id: str | None = None) -> dict[str, Any]:
    project = _validate_project_id(project_id)
    _resolve_project(owner_id=owner_id, project_id=project, required_role=PROJECT_ROLE_READER)
    visible = project_list(owner_id=owner_id)["results"]
    for candidate in visible:
        if isinstance(candidate, dict) and candidate.get("id") == project:
            return {"status": "ok", "project": candidate}
    return {"status": "not_found", "id": project}


def _fallback_project(owner_id: str | None) -> dict[str, Any]:
    project_id = _default_project_id(owner_id) if owner_id is not None else LOCAL_DEFAULT_PROJECT_ID
    return {
        "id": project_id,
        "owner_id": owner_id,
        "name": "Private Default" if owner_id is not None else "Local Default",
        "description": None,
        "is_default": True,
        "created_at": None,
        "updated_at": None,
        "archived_at": None,
        "role": "admin",
    }


def _normalize_project_record(project: Any, *, owner_id: str | None) -> dict[str, Any]:
    if isinstance(project, dict):
        normalized = dict(project)
        normalized.setdefault("role", "admin")
        return normalized
    project_id = str(project)
    return {
        **_fallback_project(owner_id),
        "id": _validate_project_id(project_id),
    }

def authenticate_bearer_token(token: str) -> str | None:
    context = authenticate_bearer_token_context(token)
    return str(context["owner_id"]) if context is not None else None


def authenticate_bearer_token_context(token: str) -> dict[str, object] | None:
    store = _runtime().metadata_store
    if hasattr(store, "auth_context_for_token"):
        context = store.auth_context_for_token(token)
        if context is not None:
            return context
    if hasattr(store, "owner_for_token"):
        owner_id = store.owner_for_token(token)
        if owner_id is not None:
            return {
                "owner_id": str(owner_id),
                "token_id": None,
                "scopes": ["memory:read", "memory:write"],
            }
    auth_tokens = getattr(store, "auth_tokens", None)
    if isinstance(auth_tokens, dict):
        owner_id = auth_tokens.get(token)
        if owner_id is not None:
            return {
                "owner_id": str(owner_id),
                "token_id": None,
                "scopes": ["memory:read", "memory:write"],
            }
    return None


def _fact_answer_text(
    question: str,
    facts: list[dict[str, Any]],
    *,
    superseded: list[dict[str, Any]] | None = None,
    conflict: bool,
) -> str:
    objects = [str(fact.get("object_normalized") or fact.get("object", "")) for fact in facts]
    if conflict:
        return "Stored facts disagree: " + "; ".join(objects)
    if "guitar" in question.lower():
        answer = f"You own {objects[0]}."
    elif len(objects) > 1:
        answer = "; ".join(objects)
    else:
        answer = objects[0]
    if superseded:
        old = "; ".join(str(fact.get("object_normalized") or fact.get("object", "")) for fact in superseded)
        answer += f" Correction noted: earlier memory said {old}."
    return answer


def _profile_summary(
    *,
    subject: str,
    facts: list[dict[str, Any]],
    owner_id: str | None,
    project_id: str | None,
    filters: dict[str, Any],
) -> dict[str, Any]:
    active_facts = [fact for fact in facts if not fact.get("superseded_by")]
    freshest_at = _freshest_fact_timestamp(active_facts)
    source_quality_counts = _count_values(active_facts, "source_quality")
    confidence_counts = _count_values(active_facts, "confidence")
    text = _profile_summary_text(active_facts)
    summary = GeneratedSummary(
        id=_generated_summary_id(
            summary_type="profile",
            target_id=subject,
            project_id=project_id,
            filters=filters,
        ),
        type=SummaryType.PROFILE,
        target_id=subject,
        project_id=project_id,
        owner_id=owner_id,
        text=text,
        basis=SummaryBasis.ACTIVE_FACTS,
        provenance_status=SummaryProvenanceStatus.FACT_IDS,
        fact_count=len(facts),
        active_fact_count=len(active_facts),
        freshest_at=freshest_at,
        source_quality_counts=source_quality_counts,
        confidence_counts=confidence_counts,
        filters={key: str(value) for key, value in filters.items() if value is not None},
        provenance=[
            GeneratedSummaryProvenance(
                fact_id=_optional_string(fact.get("id")),
                predicate=_optional_string(fact.get("predicate")),
                source_conversation_id=_optional_string(fact.get("source_conversation_id")),
                source_message_indexes=_source_message_indexes(fact),
                last_confirmed_at=_optional_string(fact.get("last_confirmed_at")),
            )
            for fact in active_facts
        ],
        generated_at=_utc_now_iso(),
    )
    payload = summary.model_dump(mode="json")
    _store_generated_summary(payload)
    return payload


def _attach_generated_summaries(
    conversation: dict[str, Any], *, owner_id: str | None, project_id: str | None
) -> None:
    conversation_summary = _conversation_summary(
        conversation, owner_id=owner_id, project_id=project_id
    )
    _store_generated_summary(conversation_summary)
    metadata = _ensure_metadata(conversation)
    metadata["generated_summary"] = {
        "id": conversation_summary["id"],
        "type": conversation_summary["type"],
        "text": conversation_summary["text"],
        "basis": conversation_summary["basis"],
        "provenance_status": conversation_summary["provenance_status"],
        "generated_at": conversation_summary["generated_at"],
    }

    topic_summary_ids: list[str] = []
    for topic in _conversation_topics(conversation):
        topic_summary = _topic_summary(
            topic,
            conversation,
            conversation_summary=conversation_summary,
            owner_id=owner_id,
            project_id=project_id,
        )
        _store_generated_summary(topic_summary)
        topic_summary_ids.append(str(topic_summary["id"]))
    metadata["generated_topic_summary_ids"] = topic_summary_ids

    project_summary = _project_summary(
        conversation,
        conversation_summary=conversation_summary,
        owner_id=owner_id,
        project_id=project_id,
    )
    _store_generated_summary(project_summary)
    metadata["generated_project_summary_id"] = project_summary["id"]


def _conversation_summary(
    conversation: dict[str, Any], *, owner_id: str | None, project_id: str | None
) -> dict[str, Any]:
    conversation_id = str(conversation.get("id", ""))
    message_indexes = _message_indexes(conversation)
    text = _conversation_summary_text(conversation)
    summary = GeneratedSummary(
        id=_generated_summary_id(
            summary_type=SummaryType.CONVERSATION,
            target_id=conversation_id,
            project_id=project_id,
            filters={},
        ),
        type=SummaryType.CONVERSATION,
        target_id=conversation_id,
        project_id=project_id,
        owner_id=owner_id,
        text=text,
        basis=SummaryBasis.CONVERSATION_MESSAGES,
        provenance_status=SummaryProvenanceStatus.CONVERSATION_IDS,
        filters={},
        provenance=[
            GeneratedSummaryProvenance(
                conversation_id=conversation_id,
                source=_optional_string(conversation.get("source")),
                title=_optional_string(conversation.get("title")),
                source_conversation_id=conversation_id,
                source_message_indexes=message_indexes,
            )
        ],
        generated_at=_utc_now_iso(),
    )
    return summary.model_dump(mode="json")


def _topic_summary(
    topic: str,
    conversation: dict[str, Any],
    *,
    conversation_summary: dict[str, Any],
    owner_id: str | None,
    project_id: str | None,
) -> dict[str, Any]:
    conversation_id = str(conversation.get("id", ""))
    summary = GeneratedSummary(
        id=_generated_summary_id(
            summary_type=SummaryType.TOPIC,
            target_id=topic,
            project_id=project_id,
            filters={"conversation_id": conversation_id},
        ),
        type=SummaryType.TOPIC,
        target_id=topic,
        project_id=project_id,
        owner_id=owner_id,
        text=f"{topic}: {conversation_summary['text']}",
        basis=SummaryBasis.TOPIC_CONVERSATIONS,
        provenance_status=SummaryProvenanceStatus.CONVERSATION_IDS,
        filters={"topic": topic, "conversation_id": conversation_id},
        provenance=[
            GeneratedSummaryProvenance(
                conversation_id=conversation_id,
                source=_optional_string(conversation.get("source")),
                title=_optional_string(conversation.get("title")),
                source_conversation_id=conversation_id,
                source_message_indexes=_message_indexes(conversation),
            )
        ],
        generated_at=_utc_now_iso(),
    )
    return summary.model_dump(mode="json")


def _project_summary(
    conversation: dict[str, Any],
    *,
    conversation_summary: dict[str, Any],
    owner_id: str | None,
    project_id: str | None,
) -> dict[str, Any]:
    target_id = project_id or LOCAL_DEFAULT_PROJECT_ID
    conversation_id = str(conversation.get("id", ""))
    summary = GeneratedSummary(
        id=_generated_summary_id(
            summary_type=SummaryType.PROJECT,
            target_id=target_id,
            project_id=project_id,
            filters={"conversation_id": conversation_id},
        ),
        type=SummaryType.PROJECT,
        target_id=target_id,
        project_id=project_id,
        owner_id=owner_id,
        text=f"{target_id}: {conversation_summary['text']}",
        basis=SummaryBasis.PROJECT_CONVERSATIONS,
        provenance_status=SummaryProvenanceStatus.CONVERSATION_IDS,
        filters={"conversation_id": conversation_id},
        provenance=[
            GeneratedSummaryProvenance(
                conversation_id=conversation_id,
                source=_optional_string(conversation.get("source")),
                title=_optional_string(conversation.get("title")),
                source_conversation_id=conversation_id,
                source_message_indexes=_message_indexes(conversation),
            )
        ],
        generated_at=_utc_now_iso(),
    )
    return summary.model_dump(mode="json")


def _conversation_summary_text(conversation: dict[str, Any]) -> str:
    subject = _conversation_summary_subject(conversation)
    snippets: list[str] = []
    messages = conversation.get("messages", [])
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip() or "message"
            text = _compact_summary_text(str(message.get("text", "")))
            if text:
                snippets.append(f"{role}: {text}")
            if len(snippets) >= 3:
                break
    body = "; ".join(snippets) if snippets else "No message text was available."
    return _truncate_summary_text(f"{subject}: {body}")


def _conversation_summary_subject(conversation: dict[str, Any]) -> str:
    title = str(conversation.get("title") or "").strip()
    if title:
        return title
    topics = _conversation_topics(conversation)
    if topics:
        return "Conversation about " + ", ".join(topics[:4])
    source = str(conversation.get("source") or "conversation").strip()
    return f"{source} conversation"


def _compact_summary_text(text: str) -> str:
    value = " ".join(text.strip().split())
    if not value:
        return ""
    sentence = re.split(r"(?<=[.!?])\s+", value, maxsplit=1)[0]
    return _truncate_summary_text(sentence, limit=220)


def _truncate_summary_text(text: str, *, limit: int = 600) -> str:
    value = " ".join(text.strip().split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "..."


def _conversation_topics(conversation: dict[str, Any]) -> list[str]:
    metadata = conversation.get("metadata", {})
    topics = metadata.get("topics") if isinstance(metadata, dict) else None
    if not isinstance(topics, list):
        return []
    return _unique_strings([str(topic) for topic in topics if isinstance(topic, str)])


def _message_indexes(conversation: dict[str, Any]) -> list[int]:
    messages = conversation.get("messages", [])
    if not isinstance(messages, list):
        return []
    return list(range(len(messages)))


def _ensure_metadata(conversation: dict[str, Any]) -> dict[str, Any]:
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        conversation["metadata"] = metadata
    return metadata


def _with_generated_summary_metadata(conversation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(conversation, dict):
        return conversation
    enriched = dict(conversation)
    metadata = dict(enriched.get("metadata", {}))
    if not isinstance(metadata.get("generated_summary"), dict):
        summary = _get_conversation_summary(enriched)
        if isinstance(summary, dict):
            metadata["generated_summary"] = {
                "id": summary["id"],
                "type": summary["type"],
                "text": summary["text"],
                "basis": summary["basis"],
                "provenance_status": summary["provenance_status"],
                "generated_at": summary["generated_at"],
            }
    enriched["metadata"] = metadata
    return enriched


def _get_conversation_summary(conversation: dict[str, Any]) -> dict[str, Any] | None:
    metadata = conversation.get("metadata", {})
    summary_id = None
    if isinstance(metadata, dict):
        generated = metadata.get("generated_summary")
        if isinstance(generated, dict) and isinstance(generated.get("id"), str):
            summary_id = generated["id"]
    if summary_id is None:
        summary_id = _generated_summary_id(
            summary_type=SummaryType.CONVERSATION,
            target_id=str(conversation.get("id", "")),
            project_id=_project_id_from_conversation(conversation),
            filters={},
        )
    store = _runtime().metadata_store
    if hasattr(store, "get_generated_summary"):
        return store.get_generated_summary(summary_id)
    summaries = getattr(store, "_generated_summaries", {})
    if isinstance(summaries, dict):
        summary = summaries.get(summary_id)
        return summary if isinstance(summary, dict) else None
    return None


def _profile_summary_text(facts: list[dict[str, Any]]) -> str:
    if not facts:
        return "No active profile facts match the requested filters."
    lines: list[str] = []
    for fact in facts[:12]:
        predicate = str(fact.get("predicate", "fact"))
        value = str(fact.get("object_normalized") or fact.get("object", ""))
        if value:
            lines.append(f"{predicate}: {value}")
    remaining = len(facts) - len(lines)
    if remaining > 0:
        lines.append(f"{remaining} more active fact(s).")
    return "; ".join(lines)


def _freshest_fact_timestamp(facts: list[dict[str, Any]]) -> str | None:
    timestamps = [
        str(fact.get("last_confirmed_at") or fact.get("updated_at") or fact.get("created_at"))
        for fact in facts
        if fact.get("last_confirmed_at") or fact.get("updated_at") or fact.get("created_at")
    ]
    return max(timestamps) if timestamps else None


def _count_values(facts: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fact in facts:
        value = fact.get(key)
        if value is None:
            continue
        text = str(value)
        counts[text] = counts.get(text, 0) + 1
    return counts


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _source_message_indexes(fact: dict[str, Any]) -> list[int]:
    indexes = fact.get("source_message_indexes", [])
    if not isinstance(indexes, list):
        return []
    parsed: list[int] = []
    for index in indexes:
        try:
            parsed.append(int(index))
        except (TypeError, ValueError):
            continue
    return parsed


def _generated_summary_id(
    *,
    summary_type: str,
    target_id: str,
    project_id: str | None,
    filters: dict[str, Any],
) -> str:
    material = json_dumps(
        {
            "type": summary_type,
            "target_id": target_id,
            "project_id": project_id,
            "filters": {key: filters[key] for key in sorted(filters) if filters[key] is not None},
        }
    )
    return f"summary:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


def _store_generated_summary(summary: dict[str, Any]) -> None:
    store = _runtime().metadata_store
    if hasattr(store, "upsert_generated_summary"):
        store.upsert_generated_summary(summary)
        return
    summaries = getattr(store, "_generated_summaries", {})
    if not isinstance(summaries, dict):
        summaries = {}
    summaries[str(summary["id"])] = dict(summary)
    setattr(store, "_generated_summaries", summaries)


def _public_fact(fact: dict[str, Any]) -> dict[str, Any]:
    public = dict(fact)
    computed_source_quality = _source_quality_for_fact(public)
    if not public.get("source_quality") or computed_source_quality == "corrected_by_user":
        public["source_quality"] = computed_source_quality
    if not public.get("confidence_reason") or computed_source_quality == "corrected_by_user":
        public["confidence_reason"] = _confidence_reason_for_fact(public)
    if not public.get("last_confirmed_at"):
        public["last_confirmed_at"] = public.get("updated_at") or public.get("created_at")
    if not public.get("object_raw"):
        public["object_raw"] = public.get("object")
    if not public.get("object_normalized"):
        public["object_normalized"] = _normalized_fact_object(public)
    public.update(_fact_save_intent_fields(public))
    public.setdefault("superseded_at", None)
    return public


def _source_quality_for_fact(fact: dict[str, Any]) -> str:
    qualifiers = fact.get("qualifiers")
    source_role = qualifiers.get("source_role") if isinstance(qualifiers, dict) else None
    if isinstance(qualifiers, dict) and qualifiers.get("corrects") and source_role != "assistant":
        return "corrected_by_user"
    if fact.get("predicate") == "recurring_topic":
        return "inferred_from_conversation"
    if source_role == "assistant":
        return "assistant_statement"
    return "direct_user_statement"


def _confidence_reason_for_fact(fact: dict[str, Any]) -> str:
    source_quality = str(fact.get("source_quality") or _source_quality_for_fact(fact))
    if _fact_qualifier_value(fact, "save_intent") == "client_auto_save":
        return "Extracted from client auto-save memory, so confidence is reduced."
    if source_quality == "corrected_by_user":
        return "Extracted from a direct user correction."
    if source_quality == "direct_user_statement":
        return "Extracted from a direct user statement."
    if source_quality == "assistant_statement":
        return "Extracted from an assistant statement."
    if source_quality == "inferred_from_conversation":
        return "Inferred from recurring conversation topics."
    return "Extracted from stored conversation context."


def _confidence_reason_for_facts(facts: list[dict[str, Any]], basis: str) -> str:
    if basis == "conflict":
        return "Multiple active facts match the question but disagree."
    if not facts:
        return "No matching facts were used."
    reasons = _unique_strings([str(fact.get("confidence_reason", "")) for fact in facts])
    return "; ".join(reasons) if reasons else "Matching normalized facts were used."


def _normalized_fact_object(fact: dict[str, Any]) -> str:
    normalized, _ = _normalize_fact_object(
        predicate=str(fact.get("predicate", "")),
        object_value=str(fact.get("object", "")),
    )
    return normalized


def _normalize_fact_object(*, predicate: str, object_value: str) -> tuple[str, dict[str, Any]]:
    normalized = " ".join(object_value.strip().split())
    normalization: dict[str, Any] = {}

    normalized, spelling_corrections = _apply_common_spelling_corrections(normalized)
    if spelling_corrections:
        normalization["spelling_corrections"] = spelling_corrections

    normalized, date_qualifier = _normalize_fact_date(normalized)
    if date_qualifier:
        normalization["date"] = date_qualifier

    if _is_name_like_fact(predicate):
        cased = _title_case_name(normalized)
        if cased != normalized:
            normalization["casing"] = {
                "strategy": "title_case_name",
                "raw": normalized,
                "normalized": cased,
            }
            normalized = cased

    if normalization:
        normalization["object_raw"] = object_value
        normalization["object_normalized"] = normalized
    return normalized, normalization


def _apply_common_spelling_corrections(value: str) -> tuple[str, list[dict[str, str]]]:
    normalized = value
    corrections: list[dict[str, str]] = []
    for misspelled, corrected in _COMMON_FACT_SPELLING_CORRECTIONS.items():
        pattern = re.compile(rf"\b{misspelled}\b", re.IGNORECASE)
        if not pattern.search(normalized):
            continue
        normalized = pattern.sub(corrected, normalized)
        corrections.append({"raw": misspelled, "normalized": corrected})
    return normalized, corrections


def _normalize_fact_date(value: str) -> tuple[str, dict[str, str] | None]:
    month_pattern = re.compile(
        r"^(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)\s+"
        r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>\d{4}))?$",
        re.IGNORECASE,
    )
    month_match = month_pattern.match(value)
    if month_match:
        month = _MONTH_NAMES[month_match.group("month").lower()]
        day = int(month_match.group("day"))
        year = month_match.group("year")
        normalized = f"{month} {day}, {year}" if year else f"{month} {day}"
        if normalized != value:
            return normalized, {"raw": value, "normalized": normalized, "precision": "day"}
        return value, {"raw": value, "normalized": normalized, "precision": "day"}

    iso_pattern = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})$")
    iso_match = iso_pattern.match(value)
    if iso_match:
        month_number = int(iso_match.group("month"))
        if 1 <= month_number <= 12:
            month = list(_MONTH_NAMES.values())[month_number - 1]
            normalized = f"{month} {int(iso_match.group('day'))}, {iso_match.group('year')}"
            return normalized, {"raw": value, "normalized": normalized, "precision": "day"}
    return value, None


def _is_name_like_fact(predicate: str) -> bool:
    return predicate in _NAME_LIKE_PREDICATES


def _title_case_name(value: str) -> str:
    return " ".join(part[:1].upper() + part[1:].lower() for part in value.split())


def _fact_evidence(fact: dict[str, Any], *, used_in_answer: bool) -> dict[str, Any]:
    evidence = {
        "type": "fact",
        "fact_id": fact.get("id"),
        "subject": fact.get("subject"),
        "predicate": fact.get("predicate"),
        "object_raw": fact.get("object_raw", fact.get("object")),
        "object_normalized": fact.get("object_normalized", fact.get("object")),
        "confidence": fact.get("confidence"),
        "confidence_reason": fact.get("confidence_reason"),
        "source_quality": fact.get("source_quality"),
        "source_conversation_id": fact.get("source_conversation_id"),
        "source_message_indexes": fact.get("source_message_indexes", []),
        "created_at": fact.get("created_at"),
        "updated_at": fact.get("updated_at"),
        "last_confirmed_at": fact.get("last_confirmed_at"),
        "superseded_by": fact.get("superseded_by"),
        "superseded_at": fact.get("superseded_at"),
        "deleted_at": fact.get("deleted_at"),
        "used_in_answer": used_in_answer,
    }
    evidence.update(_fact_save_intent_fields(fact))
    return evidence


def _fact_citation(fact: dict[str, Any]) -> dict[str, Any]:
    citation = {
        "id": fact.get("source_conversation_id"),
        "fact_id": fact.get("id"),
        "predicate": fact.get("predicate"),
        "text": fact.get("object_normalized", fact.get("object")),
        "source_quality": fact.get("source_quality"),
        "confidence_reason": fact.get("confidence_reason"),
        "last_confirmed_at": fact.get("last_confirmed_at"),
    }
    citation.update(_fact_save_intent_fields(fact))
    return citation


def _provenance_from_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for fact in facts:
        conversation_id = str(fact.get("source_conversation_id", ""))
        item = grouped.setdefault(
            conversation_id,
            {
                "conversation_id": conversation_id,
                "source": None,
                "title": None,
                "stored_at": None,
                "matching_chunks": 0,
                "used_in_answer": True,
                "save_intents": [],
                "save_intent_sources": [],
            },
        )
        item["matching_chunks"] += 1
        save_intent = _fact_qualifier_value(fact, "save_intent")
        if save_intent and save_intent not in item["save_intents"]:
            item["save_intents"].append(save_intent)
        save_intent_source = _fact_qualifier_value(fact, "save_intent_source")
        if save_intent_source and save_intent_source not in item["save_intent_sources"]:
            item["save_intent_sources"].append(save_intent_source)
    return list(grouped.values())


def _apply_in_memory_fact_supersession(active: list[dict[str, Any]], new_fact: dict[str, Any]) -> None:
    corrects = str(new_fact.get("qualifiers", {}).get("corrects", ""))
    for fact in active:
        if fact.get("subject") != new_fact.get("subject") or fact.get("predicate") != new_fact.get("predicate"):
            continue
        if corrects and corrects.lower() in str(fact.get("object", "")).lower():
            now = _utc_now_iso()
            fact["superseded_by"] = new_fact["id"]
            fact["superseded_at"] = now
            fact["updated_at"] = now


def _dedupe_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    output: list[dict[str, Any]] = []
    for fact in facts:
        key = (str(fact["subject"]), str(fact["predicate"]), str(fact["object"]))
        if key not in seen:
            seen.add(key)
            output.append(fact)
    return output


def _owned_item_predicate(object_value: str) -> str:
    lowered = object_value.lower()
    if "guitar" in lowered or "gibson" in lowered:
        return "owns_guitar"
    return "owns_item"


def _owned_item_qualifiers(object_value: str) -> dict[str, Any]:
    qualifiers: dict[str, Any] = {}
    lowered = object_value.lower()
    if "guitar" in lowered or "gibson" in lowered:
        qualifiers["instrument"] = "guitar"
    if "p90" in lowered:
        qualifiers["pickup"] = "P90"
    for color in ("cherry", "tv yellow", "black", "white", "blue", "red"):
        if color in lowered:
            qualifiers["color"] = color
            break
    return qualifiers


def _clean_fact_object(value: str) -> str:
    return value.strip(" .?!\n\t\"'")


def _normalize_predicate_part(value: str) -> str:
    return "_".join(_query_tokens(value)) or "item"


def _project_subject(conversation: dict[str, Any]) -> str:
    title = str(conversation.get("title") or "").strip()
    return title or "project"


def runtime_health() -> dict[str, Any]:
    runtime = _runtime()
    metadata_health = (
        runtime.metadata_store.health()
        if hasattr(runtime.metadata_store, "health")
        else {}
    )
    vector_health = (
        runtime.vector_store.health() if hasattr(runtime.vector_store, "health") else {}
    )
    return {
        **runtime.health_state,
        "metadata_health": metadata_health,
        "vector_health": vector_health,
    }


def _embedding_readiness(
    *,
    cfg: HubConfig,
    embedding_provider: EmbeddingProvider,
    live_probe: bool,
) -> dict[str, Any]:
    provider = str(cfg.providers.embeddings)
    health: dict[str, Any] = {
        "provider": provider,
        "model": _active_embedding_model(cfg=cfg, embedding_provider=embedding_provider),
        "dimension": int(embedding_provider.dimension),
        "status": "ok",
        "live_probe": bool(live_probe),
    }
    if not live_probe:
        health["mode"] = "configuration"
        return health
    try:
        vectors = embedding_provider.embed_texts(["ai-memory-hub readiness probe"])
        first_vector = vectors[0] if vectors else []
        if len(vectors) != 1 or len(first_vector) != embedding_provider.dimension:
            health["status"] = "degraded"
            health["error_type"] = "VectorDimensionError"
        else:
            health["mode"] = "live"
    except Exception as exc:
        health["status"] = "degraded"
        health["error_type"] = type(exc).__name__
        health["error"] = redact_secrets(str(exc))
    return health


def _hash_to_vector(text: str, dimensions: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = list(digest)
    while len(values) < dimensions:
        digest = hashlib.sha256(digest).digest()
        values.extend(list(digest))
    return [(values[index] / 255.0) for index in range(dimensions)]


def _validate_metadata_schema(
    *, metadata_store: Any, supported_versions: Sequence[int]
) -> None:
    version = int(getattr(metadata_store, "schema_version", 0))
    if version not in supported_versions:
        supported = ",".join(str(item) for item in supported_versions)
        raise SchemaVersionError(
            f"Incompatible metadata schema version: {version}. Supported versions: [{supported}]"
        )


def _log_startup_policy(*, cfg: HubConfig, requested_vector_provider: str) -> None:
    logger.info(
        "Runtime startup policy",
        extra={
            "event": "runtime_startup_policy",
            "vector_provider": requested_vector_provider,
            "storage_profile": cfg.storage.profile,
            "vector_fallback_allowed": cfg.storage.vector.allow_fallback,
            "dry_run": cfg.storage.dry_run,
        },
    )
    if (
        cfg.storage.vector.allow_fallback
        and requested_vector_provider != VectorProviderName.MEMORY.value
        and requested_vector_provider not in _FALLBACK_POLICY_WARNED
    ):
        logger.warning(
            "storage.vector.allow_fallback=true for persistent vector provider %s; "
            "startup may fall back to in-memory vectors, making vector data non-durable",
            requested_vector_provider,
            extra={
                "event": "vector_fallback_policy_warning",
                "vector_provider": requested_vector_provider,
                "vector_fallback_allowed": True,
                "non_durable_fallback_possible": True,
            },
        )
        _FALLBACK_POLICY_WARNED.add(requested_vector_provider)
    production_warning_key = f"{cfg.storage.profile}:{requested_vector_provider}"
    if (
        cfg.storage.profile == "production"
        and cfg.storage.vector.allow_fallback
        and requested_vector_provider != VectorProviderName.MEMORY.value
        and production_warning_key not in _PRODUCTION_FALLBACK_POLICY_WARNED
    ):
        logger.warning(
            "storage.profile=production with storage.vector.allow_fallback=true for "
            "persistent vector provider %s; set allow_fallback=false to fail fast",
            requested_vector_provider,
            extra={
                "event": "production_vector_fallback_policy_warning",
                "storage_profile": cfg.storage.profile,
                "vector_provider": requested_vector_provider,
                "vector_fallback_allowed": True,
                "recommended_vector_fallback_allowed": False,
            },
        )
        _PRODUCTION_FALLBACK_POLICY_WARNED.add(production_warning_key)


def _embedding_index_metadata(
    *,
    cfg: HubConfig,
    embedding_provider: EmbeddingProvider,
    vector_provider: str,
    vector_store: Any,
    vector_health: dict[str, Any],
) -> dict[str, Any]:
    embedding_provider_name = str(cfg.providers.embeddings)
    embedding_model = _active_embedding_model(cfg=cfg, embedding_provider=embedding_provider)
    embedding_options = _embedding_options(cfg=cfg, embedding_provider_name=embedding_provider_name)
    vector_index = {
        "provider": vector_provider,
        "id": _vector_index_id(
            provider=vector_provider,
            vector_store=vector_store,
            vector_health=vector_health,
        ),
        "distance": _optional_string(vector_health.get("distance") or cfg.storage.vector.distance),
    }
    return {
        "embedding": {
            "provider": embedding_provider_name,
            "model": embedding_model,
            "dimension": int(embedding_provider.dimension),
            "options": embedding_options,
        },
        "vector_index": vector_index,
    }


def _validate_embedding_index_metadata(
    *,
    metadata_store: Any,
    vector_provider: str,
    vector_store: Any,
    vector_health: dict[str, Any],
    embedding_index_metadata: dict[str, Any],
) -> bool:
    if vector_provider == VectorProviderName.MEMORY.value:
        return False
    if not hasattr(metadata_store, "get_runtime_metadata") or not hasattr(
        metadata_store, "set_runtime_metadata"
    ):
        return False
    key = _vector_compatibility_metadata_key(embedding_index_metadata["vector_index"])
    existing = metadata_store.get_runtime_metadata(key)
    if existing == embedding_index_metadata:
        return False
    rows = _vector_row_count(vector_store=vector_store, vector_health=vector_health)
    if existing is not None and rows != 0:
        raise RuntimeError(
            "Embedding index metadata mismatch: persistent vector index was built "
            "with a different embedding provider, model, dimension, or options. "
            "Reindex the vector data or use a separate vector namespace/index."
        )
    metadata_store.set_runtime_metadata(key, embedding_index_metadata)
    return existing is not None


def _active_embedding_model(*, cfg: HubConfig, embedding_provider: EmbeddingProvider) -> str:
    model = getattr(embedding_provider, "embedding_model", None)
    if isinstance(model, str) and model.strip():
        return model.strip()
    if cfg.providers.embeddings == "local":
        return "local-deterministic-hash"
    return str(cfg.providers.embedding_model)


def _embedding_options(*, cfg: HubConfig, embedding_provider_name: str) -> dict[str, Any]:
    if embedding_provider_name == "openai":
        return {"base_url": redact_secrets(cfg.openai.base_url)}
    return {}


def _vector_index_id(*, provider: str, vector_store: Any, vector_health: dict[str, Any]) -> str:
    candidates = {
        "provider": provider,
        "db_path": _optional_string(getattr(vector_store, "db_path", None)),
        "table_name": _optional_string(getattr(vector_store, "table_name", None)),
        "collection": _optional_string(
            vector_health.get("collection") or getattr(vector_store, "collection_name", None)
        ),
        "index": _optional_string(vector_health.get("index") or getattr(vector_store, "index_name", None)),
        "namespace": _optional_string(
            vector_health.get("namespace") or getattr(vector_store, "namespace", None)
        ),
        "schema": _optional_string(vector_health.get("schema") or getattr(vector_store, "schema", None)),
    }
    material = {key: value for key, value in candidates.items() if value}
    return "sha256:" + hashlib.sha256(json_dumps(material).encode("utf-8")).hexdigest()


def _vector_compatibility_metadata_key(vector_index: dict[str, Any]) -> str:
    return _VECTOR_COMPATIBILITY_METADATA_PREFIX + str(vector_index["id"])


def _vector_row_count(*, vector_store: Any, vector_health: dict[str, Any]) -> int | None:
    rows = vector_health.get("rows")
    if rows is None:
        stats = vector_health.get("stats")
        if isinstance(stats, dict):
            rows = stats.get("rows")
    if rows is None and hasattr(vector_store, "get_stats"):
        stats = vector_store.get_stats()
        if isinstance(stats, dict):
            rows = stats.get("rows")
    if rows is None:
        return None
    try:
        return int(rows)
    except (TypeError, ValueError):
        return None


def _validate_vector_dimension(*, embedding_dimension: int, vector_store: Any) -> None:
    expected = int(getattr(vector_store, "expected_dimensionality", 0))
    if expected != embedding_dimension:
        raise VectorDimensionError(
            f"Vector store dimensionality mismatch at startup: expected {embedding_dimension}, got {expected}"
        )
