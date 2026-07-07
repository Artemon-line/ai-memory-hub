from __future__ import annotations

import logging
import os
import re
import secrets
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from memory.provider_models import (
    EmbeddingProviderName,
    MetadataProviderConfigKey,
    MetadataProviderName,
    ProviderConfigSection,
    VectorProviderAlias,
    VectorProviderConfigKey,
    VectorProviderName,
)

logger = logging.getLogger(__name__)


VECTOR_PROVIDER_VALUES = tuple(item.value for item in VectorProviderName)
METADATA_PROVIDER_VALUES = tuple(item.value for item in MetadataProviderName)
_VALID_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_VALID_INDEX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_VALID_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InsertPolicy(StrEnum):
    PERMISSIVE = "permissive"
    REQUIRE_SAVE_INTENT = "require_save_intent"
    REVIEW_PENDING = "review_pending"


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    embeddings: str = EmbeddingProviderName.OPENAI.value
    embedding_model: str = "nomic-embed-text"
    embedding_dimension: int = 768
    vector_db: str = VectorProviderName.LANCEDB.value
    metadata_db: str = MetadataProviderName.SQLITE.value
    agent: str = "mvp"

    @field_validator("embeddings")
    @classmethod
    def validate_embeddings(cls, v: str) -> str:
        v = v.lower()
        if v not in {item.value for item in EmbeddingProviderName}:
            raise ValueError("providers.embeddings must be one of: openai, local")
        return v

    @field_validator("vector_db")
    @classmethod
    def validate_vector_db(cls, v: str) -> str:
        v = v.lower()
        if v == VectorProviderAlias.IN_MEMORY.value:
            v = VectorProviderName.MEMORY.value
        if v not in VECTOR_PROVIDER_VALUES:
            raise ValueError(
                "providers.vector_db must be one of: "
                + ", ".join(VECTOR_PROVIDER_VALUES)
            )
        return v

    @field_validator("metadata_db")
    @classmethod
    def validate_metadata_db(cls, v: str) -> str:
        v = v.lower()
        if v not in METADATA_PROVIDER_VALUES:
            raise ValueError(
                "providers.metadata_db must be one of: "
                + ", ".join(METADATA_PROVIDER_VALUES)
            )
        return v


class InterfacesConfig(BaseModel):
    mcp: bool = True
    api: bool = True


class PathsConfig(BaseModel):
    data_dir: str = "./data"
    logs_dir: str = "./logs"


class SchemaConfig(BaseModel):
    file: str = "./memory/schema/conversation.schema.json"


class StorageVectorConfig(BaseModel):
    allow_fallback: bool = True
    distance: str = "cosine"

    @field_validator("distance")
    @classmethod
    def validate_distance(cls, v: str) -> str:
        v = v.lower()
        if v not in {"cosine", "l2", "inner_product"}:
            raise ValueError("storage.vector.distance must be one of: cosine, l2, inner_product")
        return v


class ChromaDBVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = "./data/chromadb"
    url: str = ""
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    collection: str = "memory_vectors"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.vector.chromadb.url")
        return value.rstrip("/")

    @field_validator("collection")
    @classmethod
    def validate_collection(cls, value: str) -> str:
        return _validate_provider_name(
            value, field_name="storage.vector.chromadb.collection"
        )


class QdrantVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "http://127.0.0.1:6333"
    api_key: str = ""
    collection: str = "memory_vectors"
    prefer_grpc: bool = False

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.vector.qdrant.url")
        return value.rstrip("/")

    @field_validator("collection")
    @classmethod
    def validate_collection(cls, value: str) -> str:
        return _validate_provider_name(
            value, field_name="storage.vector.qdrant.collection"
        )


class MilvusVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str = "http://127.0.0.1:19530"
    token: str = ""
    collection: str = "memory_vectors"

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.vector.milvus.uri")
        return value.rstrip("/")

    @field_validator("collection")
    @classmethod
    def validate_collection(cls, value: str) -> str:
        return _validate_provider_name(
            value, field_name="storage.vector.milvus.collection"
        )


class WeaviateVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "http://127.0.0.1:8080"
    api_key: str = ""
    collection: str = "MemoryVector"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.vector.weaviate.url")
        return value.rstrip("/")

    @field_validator("collection")
    @classmethod
    def validate_collection(cls, value: str) -> str:
        return _validate_provider_name(
            value, field_name="storage.vector.weaviate.collection"
        )


class MongoDBAtlasVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str = ""
    database: str = "ai_memory_hub"
    collection: str = "memory_vectors"
    index: str = "memory_vector_index"

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.vector.mongodb_atlas.uri")
        return value

    @field_validator("database", "collection")
    @classmethod
    def validate_name(cls, value: str, info: Any) -> str:
        return _validate_provider_name(
            value, field_name=f"storage.vector.mongodb_atlas.{info.field_name}"
        )

    @field_validator("index")
    @classmethod
    def validate_index(cls, value: str) -> str:
        return _validate_provider_index(
            value, field_name="storage.vector.mongodb_atlas.index"
        )


class SearchVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "http://127.0.0.1:9200"
    username: str = ""
    password: str = ""
    index: str = "memory_vectors"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.vector.search.url")
        return value.rstrip("/")

    @field_validator("index")
    @classmethod
    def validate_index(cls, value: str) -> str:
        return _validate_provider_index(value, field_name="storage.vector.search.index")


class RedisVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "redis://127.0.0.1:6379/0"
    index: str = "memory_vectors"
    key_prefix: str = "memory_vectors:"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.vector.redis.url")
        return value

    @field_validator("index")
    @classmethod
    def validate_index(cls, value: str) -> str:
        return _validate_provider_index(value, field_name="storage.vector.redis.index")

    @field_validator("key_prefix")
    @classmethod
    def validate_key_prefix(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("storage.vector.redis.key_prefix must not be empty")
        return normalized


class PGVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = ""
    table_name: str = "memory_vectors"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.vector.pgvector.url")
        return value

    @field_validator("table_name")
    @classmethod
    def validate_table_name(cls, value: str) -> str:
        return _validate_sql_identifier(
            value, field_name="storage.vector.pgvector.table_name"
        )


class PineconeVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = ""
    index: str = "memory-vectors"
    namespace: str = "default"
    cloud: str = "aws"
    region: str = "us-east-1"
    create_index: bool = False

    @field_validator("index")
    @classmethod
    def validate_index(cls, value: str) -> str:
        return _validate_provider_index(value, field_name="storage.vector.pinecone.index")

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("storage.vector.pinecone.namespace must not be empty")
        return normalized

    @field_validator("cloud", "region")
    @classmethod
    def validate_name(cls, value: str, info: Any) -> str:
        return _validate_provider_name(
            value, field_name=f"storage.vector.pinecone.{info.field_name}"
        )


class TurbopufferVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str = ""
    namespace: str = "memory-vectors"
    region: str = "gcp-us-central1"

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("storage.vector.turbopuffer.namespace must not be empty")
        return _validate_provider_index(
            normalized, field_name="storage.vector.turbopuffer.namespace"
        )

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        return _validate_provider_name(
            value, field_name="storage.vector.turbopuffer.region"
        )


class VespaVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "http://127.0.0.1:8080"
    token: str = ""
    namespace: str = "memory"
    schema_name: str = Field(default="memory_vector", alias="schema")
    rank_profile: str = "vector_similarity"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.vector.vespa.url")
        return value.rstrip("/")

    @field_validator("namespace", "schema_name", "rank_profile")
    @classmethod
    def validate_names(cls, value: str, info: Any) -> str:
        field_name = "schema" if info.field_name == "schema_name" else info.field_name
        return _validate_provider_name(
            value, field_name=f"storage.vector.vespa.{field_name}"
        )


class TypesenseVectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = "http://127.0.0.1:8108"
    api_key: str = ""
    collection: str = "memory_vectors"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.vector.typesense.url")
        return value.rstrip("/")

    @field_validator("collection")
    @classmethod
    def validate_collection(cls, value: str) -> str:
        return _validate_provider_index(
            value, field_name="storage.vector.typesense.collection"
        )


class VectorProviderConfigs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pgvector: PGVectorConfig = Field(
        default_factory=PGVectorConfig,
        alias=VectorProviderConfigKey.PGVECTOR.value,
    )
    chromadb: ChromaDBVectorConfig = Field(
        default_factory=ChromaDBVectorConfig,
        alias=VectorProviderConfigKey.CHROMADB.value,
    )
    qdrant: QdrantVectorConfig = Field(
        default_factory=QdrantVectorConfig,
        alias=VectorProviderConfigKey.QDRANT.value,
    )
    milvus: MilvusVectorConfig = Field(
        default_factory=MilvusVectorConfig,
        alias=VectorProviderConfigKey.MILVUS.value,
    )
    weaviate: WeaviateVectorConfig = Field(
        default_factory=WeaviateVectorConfig,
        alias=VectorProviderConfigKey.WEAVIATE.value,
    )
    mongodb_atlas: MongoDBAtlasVectorConfig = Field(
        default_factory=MongoDBAtlasVectorConfig,
        alias=VectorProviderConfigKey.MONGODB_ATLAS.value,
    )
    elasticsearch: SearchVectorConfig = Field(
        default_factory=SearchVectorConfig,
        alias=VectorProviderConfigKey.ELASTICSEARCH.value,
    )
    opensearch: SearchVectorConfig = Field(
        default_factory=SearchVectorConfig,
        alias=VectorProviderConfigKey.OPENSEARCH.value,
    )
    redis: RedisVectorConfig = Field(
        default_factory=RedisVectorConfig,
        alias=VectorProviderConfigKey.REDIS.value,
    )
    pinecone: PineconeVectorConfig = Field(
        default_factory=PineconeVectorConfig,
        alias=VectorProviderConfigKey.PINECONE.value,
    )
    turbopuffer: TurbopufferVectorConfig = Field(
        default_factory=TurbopufferVectorConfig,
        alias=VectorProviderConfigKey.TURBOPUFFER.value,
    )
    vespa: VespaVectorConfig = Field(
        default_factory=VespaVectorConfig,
        alias=VectorProviderConfigKey.VESPA.value,
    )
    typesense: TypesenseVectorConfig = Field(
        default_factory=TypesenseVectorConfig,
        alias=VectorProviderConfigKey.TYPESENSE.value,
    )


class MongoDBMetadataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uri: str = ""
    database: str = "ai_memory_hub"
    conversations_collection: str = "conversations"
    facts_collection: str = "facts"
    generated_summaries_collection: str = "generated_summaries"
    schema_collection: str = "schema_versions"

    @field_validator("uri")
    @classmethod
    def validate_uri(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.metadata.mongodb.uri")
        return value

    @field_validator(
        "database",
        "conversations_collection",
        "facts_collection",
        "generated_summaries_collection",
        "schema_collection",
    )
    @classmethod
    def validate_name(cls, value: str, info: Any) -> str:
        return _validate_provider_name(
            value, field_name=f"storage.metadata.mongodb.{info.field_name}"
        )


class PostgresMetadataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = ""

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="storage.metadata.postgres.url")
        return value


class MetadataProviderConfigs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    postgres: PostgresMetadataConfig = Field(
        default_factory=PostgresMetadataConfig,
        alias=MetadataProviderConfigKey.POSTGRES.value,
    )
    mongodb: MongoDBMetadataConfig = Field(
        default_factory=MongoDBMetadataConfig,
        alias=MetadataProviderConfigKey.MONGODB.value,
    )


class StorageConfig(BaseModel):
    profile: str = "local"
    dry_run: bool = False
    allow_trusted_appends: bool = False
    metadata_schema_versions: tuple[int, ...] = (1,)
    vector: StorageVectorConfig = Field(default_factory=StorageVectorConfig)
    vector_providers: VectorProviderConfigs = Field(
        default_factory=VectorProviderConfigs,
        alias=ProviderConfigSection.VECTOR_PROVIDERS.value,
    )
    metadata_providers: MetadataProviderConfigs = Field(
        default_factory=MetadataProviderConfigs,
        alias=ProviderConfigSection.METADATA_PROVIDERS.value,
    )

    @field_validator("profile")
    @classmethod
    def validate_profile(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"local", "development", "test", "production"}:
            raise ValueError(
                "storage.profile must be one of: local, development, test, production"
            )
        return normalized

    @model_validator(mode="before")
    @classmethod
    def apply_profile_defaults(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        profile = str(data.get("profile", "local")).lower()
        vector = data.get("vector")
        if profile != "production":
            return data
        if not isinstance(vector, dict):
            data["vector"] = {"allow_fallback": False}
            return data
        if "allow_fallback" not in vector:
            data["vector"] = {**vector, "allow_fallback": False}
        return data


class TokenizerConfig(BaseModel):
    enabled: bool = False
    encoding: str = "cl100k_base"


class AskConfig(BaseModel):
    max_context_tokens: int = Field(default=2000, ge=1)


class RetrievalConfig(BaseModel):
    vector_score_threshold: float = Field(default=7.5, ge=0)
    keyword_enabled: bool = True
    keyword_candidate_limit: int = Field(default=50, ge=1, le=500)
    keyword_weight: float = Field(default=0.25, ge=0)
    metadata_weight: float = Field(default=0.15, ge=0)
    candidate_multiplier: int = Field(default=3, ge=1, le=20)


class MemoryConfig(BaseModel):
    insert_policy: str = InsertPolicy.PERMISSIVE.value

    @field_validator("insert_policy")
    @classmethod
    def validate_insert_policy(cls, value: str) -> str:
        normalized = value.lower()
        allowed = {item.value for item in InsertPolicy}
        if normalized not in allowed:
            raise ValueError(
                "memory.insert_policy must be one of: " + ", ".join(sorted(allowed))
            )
        return normalized


class ChunkingConfig(BaseModel):
    strategy: str = "message"
    max_tokens: int = Field(default=800, ge=1)
    overlap_tokens: int = Field(default=80, ge=0)

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        value = v.lower()
        if value not in {"message", "token"}:
            raise ValueError("chunking.strategy must be one of: message, token")
        return value

    @model_validator(mode="after")
    def validate_overlap(self) -> "ChunkingConfig":
        if self.overlap_tokens >= self.max_tokens:
            raise ValueError("chunking.overlap_tokens must be less than chunking.max_tokens")
        return self


class OpenAIConfig(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "dummy_key"
    model: str = "gpt-4.1"


class MCPConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    list_page_size: int | None = Field(default=100, ge=1)


class APITokenConfig(BaseModel):
    hash_secret_env: str = "AMH_TOKEN_HASH_SECRET"
    default_expiry_days: int = Field(default=365, ge=1)


class OAuthConfig(BaseModel):
    authorization_servers: list[str] = Field(default_factory=list)
    resource: str = ""
    issuer: str = ""
    audience: str = ""
    jwt_secret: str = ""
    jwt_secret_env: str = "AMH_OAUTH_JWT_SECRET"
    scopes_supported: list[str] = Field(
        default_factory=lambda: ["memory:read", "memory:write", "memory:admin"]
    )

    @field_validator("authorization_servers")
    @classmethod
    def validate_authorization_servers(cls, values: list[str]) -> list[str]:
        for value in values:
            _validate_absolute_uri(value, field_name="api.oauth.authorization_servers")
        return values

    @field_validator("resource")
    @classmethod
    def validate_resource(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="api.oauth.resource")
        return value

    @field_validator("scopes_supported")
    @classmethod
    def validate_scopes_supported(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if not normalized:
            raise ValueError("api.oauth.scopes_supported must not be empty")
        return normalized


class APIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    auth: str = "none"
    public_base_url: str = ""
    cors_allow_origins: list[str] = Field(default_factory=list)
    token: APITokenConfig = Field(default_factory=APITokenConfig)
    oauth: OAuthConfig = Field(default_factory=OAuthConfig)

    @field_validator("auth")
    @classmethod
    def validate_auth(cls, v: str) -> str:
        value = v.lower()
        if value not in {"none", "bearer_token", "oauth_resource_server"}:
            raise ValueError(
                "api.auth must be one of: none, bearer_token, oauth_resource_server"
            )
        return value

    @field_validator("public_base_url")
    @classmethod
    def validate_public_base_url(cls, value: str) -> str:
        if value:
            _validate_absolute_uri(value, field_name="api.public_base_url")
        return value.rstrip("/")

    @field_validator("cors_allow_origins")
    @classmethod
    def validate_cors_allow_origins(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            origin = str(value).strip().rstrip("/")
            if not origin:
                continue
            _validate_cors_origin(origin)
            if origin not in normalized:
                normalized.append(origin)
        return normalized

    @model_validator(mode="after")
    def validate_auth_settings(self) -> "APIConfig":
        if self.auth == "none" and self.host not in {"127.0.0.1", "localhost", "::1"}:
            logger.warning("api.auth=none is configured with non-loopback host %s", self.host)
        if self.auth == "oauth_resource_server":
            if not self.public_base_url:
                raise ValueError("api.public_base_url is required for oauth_resource_server")
            if not self.oauth.authorization_servers:
                raise ValueError(
                    "api.oauth.authorization_servers is required for oauth_resource_server"
                )
            if not self.oauth.jwt_secret and not self.oauth.jwt_secret_env:
                raise ValueError(
                    "api.oauth.jwt_secret or api.oauth.jwt_secret_env is required for "
                    "oauth_resource_server"
                )
        return self


class LoggingConfig(BaseModel):
    enabled: bool = True
    format: str = "text"
    level: str = "INFO"
    access_logs: bool = True
    request_id_header: str = "x-request-id"
    include_stack_traces: bool = True

    @field_validator("format")
    @classmethod
    def validate_format(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"text", "json"}:
            raise ValueError("observability.logging.format must be one of: text, json")
        return normalized

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(
                "observability.logging.level must be one of: "
                "DEBUG, INFO, WARNING, ERROR, CRITICAL"
            )
        return normalized

    @field_validator("request_id_header")
    @classmethod
    def validate_request_id_header(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("observability.logging.request_id_header must not be empty")
        return normalized


class TracingConfig(BaseModel):
    enabled: bool = False
    endpoint: str = "http://otel-collector:4317"
    protocol: str = "grpc"
    sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    service_name: str = "ai-memory-hub"
    environment: str = "local"

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("observability.tracing.endpoint must not be empty")
        _validate_absolute_uri(normalized, field_name="observability.tracing.endpoint")
        return normalized.rstrip("/")

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, value: str) -> str:
        normalized = value.lower()
        if normalized != "grpc":
            raise ValueError("observability.tracing.protocol must be grpc")
        return normalized

    @field_validator("service_name", "environment")
    @classmethod
    def validate_non_empty_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("observability.tracing labels must not be empty")
        return normalized


class MetricsConfig(BaseModel):
    enabled: bool = False
    endpoint: str = "http://otel-collector:4317"
    protocol: str = "grpc"

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("observability.metrics.endpoint must not be empty")
        _validate_absolute_uri(normalized, field_name="observability.metrics.endpoint")
        return normalized.rstrip("/")

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, value: str) -> str:
        normalized = value.lower()
        if normalized != "grpc":
            raise ValueError("observability.metrics.protocol must be grpc")
        return normalized


class ObservabilityConfig(BaseModel):
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    debug_payloads: bool = False
    embedding_readiness_probe: bool = False


class HubConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    interfaces: InterfacesConfig = Field(default_factory=InterfacesConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    # Use schema_config as the internal name to avoid collision with BaseModel.schema
    schema_config: SchemaConfig = Field(default_factory=SchemaConfig, alias="schema")
    storage: StorageConfig = Field(default_factory=StorageConfig)
    tokenizer: TokenizerConfig = Field(default_factory=TokenizerConfig)
    ask: AskConfig = Field(default_factory=AskConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)


def parse_config(config_dict: dict[str, Any] | None) -> HubConfig:
    if config_dict is None:
        return HubConfig()
    return HubConfig.model_validate(config_dict)


def load_config(path: str | Path | None = None) -> HubConfig:
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config.yaml"

    config_path = Path(path)
    if not config_path.exists():
        if path == Path(__file__).resolve().parent.parent / "config.yaml":
            logger.warning(f"Config file not found at {config_path}, using defaults")
            return HubConfig()
        raise FileNotFoundError(f"Config file not found: {config_path}")

    text = config_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}

    return parse_config(raw)


def normalize_config(config: HubConfig | dict[str, Any] | None) -> HubConfig:
    if isinstance(config, HubConfig):
        return config
    if isinstance(config, dict):
        return parse_config(config)
    return load_config()


def ensure_token_hash_secret(config: HubConfig) -> str | None:
    if config.api.auth != "bearer_token":
        return None
    env_name = config.api.token.hash_secret_env
    existing = os.environ.get(env_name)
    if existing:
        return existing
    secret_path = Path(config.paths.data_dir) / ".token_hash_secret"
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    if secret_path.exists():
        secret = secret_path.read_text(encoding="utf-8").strip()
    else:
        secret = secrets.token_urlsafe(48)
        secret_path.write_text(secret + "\n", encoding="utf-8")
    os.environ[env_name] = secret
    return secret


def _validate_absolute_uri(value: str, *, field_name: str) -> None:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute URI")
    if parsed.fragment:
        raise ValueError(f"{field_name} must not include a fragment")


def _validate_cors_origin(value: str) -> None:
    if value == "*":
        raise ValueError("api.cors_allow_origins must list explicit origins, not '*'")
    parsed = urlparse(value)
    if parsed.fragment or parsed.query or parsed.path not in {"", "/"}:
        raise ValueError("api.cors_allow_origins entries must be origins only")
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return
    if parsed.scheme in {"chrome-extension", "moz-extension"} and parsed.netloc:
        return
    raise ValueError(
        "api.cors_allow_origins entries must be http(s), chrome-extension, "
        "or moz-extension origins"
    )


def _validate_provider_name(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if not _VALID_NAME_RE.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must start with a letter and contain only letters, "
            "numbers, underscores, dashes, or dots"
        )
    return normalized


def _validate_provider_index(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if not _VALID_INDEX_RE.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must start with a letter and contain only letters, "
            "numbers, underscores, dashes, or dots"
        )
    return normalized


def _validate_sql_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if not _VALID_SQL_IDENTIFIER_RE.fullmatch(normalized):
        raise ValueError(
            f"{field_name} must start with a letter or underscore and contain only "
            "letters, numbers, or underscores"
        )
    return normalized
