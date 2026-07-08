from __future__ import annotations

import pytest

from memory.advanced_memory import (
    AgentMemoryFilter,
    DerivedMemoryRecord,
    DerivedMemoryType,
    ExtractorMetadata,
    ForgetMode,
    MemoryScoringSignals,
    MemoryScoringWeights,
    ReviewReason,
    ReviewStatus,
    SensitivityClass,
    SharedMemoryPolicy,
    SharedMemoryVisibility,
    SourceProvenance,
    advanced_relevance_boost,
    create_forget_audit_record,
    create_review_item,
    filter_derived_records_for_agent,
    stable_derived_id,
)


def test_derived_memory_record_requires_source_provenance() -> None:
    with pytest.raises(ValueError, match="provenance"):
        DerivedMemoryRecord(
            id="entity:test",
            record_type=DerivedMemoryType.ENTITY,
            extractor=ExtractorMetadata(name="deterministic-graph", version="1"),
            confidence=0.9,
            provenance=[],
        )


def test_provenance_rejects_message_index_without_conversation_id() -> None:
    with pytest.raises(ValueError, match="message_index"):
        SourceProvenance(message_index=2)


def test_derived_memory_record_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError):
        DerivedMemoryRecord(
            id="relationship:test",
            record_type=DerivedMemoryType.RELATIONSHIP,
            extractor=ExtractorMetadata(name="deterministic-graph", version="1"),
            confidence=0.75,
            review_status=ReviewStatus.NEEDS_REVIEW,
            provenance=[SourceProvenance(conversation_id="conv-1", message_index=0)],
            unsafe_extra=True,
        )


def test_stable_derived_id_is_order_independent() -> None:
    left = stable_derived_id("entity", {"name": "PGVector", "type": "tool"})
    right = stable_derived_id("entity", {"type": "tool", "name": "PGVector"})

    assert left == right
    assert left.startswith("entity:")


def test_advanced_relevance_boost_is_bounded_and_explainable() -> None:
    scoring = advanced_relevance_boost(
        MemoryScoringSignals(
            pinned=True,
            importance=1.0,
            access_count=100,
            confidence=1.0,
            updated_at="2026-07-08T00:00:00Z",
        ),
        MemoryScoringWeights(pin_weight=1.0, importance_weight=1.0, access_weight=1.0),
    )

    assert scoring["boost"] == 0.6
    assert scoring["signals"]["pinned"] is True


def test_advanced_scoring_rejects_unknown_signal_keys() -> None:
    with pytest.raises(ValueError):
        MemoryScoringSignals.model_validate({"pinned": True, "raw_payload": "nope"})


def test_review_queue_item_has_stable_id_and_provenance() -> None:
    item = create_review_item(
        reason=ReviewReason.CONFLICT,
        record_type=DerivedMemoryType.RELATIONSHIP,
        record_id="rel:1",
        provenance=[SourceProvenance(conversation_id="conv-1", message_index=0)],
        created_at="2026-07-08T00:00:00Z",
        project_id="project-a",
    )

    assert item.id.startswith("review:")
    assert item.status == ReviewStatus.NEEDS_REVIEW
    assert item.provenance[0].conversation_id == "conv-1"


def test_forget_audit_requires_admin_reason_and_recommends_export_for_purge() -> None:
    audit = create_forget_audit_record(
        target_type=DerivedMemoryType.FACT,
        target_id="fact-1",
        mode=ForgetMode.PURGE,
        admin_actor_id="admin-a",
        reason="user requested local purge",
        provenance=[SourceProvenance(fact_id="fact-1")],
        created_at="2026-07-08T00:00:00Z",
    )

    assert audit.id.startswith("forget:")
    assert audit.export_recommended is True
    assert audit.admin_actor_id == "admin-a"


def test_forget_audit_rejects_empty_reason() -> None:
    with pytest.raises(ValueError, match="reason"):
        create_forget_audit_record(
            target_type=DerivedMemoryType.FACT,
            target_id="fact-1",
            mode=ForgetMode.HIDE,
            admin_actor_id="admin-a",
            reason="",
            provenance=[SourceProvenance(fact_id="fact-1")],
            created_at="2026-07-08T00:00:00Z",
        )


def test_shared_memory_policy_defaults_private_and_requires_project_for_project_scope() -> None:
    assert SharedMemoryPolicy().visibility == SharedMemoryVisibility.PRIVATE
    with pytest.raises(ValueError, match="project_id"):
        SharedMemoryPolicy(visibility=SharedMemoryVisibility.PROJECT)


def test_agent_memory_filter_narrows_records() -> None:
    records = [
        {
            "id": "entity:1",
            "record_type": "entity",
            "project_id": "project-a",
            "metadata": {"source": "codex", "sensitivity": "private", "trusted_capture": True},
        },
        {
            "id": "entity:2",
            "record_type": "entity",
            "project_id": "project-b",
            "metadata": {"source": "opencode", "sensitivity": "secret", "trusted_capture": True},
        },
        {
            "id": "rel:1",
            "record_type": "relationship",
            "project_id": "project-a",
            "metadata": {"source": "codex", "sensitivity": "internal", "trusted_capture": False},
        },
    ]
    agent_filter = AgentMemoryFilter(
        allowed_sources=["codex"],
        allowed_project_ids=["project-a"],
        allowed_record_types=[DerivedMemoryType.ENTITY],
        max_sensitivity=SensitivityClass.PRIVATE,
        trusted_capture_only=True,
    )

    assert [record["id"] for record in filter_derived_records_for_agent(records, agent_filter)] == ["entity:1"]
