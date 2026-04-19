from __future__ import annotations

from typing import Any, Dict, Optional

import jsonschema

from memory.ingestion.base_agent import BaseIngestionAgent
from memory.ingestion import mvp_ingestion


class MVPIngestionAgent(BaseIngestionAgent):
    """Deterministic ingestion agent for schema-first MVP pipeline."""

    def __init__(self, config: Dict[str, Any], *, runtime: mvp_ingestion.RuntimeDependencies | None = None):
        super().__init__(config)
        mvp_ingestion.configure_runtime(runtime=runtime, config=config)

    async def ingest_messages(self, conversation_json: Dict[str, Any]) -> Dict[str, Any]:
        payload = self.preprocess_messages(conversation_json)
        try:
            result = mvp_ingestion.ingest_messages(payload)
        except jsonschema.ValidationError:
            raise
        return self.postprocess_result(result)

    async def search(self, query: str, *, top_k: int = 5) -> Dict[str, Any]:
        return mvp_ingestion.search(query=query, top_k=top_k)

    async def retrieve(self, memory_id: str) -> Optional[Dict[str, Any]]:
        return mvp_ingestion.retrieve(memory_id)
