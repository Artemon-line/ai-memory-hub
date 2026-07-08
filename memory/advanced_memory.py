from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")


class DerivedMemoryType(StrEnum):
    ENTITY = "entity"
    RELATIONSHIP = "relationship"
    SUMMARY = "summary"
    FACT = "fact"
    REVIEW_ITEM = "review_item"
    FORGET_AUDIT = "forget_audit"


class EntityType(StrEnum):
    PERSON = "person"
    PROJECT = "project"
    TOOL = "tool"
    PROVIDER = "provider"
    ORGANIZATION = "organization"
    DECISION = "decision"
    PREFERENCE = "preference"
    OWNED_ITEM = "owned_item"


class RelationshipPredicate(StrEnum):
    CREATED_BY = "created_by"
    USES_TOOL = "uses_tool"
    USES_PROVIDER = "uses_provider"
    DECIDED = "decided"
    PREFERS = "prefers"
    OWNS = "owns"
    WORKS_ON = "works_on"
    CHANGED_TO = "changed_to"


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


class ForgetMode(StrEnum):
    HIDE = "hide"
    ARCHIVE = "archive"
    PURGE = "purge"


class ReviewReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    CONFLICT = "conflict"
    STALE_SUMMARY = "stale_summary"
    REDUNDANT_MEMORY = "redundant_memory"


class MemoryScoringSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pinned: bool = False
    importance: float = Field(default=0.0, ge=0.0, le=1.0)
    access_count: int = Field(default=0, ge=0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    updated_at: str | None = None
    contradicted: bool = False


class MemoryScoringWeights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recency_weight: float = Field(default=0.05, ge=0.0, le=1.0)
    importance_weight: float = Field(default=0.15, ge=0.0, le=1.0)
    pin_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    access_weight: float = Field(default=0.05, ge=0.0, le=1.0)


class ReviewQueueItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    reason: ReviewReason
    record_type: DerivedMemoryType
    record_id: str
    priority: int = Field(default=2, ge=0, le=3)
    provenance: list[SourceProvenance] = Field(min_length=1)
    owner_id: str | None = None
    project_id: str | None = None
    created_at: str
    status: ReviewStatus = ReviewStatus.NEEDS_REVIEW


class ForgetAuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    target_type: DerivedMemoryType | str
    target_id: str
    mode: ForgetMode
    admin_actor_id: str
    reason: str = Field(min_length=1, max_length=512)
    export_recommended: bool = True
    created_at: str
    provenance: list[SourceProvenance] = Field(min_length=1)


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


def advanced_relevance_boost(
    signals: MemoryScoringSignals, weights: MemoryScoringWeights, *, now: datetime | None = None
) -> dict[str, Any]:
    recency = _recency_score(signals.updated_at, now=now)
    access = min(signals.access_count, 20) / 20
    contradiction_penalty = 0.15 if signals.contradicted else 0.0
    boost = (
        (weights.pin_weight if signals.pinned else 0.0)
        + signals.importance * weights.importance_weight
        + recency * weights.recency_weight
        + access * weights.access_weight
        + signals.confidence * 0.03
        - contradiction_penalty
    )
    bounded = max(-0.2, min(0.6, boost))
    return {
        "boost": bounded,
        "signals": {
            "pinned": signals.pinned,
            "importance": signals.importance,
            "recency": recency,
            "access": access,
            "confidence": signals.confidence,
            "contradicted": signals.contradicted,
        },
    }


def create_review_item(
    *,
    reason: ReviewReason,
    record_type: DerivedMemoryType,
    record_id: str,
    provenance: list[SourceProvenance],
    created_at: str,
    owner_id: str | None = None,
    project_id: str | None = None,
    priority: int = 2,
) -> ReviewQueueItem:
    item_id = stable_derived_id(
        "review",
        {
            "reason": reason.value,
            "record_type": record_type.value,
            "record_id": record_id,
            "project_id": project_id,
        },
    )
    return ReviewQueueItem(
        id=item_id,
        reason=reason,
        record_type=record_type,
        record_id=record_id,
        priority=priority,
        provenance=provenance,
        owner_id=owner_id,
        project_id=project_id,
        created_at=created_at,
    )


def create_forget_audit_record(
    *,
    target_type: DerivedMemoryType | str,
    target_id: str,
    mode: ForgetMode,
    admin_actor_id: str,
    reason: str,
    provenance: list[SourceProvenance],
    created_at: str,
) -> ForgetAuditRecord:
    audit_id = stable_derived_id(
        "forget",
        {
            "target_type": str(target_type),
            "target_id": target_id,
            "mode": mode.value,
            "admin_actor_id": admin_actor_id,
            "created_at": created_at,
        },
    )
    return ForgetAuditRecord(
        id=audit_id,
        target_type=target_type,
        target_id=target_id,
        mode=mode,
        admin_actor_id=admin_actor_id,
        reason=reason,
        provenance=provenance,
        created_at=created_at,
        export_recommended=mode == ForgetMode.PURGE,
    )


def _recency_score(value: str | None, *, now: datetime | None = None) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    current = now or datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age_days = max((current.astimezone(UTC) - parsed.astimezone(UTC)).days, 0)
    return max(0.0, 1.0 - min(age_days, 365) / 365)


class EntityRecord(DerivedMemoryRecord):
    record_type: DerivedMemoryType = DerivedMemoryType.ENTITY
    entity_type: EntityType
    name: str = Field(min_length=1, max_length=256)
    normalized_name: str = Field(min_length=1, max_length=256)
    aliases: list[str] = Field(default_factory=list)
    source_fact_id: str | None = None


class RelationshipRecord(DerivedMemoryRecord):
    record_type: DerivedMemoryType = DerivedMemoryType.RELATIONSHIP
    subject: str = Field(min_length=1, max_length=256)
    predicate: RelationshipPredicate | str
    object: str = Field(min_length=1, max_length=512)
    subject_entity_id: str | None = None
    object_entity_id: str | None = None
    freshness_at: str | None = None
    superseded_by: str | None = None
    superseded_at: str | None = None
    conflict_group: str | None = None


_EXTRACTOR = ExtractorMetadata(name="deterministic-graph", version="1")
_KNOWN_TOOLS = (
    "Codex",
    "opencode",
    "PGVector",
    "Docker",
    "Podman",
    "tiktoken",
    "Ollama",
    "Bruno",
)
_KNOWN_PROVIDERS = (
    "SQLite",
    "Postgres",
    "MongoDB",
    "LanceDB",
    "PGVector",
    "ChromaDB",
    "Qdrant",
    "Milvus",
    "Weaviate",
    "Elasticsearch",
    "OpenSearch",
)
_CREATOR_RE = re.compile(r"\b(?:the\s+)?creator\s+(?:is|was)\s+(?P<object>[^.!\n]{2,80})", re.I)
_PROJECT_RE = re.compile(r"\b(?P<subject>[A-Z][A-Za-z0-9 _-]{2,80})\s+is\s+(?P<object>[^.!\n]{3,160})", re.I)
_PREFERENCE_RE = re.compile(r"\b(?:my|the user'?s)\s+favorite\s+(?P<subject>[a-zA-Z][A-Za-z _-]{1,60})\s+is\s+(?P<object>[^.!\n]{2,120})", re.I)
_OWN_RE = re.compile(r"\bI\s+(?:own|have)\s+(?P<object>[^.!\n]{2,140})", re.I)
_WORKS_ON_RE = re.compile(r"\b(?P<person>[A-Z][A-Za-z _.-]{1,80})\s+works\s+on\s+(?P<project>[A-Z][A-Za-z0-9 _-]{2,80})", re.I)
_USES_RE = re.compile(r"\b(?P<subject>[A-Z][A-Za-z0-9 _-]{2,80})\s+uses\s+(?P<object>[^.!\n]{2,80})", re.I)
_CHANGED_RE = re.compile(r"\b(?P<subject>[A-Z][A-Za-z0-9 _-]{2,80})\s+changed\s+to\s+(?P<object>[^.!\n]{2,120})", re.I)


def extract_memory_graph(conversation: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    entities: list[EntityRecord] = []
    relationships: list[RelationshipRecord] = []
    seen_entities: set[str] = set()
    seen_relationships: set[str] = set()
    conversation_id = str(conversation.get("id") or "")
    owner_id = _optional_str(conversation.get("owner_id"))
    project_id = _optional_str(conversation.get("project_id"))
    timestamp = _optional_str(conversation.get("timestamp"))
    messages = conversation.get("messages", [])
    if not isinstance(messages, list):
        return {"entities": [], "relationships": []}

    def add_entity(name: str, entity_type: EntityType, index: int) -> str:
        cleaned = _clean_text(name)
        normalized = normalize_graph_text(cleaned)
        payload = {"conversation_id": conversation_id, "type": entity_type.value, "name": normalized}
        entity_id = stable_derived_id("entity", payload)
        if entity_id not in seen_entities:
            seen_entities.add(entity_id)
            entities.append(
                EntityRecord(
                    id=entity_id,
                    entity_type=entity_type,
                    name=cleaned,
                    normalized_name=normalized,
                    extractor=_EXTRACTOR,
                    confidence=0.9,
                    provenance=[SourceProvenance(conversation_id=conversation_id, message_index=index)],
                    owner_id=owner_id,
                    project_id=project_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        return entity_id

    def add_relationship(
        subject: str,
        predicate: RelationshipPredicate,
        object_value: str,
        index: int,
        *,
        subject_type: EntityType = EntityType.PROJECT,
        object_type: EntityType = EntityType.TOOL,
        confidence: float = 0.85,
    ) -> None:
        subject_id = add_entity(subject, subject_type, index)
        object_id = add_entity(object_value, object_type, index)
        payload = {
            "conversation_id": conversation_id,
            "message_index": index,
            "subject": normalize_graph_text(subject),
            "predicate": predicate.value,
            "object": normalize_graph_text(object_value),
        }
        relationship_id = stable_derived_id("rel", payload)
        if relationship_id in seen_relationships:
            return
        seen_relationships.add(relationship_id)
        relationships.append(
            RelationshipRecord(
                id=relationship_id,
                subject=_clean_text(subject),
                predicate=predicate,
                object=_clean_text(object_value),
                subject_entity_id=subject_id,
                object_entity_id=object_id,
                extractor=_EXTRACTOR,
                confidence=confidence,
                provenance=[SourceProvenance(conversation_id=conversation_id, message_index=index)],
                owner_id=owner_id,
                project_id=project_id,
                created_at=timestamp,
                updated_at=timestamp,
                freshness_at=timestamp,
                conflict_group=_relationship_conflict_group(subject, predicate),
            )
        )

    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        text = str(message.get("text", ""))
        for tool in _names_in_text(text, _KNOWN_TOOLS):
            add_entity(tool, EntityType.TOOL, index)
        for provider in _names_in_text(text, _KNOWN_PROVIDERS):
            add_entity(provider, EntityType.PROVIDER, index)
        for match in _CREATOR_RE.finditer(text):
            project = _metadata_title(conversation) or "project"
            add_relationship(project, RelationshipPredicate.CREATED_BY, match.group("object"), index, object_type=EntityType.PERSON)
        for match in _PROJECT_RE.finditer(text):
            subject = match.group("subject")
            object_value = match.group("object")
            normalized_subject = normalize_graph_text(subject)
            if normalized_subject in {"i", "the creator"} or normalized_subject.startswith("my favorite "):
                continue
            add_relationship(subject, RelationshipPredicate.DECIDED, object_value, index, object_type=EntityType.DECISION, confidence=0.72)
        for match in _PREFERENCE_RE.finditer(text):
            add_relationship(
                match.group("subject"),
                RelationshipPredicate.PREFERS,
                match.group("object"),
                index,
                subject_type=EntityType.PREFERENCE,
                object_type=EntityType.PREFERENCE,
            )
        for match in _OWN_RE.finditer(text):
            add_relationship("user", RelationshipPredicate.OWNS, match.group("object"), index, subject_type=EntityType.PERSON, object_type=EntityType.OWNED_ITEM)
        for match in _WORKS_ON_RE.finditer(text):
            add_relationship(match.group("person"), RelationshipPredicate.WORKS_ON, match.group("project"), index, subject_type=EntityType.PERSON, object_type=EntityType.PROJECT)
        for match in _USES_RE.finditer(text):
            add_relationship(match.group("subject"), RelationshipPredicate.USES_TOOL, match.group("object"), index)
        for match in _CHANGED_RE.finditer(text):
            add_relationship(match.group("subject"), RelationshipPredicate.CHANGED_TO, match.group("object"), index, object_type=EntityType.PREFERENCE)

    return {
        "entities": [entity.model_dump(mode="json") for entity in entities],
        "relationships": [relationship.model_dump(mode="json") for relationship in relationships],
    }


def normalize_graph_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _clean_text(value: str) -> str:
    return " ".join(value.strip().strip(" .,!?:;").split())


def _names_in_text(text: str, names: Iterable[str]) -> list[str]:
    folded = text.casefold()
    return [name for name in names if name.casefold() in folded]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _metadata_title(conversation: dict[str, Any]) -> str | None:
    title = conversation.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    metadata = conversation.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("title"), str):
        return metadata["title"].strip() or None
    return None


def _relationship_conflict_group(subject: str, predicate: RelationshipPredicate) -> str:
    return f"{normalize_graph_text(subject)}:{predicate.value}"
