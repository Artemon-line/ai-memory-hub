from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SummaryType(StrEnum):
    PROFILE = "profile"
    CONVERSATION = "conversation"
    TOPIC = "topic"
    PROJECT = "project"


class SummaryProvenanceStatus(StrEnum):
    FACT_IDS = "fact_ids"
    CONVERSATION_IDS = "conversation_ids"
    MIXED = "mixed"
    NONE = "none"


class SummaryBasis(StrEnum):
    ACTIVE_FACTS = "active_facts"


class GeneratedSummaryProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str | None = None
    predicate: str | None = None
    source_conversation_id: str | None = None
    source_message_indexes: list[int] = []
    last_confirmed_at: str | None = None


class GeneratedSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: SummaryType
    target_id: str
    project_id: str | None = None
    owner_id: str | None = None
    text: str
    basis: SummaryBasis | str
    provenance_status: SummaryProvenanceStatus
    fact_count: int = 0
    active_fact_count: int = 0
    freshest_at: str | None = None
    source_quality_counts: dict[str, int] = {}
    confidence_counts: dict[str, int] = {}
    filters: dict[str, str] = {}
    provenance: list[GeneratedSummaryProvenance] = []
    generated_at: str
