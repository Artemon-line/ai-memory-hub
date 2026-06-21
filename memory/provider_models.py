from __future__ import annotations

from enum import StrEnum


class EmbeddingProviderName(StrEnum):
    OPENAI = "openai"
    LOCAL = "local"


class VectorProviderName(StrEnum):
    LANCEDB = "lancedb"
    PGVECTOR = "pgvector"
    MEMORY = "memory"
    CHROMADB = "chromadb"
    QDRANT = "qdrant"
    MONGODB_ATLAS = "mongodb_atlas"


class VectorProviderAlias(StrEnum):
    IN_MEMORY = "in_memory"


class MetadataProviderName(StrEnum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"
    MONGODB = "mongodb"


class ProviderConfigSection(StrEnum):
    VECTOR_PROVIDERS = "vector_providers"
    METADATA_PROVIDERS = "metadata_providers"


class VectorProviderConfigKey(StrEnum):
    CHROMADB = "chromadb"
    QDRANT = "qdrant"
    MILVUS = "milvus"
    WEAVIATE = "weaviate"
    MONGODB_ATLAS = "mongodb_atlas"
    ELASTICSEARCH = "elasticsearch"
    OPENSEARCH = "opensearch"


class MetadataProviderConfigKey(StrEnum):
    MONGODB = "mongodb"


class SecretConfigKey(StrEnum):
    API_KEY = "api_key"
    TOKEN = "token"
    URI = "uri"
    USERNAME = "username"
    PASSWORD = "password"


class VectorPayloadKey(StrEnum):
    MEMORY_ID = "memory_id"
    PROJECT_ID = "project_id"
    OWNER_ID = "owner_id"
    CHUNK_ID = "chunk_id"
    CHUNK_INDEX = "chunk_index"
    MESSAGE_HASH = "message_hash"
    ROLE = "role"
    TEXT = "text"
    VECTOR = "vector"
    SCORE = "score"


class VectorStatsKey(StrEnum):
    PROVIDER = "provider"
    ROWS = "rows"
    EXPECTED_DIMENSIONALITY = "expected_dimensionality"
    COLLECTION = "collection"
    MODE = "mode"
    STATS = "stats"


class ChromaQueryKey(StrEnum):
    IDS = "ids"
    METADATAS = "metadatas"
    DOCUMENTS = "documents"
    DISTANCES = "distances"


class ChromaClientMode(StrEnum):
    HTTP = "http"
    PERSISTENT = "persistent"


class QdrantDistanceName(StrEnum):
    COSINE = "Cosine"
    L2 = "Euclid"
    INNER_PRODUCT = "Dot"


class MongoCollectionName(StrEnum):
    CONVERSATIONS = "conversations"
    FACTS = "facts"
    GENERATED_SUMMARIES = "generated_summaries"
