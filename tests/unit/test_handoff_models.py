from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from memory.ingestion.handoff_models import HandoffClaim, HandoffPacket


def _packet_data() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "handoff_id": "ephemeral-id",
        "goal": {"text": "Continue the task"},
        "status": "active",
        "summary": [],
        "citations": [],
        "created_at": now,
        "updated_at": now,
        "confidence": "none",
        "context_tokens_used": 0,
        "context_token_budget": 100,
    }


def test_handoff_packet_accepts_ephemeral_packet() -> None:
    packet = HandoffPacket.model_validate(_packet_data())

    assert packet.goal == HandoffClaim(text="Continue the task")
    assert packet.status == "active"


@pytest.mark.parametrize("field", ["status", "confidence"])
def test_handoff_packet_rejects_invalid_enums(field: str) -> None:
    data = _packet_data()
    data[field] = "unknown"

    with pytest.raises(ValidationError):
        HandoffPacket.model_validate(data)


def test_handoff_packet_rejects_non_positive_budget() -> None:
    data = _packet_data()
    data["context_token_budget"] = 0

    with pytest.raises(ValidationError):
        HandoffPacket.model_validate(data)
