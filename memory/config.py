from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    embeddings: str = "openai"
    embedding_model: str = "nomic-embed-text"
    embedding_dimension: int = 768
    vector_db: str = "lancedb"
    metadata_db: str = "sqlite"
    metadata_dsn: str = ""

    @field_validator("embeddings")
    @classmethod
    def validate_embeddings(cls, v: str) -> str:
        v = v.lower()
        if v not in {"openai", "local"}:
            raise ValueError("providers.embeddings must be one of: openai, local")
        return v

    @field_validator("vector_db")
    @classmethod
    def validate_vector_db(cls, v: str) -> str:
        v = v.lower()
        if v == "in_memory":
            v = "memory"
        if v not in {"lancedb", "pgvector", "memory"}:
            raise ValueError("providers.vector_db must be one of: lancedb, pgvector, memory")
        return v

    @field_validator("metadata_db")
    @classmethod
    def validate_metadata_db(cls, v: str) -> str:
        v = v.lower()
        if v not in {"sqlite", "postgres"}:
            raise ValueError("providers.metadata_db must be one of: sqlite, postgres")
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


class StorageConfig(BaseModel):
    dry_run: bool = False
    allow_trusted_appends: bool = False
    metadata_schema_versions: tuple[int, ...] = (1,)
    vector: StorageVectorConfig = Field(default_factory=StorageVectorConfig)


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


class APIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    auth: str = "none"
    api_key: str = ""

    @field_validator("auth")
    @classmethod
    def validate_auth(cls, v: str) -> str:
        value = v.lower()
        if value not in {"none", "bearer_token", "oauth_resource_server"}:
            raise ValueError(
                "api.auth must be one of: none, bearer_token, oauth_resource_server"
            )
        return value


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
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    api: APIConfig = Field(default_factory=APIConfig)


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
