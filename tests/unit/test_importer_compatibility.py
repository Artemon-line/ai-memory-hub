from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from memory.importers.compatibility import IMPORTER_COMPATIBILITY, EvidenceReference
from memory.importers.compatibility_report import (
    CompatibilityMarkdownRenderer,
    CompatibilityMatrixValidator,
)
from memory.importers.registry import importer_names

_ROOT = Path(__file__).parents[2]


def test_compatibility_matrix_covers_registry_once_with_existing_evidence() -> None:
    errors = CompatibilityMatrixValidator(_ROOT, importer_names()).validate(IMPORTER_COMPATIBILITY)

    assert errors == ()
    assert sorted(entry.importer for entry in IMPORTER_COMPATIBILITY) == list(importer_names())


def test_compatibility_matrix_rejects_duplicate_missing_and_extra_importers() -> None:
    first = IMPORTER_COMPATIBILITY[0]
    unknown = replace(first, importer="unknown")

    errors = CompatibilityMatrixValidator(_ROOT, importer_names()).validate(
        (*IMPORTER_COMPATIBILITY[1:], first, first, unknown)
    )

    assert "matrix entries must be unique: claude-code-session-jsonl" in errors
    assert "matrix entries without registered importers: unknown" in errors


def test_compatibility_matrix_rejects_missing_local_references_and_symbols() -> None:
    entry = replace(
        IMPORTER_COMPATIBILITY[0],
        fixtures=(EvidenceReference("tests/fixtures/importers/missing.json"),),
        tests=(EvidenceReference("tests/unit/test_importers.py", "test_missing"),),
    )

    errors = CompatibilityMatrixValidator(_ROOT, (entry.importer,)).validate((entry,))

    assert errors == (
        "claude-code-session-jsonl: missing evidence file: tests/fixtures/importers/missing.json",
        "claude-code-session-jsonl: missing symbol test_missing in tests/unit/test_importers.py",
    )


def test_compatibility_matrix_requires_version_and_date_together() -> None:
    entry = replace(
        IMPORTER_COMPATIBILITY[0],
        verified_client_version="Example 1.0",
        verification_date=None,
    )

    errors = CompatibilityMatrixValidator(_ROOT, (entry.importer,)).validate((entry,))

    assert errors == (
        "claude-code-session-jsonl: verified client version and date must be set together",
    )


def test_generated_compatibility_report_is_current() -> None:
    expected = CompatibilityMarkdownRenderer().render(IMPORTER_COMPATIBILITY)

    assert (_ROOT / "docs" / "importer_compatibility.md").read_text(encoding="utf-8") == expected
