from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProvidersConfig:
    embeddings: str = "local"
    vector_db: str = "lancedb"


@dataclass(frozen=True)
class InterfacesConfig:
    mcp: bool = True
    api: bool = True


@dataclass(frozen=True)
class PathsConfig:
    data_dir: str = "./data"


@dataclass(frozen=True)
class HubConfig:
    providers: ProvidersConfig
    interfaces: InterfacesConfig
    paths: PathsConfig


DEFAULT_CONFIG = HubConfig(
    providers=ProvidersConfig(),
    interfaces=InterfacesConfig(),
    paths=PathsConfig(),
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

    embeddings = str(providers.get("embeddings", DEFAULT_CONFIG.providers.embeddings)).lower()
    vector_db = str(providers.get("vector_db", DEFAULT_CONFIG.providers.vector_db)).lower()
    if embeddings not in {"openai", "local"}:
        raise ValueError("providers.embeddings must be one of: openai, local")
    if vector_db not in {"lancedb", "pgvector"}:
        raise ValueError("providers.vector_db must be one of: lancedb, pgvector")

    return HubConfig(
        providers=ProvidersConfig(embeddings=embeddings, vector_db=vector_db),
        interfaces=InterfacesConfig(
            mcp=_parse_bool(interfaces.get("mcp"), DEFAULT_CONFIG.interfaces.mcp),
            api=_parse_bool(interfaces.get("api"), DEFAULT_CONFIG.interfaces.api),
        ),
        paths=PathsConfig(data_dir=str(paths.get("data_dir", DEFAULT_CONFIG.paths.data_dir))),
    )


def normalize_config(config: HubConfig | dict[str, Any] | None) -> HubConfig:
    if isinstance(config, HubConfig):
        return config
    if isinstance(config, dict):
        return parse_config(config)
    return load_config()


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
    if config_path.suffix.lower() == ".json":
        raw = json.loads(text)
    else:
        raw = _load_yaml_if_available(text)

    return parse_config(raw)
