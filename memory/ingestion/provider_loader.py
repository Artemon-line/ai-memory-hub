from __future__ import annotations

from typing import Any

from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent


def load_ingestion_agent(config: dict[str, Any], **kwargs: Any) -> BaseIngestionAgent:
    providers = config.get("providers", {}) if isinstance(config, dict) else {}
    agent_name = str(providers.get("agent", "mvp")).lower()

    if agent_name == "mvp":
        return MVPIngestionAgent(config=config, **kwargs)

    raise ValueError(f"Unsupported ingestion agent '{agent_name}'. Supported values: mvp")
