from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

FORBIDDEN_PLUGIN_OUTPUT_KEYS = {
    "owner_id",
    "project_id",
    "auth",
    "authorization",
    "api_key",
    "token",
    "raw_html",
    "screenshot",
    "screenshot_data",
}


class PluginSafetyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_secrets: bool = False
    allow_raw_browser_artifacts: bool = False
    require_deterministic_fixtures: bool = True


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    capabilities: list[str] = Field(default_factory=list)
    safety: PluginSafetyPolicy = Field(default_factory=PluginSafetyPolicy)


class CaptureImportAdapter(Protocol):
    manifest: PluginManifest

    def import_conversations(self, source: Any) -> list[dict[str, Any]]:
        """Return normalized conversation-like dictionaries for core validation."""
        ...


class IngestionEnricher(Protocol):
    manifest: PluginManifest

    def enrich(self, conversation: dict[str, Any]) -> dict[str, Any]:
        """Return derived metadata/fact/entity/relationship payloads with provenance."""
        ...


class EmbeddingProviderPlugin(Protocol):
    manifest: PluginManifest
    dimension: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Return embeddings through the existing provider boundary."""
        ...


def validate_plugin_conversation_output(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("plugin conversation output must be an object")
    _reject_forbidden_keys(payload)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("plugin conversation output must include messages")
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("plugin message output must be an object")
        if not isinstance(message.get("role"), str) or not isinstance(message.get("text"), str):
            raise ValueError("plugin messages must include string role and text")
        _reject_forbidden_keys(message)
    return payload


def validate_plugin_enrichment_output(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("plugin enrichment output must be an object")
    _reject_forbidden_keys(payload)
    for key in ("facts", "entities", "relationships", "summaries"):
        value = payload.get(key, [])
        if value is not None and not isinstance(value, list):
            raise ValueError(f"plugin enrichment field {key} must be a list")
    return payload


def _reject_forbidden_keys(payload: dict[str, Any]) -> None:
    found = FORBIDDEN_PLUGIN_OUTPUT_KEYS.intersection(payload)
    if found:
        blocked = ", ".join(sorted(found))
        raise ValueError(f"plugin output may not set protected or unsafe keys: {blocked}")
