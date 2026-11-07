from __future__ import annotations

from typing import Any

import pytest

from memory.ingestion.fact_validation import (
    AssertionType,
    classify_assertion,
    validate_fact_candidate,
)


def test_assertion_classification_uses_the_evidence_clause() -> None:
    text = "I do not own a camera, but my favorite editor is Vim."
    start = text.index("my favorite")

    assert classify_assertion(text, (start, len(text) - 1)) == AssertionType.ASSERTED


@pytest.mark.parametrize(
    "text",
    [
        "I don't like tea, I like coffee.",
        "I don't like tea, and I like coffee.",
        "I don't like tea and I like coffee.",
        "I don't like tea; I like coffee.",
        "I don't like tea, so I prefer coffee.",
        "If I liked tea I would say so, but I like coffee.",
        "Maybe tea is fine, coffee is my preference.",
    ],
)
def test_asserted_evidence_is_not_modified_by_an_earlier_clause(text: str) -> None:
    start = text.index("coffee")

    assert classify_assertion(text, (start, start + len("coffee"))) == AssertionType.ASSERTED


@pytest.mark.parametrize(
    "text, expected",
    [
        ("I like coffee, but I don't like tea.", AssertionType.ASSERTED),
        ("I like coffee, not tea.", AssertionType.ASSERTED),
        ("I like tea, but I don't like coffee.", AssertionType.NEGATED),
        ("I like tea and I might like coffee.", AssertionType.HYPOTHETICAL),
    ],
)
def test_assertion_modifiers_are_scoped_to_their_clause(
    text: str, expected: AssertionType
) -> None:
    start = text.index("coffee")

    assert classify_assertion(text, (start, start + len("coffee"))) == expected


@pytest.mark.parametrize(
    "text, token, expected",
    [
        ("For now, my favorite editor is Vim.", "Vim", AssertionType.TEMPORARY),
        ("Maybe, my favorite editor is Vim.", "Vim", AssertionType.UNCERTAIN),
        ("If asked, I prefer coffee.", "coffee", AssertionType.HYPOTHETICAL),
    ],
)
def test_leading_modifier_comma_keeps_modifier_scope(
    text: str, token: str, expected: AssertionType
) -> None:
    start = text.index(token)

    assert classify_assertion(text, (start, start + len(token))) == expected


@pytest.mark.parametrize(
    "text, token, expected",
    [
        ("I do not own cameras, lenses, or tripods.", "tripods", AssertionType.NEGATED),
        (
            "I do not own the camera, the lens, or the tripod.",
            "tripod",
            AssertionType.NEGATED,
        ),
        ("I might buy tea, coffee, or juice.", "juice", AssertionType.HYPOTHETICAL),
        ("I might invite you and her.", "her", AssertionType.HYPOTHETICAL),
    ],
)
def test_list_punctuation_does_not_end_modifier_scope(
    text: str, token: str, expected: AssertionType
) -> None:
    start = text.index(token)

    assert classify_assertion(text, (start, start + len(token))) == expected


def test_fact_validation_accepts_asserted_evidence_after_a_negated_clause() -> None:
    text = "I don't like tea, but I like coffee."
    start = text.index("coffee")

    validated = validate_fact_candidate(
        _candidate(
            text,
            rule_identifier="preference:drink",
            object_value="coffee",
            evidence_span=(start, start + len("coffee")),
        ),
        [{"role": "user", "text": text}],
        start_message_index=0,
    )

    assert validated is not None
    assert validated["qualifiers"]["extraction"]["assertion_type"] == "asserted"


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


def _candidate(
    text: str,
    *,
    rule_identifier: str,
    object_value: str = "camera",
    evidence_span: tuple[int, int] | None = None,
) -> dict[str, Any]:
    start, end = evidence_span or (0, len(text))
    return {
        "subject": "user",
        "predicate": "owned_item",
        "object": object_value,
        "source_message_indexes": [0],
        "source_quality": "direct_user_statement",
        "qualifiers": {
            "extraction": {
                "evidence_span": {
                    "message_index": 0,
                    "start": start,
                    "end": end,
                },
                "source_role": "user",
                "extractor_name": "external",
                "extractor_version": "1",
                "rule_identifier": rule_identifier,
            }
        },
    }
