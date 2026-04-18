# memory/ingestion/base_agent.py

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseIngestionAgent(ABC):
    """
    Base interface for ingestion agents in ai-memory-hub.

    The ingestion agent is responsible for:
      - receiving raw messages (from MCP or manual input)
      - formatting them into the unified JSON schema
      - validating the schema
      - calling memory.insert
      - triggering chunking + embeddings
      - returning a structured result

    This class defines the contract. Implementations may use:
      - LangGraph (default)
      - custom Python logic
      - any other orchestration framework
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Store the loaded config so the agent can access:
          - providers (inference, embeddings, storage, vector_db)
          - schema path
          - interface settings
        """
        self.config = config

    # ---------------------------------------------------------
    # REQUIRED METHODS
    # ---------------------------------------------------------

    @abstractmethod
    async def ingest_messages(
        self,
        messages: List[Dict[str, str]],
        *,
        source: str,
        title: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Main ingestion entrypoint.

        Parameters:
            messages: List of raw messages, each { "role": "user|assistant", "text": "..." }
            source:   Where the messages came from (chatgpt, claude, copilot, mcp, manual, etc.)
            title:    Optional conversation title
            metadata: Optional metadata overrides

        Returns:
            A dict representing the validated conversation object
            after it has been inserted into storage.
        """
        raise NotImplementedError

    # ---------------------------------------------------------
    # OPTIONAL HOOKS
    # ---------------------------------------------------------

    def preprocess_messages(
        self,
        messages: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        """
        Optional hook for normalization before schema formatting.
        Default: no-op.
        """
        return messages

    def postprocess_result(
        self,
        result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Optional hook for modifying the final result.
        Default: no-op.
        """
        return result
