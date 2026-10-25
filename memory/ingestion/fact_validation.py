from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AssertionType(StrEnum):
    ASSERTED = "asserted"
    NEGATED = "negated"
    HYPOTHETICAL = "hypothetical"
    QUOTED = "quoted"
    UNCERTAIN = "uncertain"
    TEMPORARY = "temporary"


class TemporalScope(StrEnum):
    ONGOING = "ongoing"
    TEMPORARY = "temporary"


class ValidationStatus(StrEnum):
    LEGACY = "legacy"
    VALIDATED = "validated"


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message_index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "EvidenceSpan":
        if self.end <= self.start:
            raise ValueError("evidence span end must be greater than start")
        return self


class ExternalFactCandidate(BaseModel):
    """Bounded, evidence-bearing output accepted from an external extractor."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=160)
    predicate: str = Field(min_length=1, max_length=120)
    object: str = Field(min_length=1, max_length=500)
    qualifiers: dict[str, Any] = Field(default_factory=dict)
    source_message_indexes: list[int] = Field(min_length=1, max_length=16)
    evidence_span: EvidenceSpan
    extractor_name: str = Field(default="external", min_length=1, max_length=80)
    extractor_version: str = Field(default="unspecified", min_length=1, max_length=40)
    rule_identifier: str = Field(default="external", min_length=1, max_length=120)

    @field_validator("subject", "predicate", "object", "extractor_name", "extractor_version", "rule_identifier")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("source_message_indexes")
    @classmethod
    def unique_indexes(cls, values: list[int]) -> list[int]:
        if any(index < 0 for index in values):
            raise ValueError("source message indexes must be non-negative")
        return list(dict.fromkeys(values))

    @field_validator("qualifiers")
    @classmethod
    def bound_qualifiers(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 32:
            raise ValueError("qualifiers exceed key limit")
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("qualifiers must be JSON serializable") from exc
        if len(encoded.encode("utf-8")) > 4096:
            raise ValueError("qualifiers exceed size limit")
        return value

    @model_validator(mode="after")
    def evidence_index_is_declared(self) -> "ExternalFactCandidate":
        if self.evidence_span.message_index not in self.source_message_indexes:
            raise ValueError("evidence message index must be listed in source_message_indexes")
        return self


_NEGATED_RE = re.compile(
    r"\b(?:do(?:es)?\s+not|don['’]t|doesn['’]t|didn['’]t|never|no\s+longer|is\s+not|isn['’]t|am\s+not|not)\b",
    re.IGNORECASE,
)
_HYPOTHETICAL_RE = re.compile(
    r"\b(?:if|hypothetically|suppose|assuming|would|could|might|may\s+(?:be|have|own|prefer|like|use))\b",
    re.IGNORECASE,
)
_UNCERTAIN_RE = re.compile(
    r"\b(?:maybe|perhaps|probably|possibly|I\s+(?:think|guess|suspect)|not\s+sure|unsure)\b",
    re.IGNORECASE,
)
_TEMPORARY_RE = re.compile(
    r"\b(?:for\s+now|temporarily|temporary|today|this\s+(?:week|month)|until\b[^.?!\n]{0,80})",
    re.IGNORECASE,
)


def classify_assertion(text: str, span: tuple[int, int]) -> AssertionType:
    start, end = span
    clause_start = max(text.rfind(separator, 0, start) for separator in ".?!\n") + 1
    clause_end_candidates = [
        position for separator in ".?!\n" if (position := text.find(separator, end)) >= 0
    ]
    clause_end = min(clause_end_candidates) if clause_end_candidates else len(text)
    clause = text[clause_start:clause_end]
    relative_start = max(0, start - clause_start)
    if _inside_quotes(clause, relative_start):
        return AssertionType.QUOTED
    if _NEGATED_RE.search(clause):
        return AssertionType.NEGATED
    if _HYPOTHETICAL_RE.search(clause):
        return AssertionType.HYPOTHETICAL
    if _UNCERTAIN_RE.search(clause):
        return AssertionType.UNCERTAIN
    if _TEMPORARY_RE.search(clause):
        return AssertionType.TEMPORARY
    return AssertionType.ASSERTED


def validate_fact_candidate(
    fact: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    *,
    start_message_index: int,
) -> dict[str, Any] | None:
    candidate = dict(fact)
    qualifiers = candidate.get("qualifiers")
    if not isinstance(qualifiers, dict):
        return None
    extraction = qualifiers.get("extraction")
    if not isinstance(extraction, dict):
        return None
    try:
        evidence = EvidenceSpan.model_validate(extraction.get("evidence_span"))
    except ValueError:
        return None
    local_index = evidence.message_index - start_message_index
    if local_index < 0 or local_index >= len(messages):
        return None
    message = messages[local_index]
    text = str(message.get("text", ""))
    if evidence.end > len(text) or not text[evidence.start : evidence.end].strip():
        return None
    indexes = candidate.get("source_message_indexes")
    if not isinstance(indexes, list) or evidence.message_index not in indexes:
        return None
    source_role = str(message.get("role", ""))
    declared_role = str(extraction.get("source_role", ""))
    if declared_role and declared_role != source_role:
        return None

    assertion = classify_assertion(text, (evidence.start, evidence.end))
    if str(extraction.get("rule_identifier", "")).startswith("correction:"):
        assertion = AssertionType.ASSERTED
    if assertion in {AssertionType.NEGATED, AssertionType.HYPOTHETICAL}:
        return None
    if str(extraction.get("extractor_name")) != "deterministic_rules":
        evidence_text = text[evidence.start : evidence.end].casefold()
        object_tokens = _tokens(str(candidate.get("object", "")))
        if object_tokens and not any(token in evidence_text for token in object_tokens):
            return None

    temporal_scope = (
        TemporalScope.TEMPORARY
        if assertion == AssertionType.TEMPORARY
        else TemporalScope.ONGOING
    )
    score, label, reason = calibrate_confidence(
        assertion=assertion,
        source_role=source_role,
        source_quality=str(candidate.get("source_quality", "")),
        client_auto_save=_qualifier_value(qualifiers, "save_intent") == "client_auto_save",
    )
    extraction.update(
        {
            "assertion_type": assertion.value,
            "confidence_score": score,
            "source_role": source_role,
            "temporal_scope": temporal_scope.value,
            "validation_status": ValidationStatus.VALIDATED.value,
        }
    )
    qualifiers["extraction"] = extraction
    candidate["qualifiers"] = qualifiers
    candidate["confidence"] = label
    candidate["confidence_reason"] = reason
    return candidate


def calibrate_confidence(
    *,
    assertion: AssertionType,
    source_role: str,
    source_quality: str,
    client_auto_save: bool,
) -> tuple[float, str, str]:
    if assertion == AssertionType.QUOTED:
        return 0.3, "low", "Quoted evidence is retained as non-authoritative context."
    if assertion == AssertionType.UNCERTAIN:
        return 0.4, "low", "The source statement expresses uncertainty."
    if source_quality == "inferred_from_conversation":
        return 0.55, "medium", "Inferred from recurring conversation topics."
    if source_role == "assistant":
        return 0.5, "medium", "Assistant-derived evidence requires user corroboration."
    if assertion == AssertionType.TEMPORARY:
        return 0.6, "medium", "The source statement is explicitly temporary."
    if source_quality == "corrected_by_user":
        return 0.98, "high", "Extracted from a direct user correction."
    if client_auto_save:
        return 0.65, "medium", "Extracted from client auto-save memory, so confidence is reduced."
    return 0.9, "high", "Extracted from a direct user statement."


def extraction_public_fields(qualifiers: Any) -> dict[str, Any]:
    if not isinstance(qualifiers, dict):
        return {"validation_status": ValidationStatus.LEGACY.value}
    extraction = qualifiers.get("extraction")
    if not isinstance(extraction, dict):
        return {"validation_status": ValidationStatus.LEGACY.value}
    allowed = {
        "assertion_type",
        "confidence_score",
        "evidence_span",
        "extractor_name",
        "extractor_version",
        "rule_identifier",
        "source_role",
        "temporal_scope",
        "validation_status",
    }
    return {key: extraction[key] for key in allowed if key in extraction}


def _inside_quotes(clause: str, position: int) -> bool:
    before = clause[:position]
    return before.count('"') % 2 == 1 or (
        before.rfind("“") > before.rfind("”")
        or before.rfind("‘") > before.rfind("’")
    )


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]*", value)
        if len(token) >= 3
    }


def _qualifier_value(qualifiers: Mapping[str, Any], key: str) -> str | None:
    value = qualifiers.get(key)
    return str(value) if value is not None and str(value) else None
