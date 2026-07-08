from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")


class DerivedMemoryType(StrEnum):
    ENTITY = "entity"
    RELATIONSHIP = "relationship"
    SUMMARY = "summary"
    FACT = "fact"
    REVIEW_ITEM = "review_item"
    FORGET_AUDIT = "forget_audit"


class ExtractorKind(StrEnum):
    DETERMINISTIC = "deterministic"
    HOSTED_LLM = "hosted_llm"
    PLUGIN = "plugin"
    MANUAL = "manual"


class ReviewStatus(StrEnum):
    ACTIVE = "active"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    HIDDEN = "hidden"
    PURGED = "purged"


class SourceScope(StrEnum):
    PRIVATE = "private"
    PROJECT = "project"
    TEAM = "team"
    IMPORTED = "imported"


class SourceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str | None = None
    message_index: int | None = Field(default=None, ge=0)
    chunk_id: str | None = None
    fact_id: str | None = None
    summary_id: str | None = None

    @model_validator(mode="after")
    def require_source(self) -> SourceProvenance:
        if not any(
            (
                self.conversation_id,
                self.chunk_id,
                self.fact_id,
                self.summary_id,
            )
        ):
            raise ValueError("provenance must reference conversation, chunk, fact, or summary evidence")
        if self.message_index is not None and not self.conversation_id:
            raise ValueError("message_index provenance requires conversation_id")
        return self


class ExtractorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    kind: ExtractorKind = ExtractorKind.DETERMINISTIC


class DerivedMemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    record_type: DerivedMemoryType
    extractor: ExtractorMetadata
    confidence: float = Field(ge=0.0, le=1.0)
    source_scope: SourceScope = SourceScope.PRIVATE
    review_status: ReviewStatus = ReviewStatus.ACTIVE
    provenance: list[SourceProvenance] = Field(min_length=1)
    owner_id: str | None = None
    project_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError("advanced-memory id must be a stable bounded identifier")
        return value


def stable_derived_id(prefix: str, payload: dict[str, Any]) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}:{digest}"
