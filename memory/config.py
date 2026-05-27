from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, ConfigDict

logger = logging.getLogger(__name__)


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    embeddings: str = "openai"
    embedding_model: str = "nomic-embed-text"
    embedding_dimension: int = 768
    inference: str = "openai"
    inference_model: str = "llama3"
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
        if v not in {"lancedb", "in_memory"}:
            raise ValueError("providers.vector_db must be one of: lancedb, in_memory")
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


class StorageConfig(BaseModel):
    dry_run: bool = False
    metadata_schema_versions: tuple[int, ...] = (1,)
    vector: StorageVectorConfig = Field(default_factory=StorageVectorConfig)


class OpenAIConfig(BaseModel):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "dummy_key"
    model: str = "gpt-4.1"


class MCPConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765


class APIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    auth: str = "none"
    api_key: str = ""


class HubConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    interfaces: InterfacesConfig = Field(default_factory=InterfacesConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    # Use schema_config as the internal name to avoid collision with BaseModel.schema
    schema_config: SchemaConfig = Field(default_factory=SchemaConfig, alias="schema")
    storage: StorageConfig = Field(default_factory=StorageConfig)
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
