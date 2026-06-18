from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
