from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HandoffStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    WAITING_FOR_REVIEW = "waiting_for_review"
    COMPLETE = "complete"
    SUPERSEDED = "superseded"


class HandoffCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    memory_id: str = Field(min_length=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    score: float


class HandoffClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)


class HandoffChangedFile(HandoffClaim):
    path: str = Field(min_length=1)
    committed: bool | None = None


class HandoffCommand(HandoffClaim):
    command: str = Field(min_length=1)
    result: str | None = None


class HandoffPacket(BaseModel):
    """Ephemeral, evidence-linked continuation packet."""

    model_config = ConfigDict(extra="forbid")
    handoff_id: str = Field(min_length=1)
    project_id: str | None = None
    thread_id: str | None = None
    source_agent: str | None = None
    target_agent: str | None = None
    goal: HandoffClaim
    status: HandoffStatus
    summary: list[HandoffClaim]
    decisions: list[HandoffClaim] = Field(default_factory=list)
    changed_files: list[HandoffChangedFile] = Field(default_factory=list)
    commands_run: list[HandoffCommand] = Field(default_factory=list)
    validation: list[HandoffClaim] = Field(default_factory=list)
    blockers: list[HandoffClaim] = Field(default_factory=list)
    next_steps: list[HandoffClaim] = Field(default_factory=list)
    citations: list[HandoffCitation]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    confidence: str
    completeness_notes: list[str] = Field(default_factory=list)
    context_tokens_used: int = Field(ge=0)
    context_token_budget: int = Field(ge=1)
    context_truncated: bool = False

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: str) -> str:
        if value not in {"none", "low", "medium", "high"}:
            raise ValueError("confidence must be one of: none, low, medium, high")
        return value
