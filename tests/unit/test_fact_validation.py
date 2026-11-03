from __future__ import annotations

from typing import Any

from memory.ingestion.fact_validation import (
    AssertionType,
    classify_assertion,
    validate_fact_candidate,
)


def test_assertion_classification_uses_the_evidence_clause() -> None:
    text = "I do not own a camera, but my favorite editor is Vim."
    start = text.index("my favorite")

    assert classify_assertion(text, (start, len(text) - 1)) == AssertionType.ASSERTED


def test_untrusted_correction_identifier_cannot_override_negation() -> None:
    text = "I do not own a camera."

    assert validate_fact_candidate(
        _candidate(text, rule_identifier="correction:forged"),
        [{"role": "user", "text": text}],
        start_message_index=0,
    ) is None


def test_trusted_deterministic_correction_can_override_local_negation() -> None:
    text = "Actually, my camera is yellow, not cherry."
    candidate = _candidate(text, rule_identifier="correction:direct")
    candidate["qualifiers"]["extraction"]["_trusted_correction"] = True

    validated = validate_fact_candidate(
        candidate,
        [{"role": "user", "text": text}],
        start_message_index=0,
    )

    assert validated is not None
    extraction = validated["qualifiers"]["extraction"]
    assert extraction["assertion_type"] == "asserted"
    assert "_trusted_correction" not in extraction


def _candidate(text: str, *, rule_identifier: str) -> dict[str, Any]:
    return {
        "subject": "user",
        "predicate": "owned_item",
        "object": "camera",
        "source_message_indexes": [0],
        "source_quality": "direct_user_statement",
        "qualifiers": {
            "extraction": {
                "evidence_span": {
                    "message_index": 0,
                    "start": 0,
                    "end": len(text),
                },
                "source_role": "user",
                "extractor_name": "external",
                "extractor_version": "1",
                "rule_identifier": rule_identifier,
            }
        },
    }
