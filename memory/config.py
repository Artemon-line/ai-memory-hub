from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProvidersConfig:
    embeddings: str = "local"
    embedding_model: str = ""
    embedding_dimention: int = 1536
    vector_db: str = "lancedb"
    metadata_db: str = "sqlite"
    metadata_dsn: str = ""


@dataclass(frozen=True)
class InterfacesConfig:
    mcp: bool = True
    api: bool = True


@dataclass(frozen=True)
class PathsConfig:
    data_dir: str = "./data"


@dataclass(frozen=True)
class SchemaConfig:
    file: str = "./memory/schema/conversation.schema.json"


@dataclass(frozen=True)
class StorageVectorConfig:
    allow_fallback: bool = True


@dataclass(frozen=True)
class StorageConfig:
    dry_run: bool = False
    metadata_schema_versions: tuple[int, ...] = (1,)
    vector: StorageVectorConfig = StorageVectorConfig()


@dataclass(frozen=True)
class OpenAIConfig:
    base_url: str = "https://api.openai.com"
    api_key: str = "dummy_key"
    model: str = "gpt-4.1"


@dataclass(frozen=True)
class HubConfig:
    providers: ProvidersConfig
    interfaces: InterfacesConfig
    paths: PathsConfig
    schema: SchemaConfig
    storage: StorageConfig
    openai: OpenAIConfig


DEFAULT_CONFIG = HubConfig(
    providers=ProvidersConfig(),
    interfaces=InterfacesConfig(),
    paths=PathsConfig(),
    schema=SchemaConfig(),
    storage=StorageConfig(),
    openai=OpenAIConfig(),
)


def _as_dict(config: dict[str, Any] | None) -> dict[str, Any]:
    return config if isinstance(config, dict) else {}


def _parse_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    return default


def parse_config(config: dict[str, Any] | None) -> HubConfig:
    config_dict = _as_dict(config)
    providers = _as_dict(config_dict.get("providers"))
    interfaces = _as_dict(config_dict.get("interfaces"))
    paths = _as_dict(config_dict.get("paths"))
    schema = _as_dict(config_dict.get("schema"))
    storage = _as_dict(config_dict.get("storage"))
    storage_vector = _as_dict(storage.get("vector"))
    openai = _as_dict(config_dict.get("openai"))

    open_ai_base_url = openai.get("base_url", DEFAULT_CONFIG.openai.base_url)
    open_ai_key = openai.get("api_key", DEFAULT_CONFIG.openai.api_key)
    open_ai_model = openai.get("model", DEFAULT_CONFIG.openai.model)

    embeddings = str(
        providers.get("embeddings", DEFAULT_CONFIG.providers.embeddings)
    ).lower()
    embedding_model = str(
        providers.get("embedding_model", DEFAULT_CONFIG.providers.embedding_model)
    ).lower()
    embedding_dimention = int(
        providers.get(
            "embedding_dimention", DEFAULT_CONFIG.providers.embedding_dimention
        )
    )

    vector_db = str(
        providers.get("vector_db", DEFAULT_CONFIG.providers.vector_db)
    ).lower()
    metadata_db = str(
        providers.get("metadata_db", DEFAULT_CONFIG.providers.metadata_db)
    ).lower()
    metadata_dsn = str(
        providers.get("metadata_dsn", DEFAULT_CONFIG.providers.metadata_dsn)
    )
    if embeddings not in {"openai", "local"}:
        raise ValueError("providers.embeddings must be one of: openai, local")
    if vector_db not in {"lancedb", "in_memory"}:
        raise ValueError("providers.vector_db must be one of: lancedb, in_memory")
    if metadata_db not in {"sqlite", "postgres"}:
        raise ValueError("providers.metadata_db must be one of: sqlite, postgres")

    return HubConfig(
        openai=OpenAIConfig(
            base_url=open_ai_base_url, api_key=open_ai_key, model=open_ai_model
        ),
        providers=ProvidersConfig(
            embeddings=embeddings,
            embedding_model=embedding_model,
            embedding_dimention=embedding_dimention,
            vector_db=vector_db,
            metadata_db=metadata_db,
            metadata_dsn=metadata_dsn,
        ),
        interfaces=InterfacesConfig(
            mcp=_parse_bool(interfaces.get("mcp"), DEFAULT_CONFIG.interfaces.mcp),
            api=_parse_bool(interfaces.get("api"), DEFAULT_CONFIG.interfaces.api),
        ),
        paths=PathsConfig(
            data_dir=str(paths.get("data_dir", DEFAULT_CONFIG.paths.data_dir))
        ),
        schema=SchemaConfig(file=str(schema.get("file", DEFAULT_CONFIG.schema.file))),
        storage=StorageConfig(
            dry_run=_parse_bool(storage.get("dry_run"), DEFAULT_CONFIG.storage.dry_run),
            metadata_schema_versions=tuple(
                int(value)
                for value in storage.get(
                    "metadata_schema_versions",
                    list(DEFAULT_CONFIG.storage.metadata_schema_versions),
                )
            ),
            vector=StorageVectorConfig(
                allow_fallback=_parse_bool(
                    storage_vector.get("allow_fallback"),
                    DEFAULT_CONFIG.storage.vector.allow_fallback,
                )
            ),
        ),
    )


def normalize_config(config: HubConfig | dict[str, Any] | None) -> HubConfig:
    if isinstance(config, HubConfig):
        return config
    CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
    return load_config(CONFIG_PATH)


def _load_yaml_if_available(text: str) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to parse YAML config files") from exc

    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        return {}
    return loaded


def load_config(path: str | Path | None = None) -> HubConfig:
    if path is None:
        return DEFAULT_CONFIG

    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    text = config_path.read_text(encoding="utf-8")
    raw = _load_yaml_if_available(text)

    return parse_config(raw)
