from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from memory.import_discovery import DISCOVERY_PROVIDERS, build_discovery_service
from memory.importer_compatibility import IMPORTER_COMPATIBILITY, SourceClient
from memory.importer_compatibility_report import (
    CompatibilityMarkdownRenderer,
    CompatibilityMatrixValidator,
    DiscoveryMatrixValidator,
    ReleaseContext,
)
from memory.importers.registry import importer_names
from tools.prepare_mkdocs import prepare_docs

_ROOT = Path(__file__).parents[2]


def test_compatibility_matrix_covers_registry_and_every_client_exactly_once() -> None:
    errors = CompatibilityMatrixValidator(_ROOT, importer_names()).validate(IMPORTER_COMPATIBILITY)

    assert errors == ()
    assert sorted(entry.importer.value for entry in IMPORTER_COMPATIBILITY) == list(
        importer_names()
    )
    pinned_clients = [pin.client for entry in IMPORTER_COMPATIBILITY for pin in entry.pins]
    assert sorted(pinned_clients, key=lambda client: client.value) == sorted(
        SourceClient,
        key=lambda client: client.value,
    )


def test_compatibility_matrix_rejects_duplicate_and_missing_entries() -> None:
    first = IMPORTER_COMPATIBILITY[0]
    registered_without_last = importer_names()[:-1]

    errors = CompatibilityMatrixValidator(_ROOT, registered_without_last).validate(
        (*IMPORTER_COMPATIBILITY, first)
    )

    assert "matrix entries must be unique: claude-code-session-jsonl" in errors
    assert "matrix entries without registered importers: qwen-code-session-export" in errors
    assert "source clients must have exactly one version pin: Claude Code" in errors


def test_compatibility_matrix_rejects_missing_fixture_and_test_file(tmp_path: Path) -> None:
    entry = replace(IMPORTER_COMPATIBILITY[0], tests=())

    errors = CompatibilityMatrixValidator(tmp_path, (entry.importer.value,)).validate((entry,))

    assert "missing focused test file: tests\\unit\\test_importers.py" in errors or (
        "missing focused test file: tests/unit/test_importers.py" in errors
    )
    assert any("missing fixture" in error for error in errors)
    assert any("at least one focused test" in error for error in errors)


def test_renderer_includes_release_identity_and_typed_pins() -> None:
    report = CompatibilityMarkdownRenderer().render(
        IMPORTER_COMPATIBILITY,
        ReleaseContext(version="9.8.7", generated_on=date(2026, 10, 18)),
    )

    assert "Generated for ai-memory-hub `9.8.7` on 2026-10-18." in report
    assert (
        "| `codex-rollout-jsonl` | Codex CLI and app | Exact client release | `0.152.0` |" in report
    )
    assert (
        "| `pi-session-jsonl` | OpenClaw | Format version | `openclaw-legacy-session-v3` |"
        in report
    )
    assert "## Latest-session discovery providers" in report
    assert (
        "| `copilot-cli` | `copilot-cli-events-jsonl` | `COPILOT_HOME` | `~/.copilot` |"
        in report
    )


def test_discovery_matrix_covers_every_adapter_and_registered_importer() -> None:
    errors = DiscoveryMatrixValidator(_ROOT, importer_names()).validate(
        DISCOVERY_PROVIDERS,
        build_discovery_service(environment={}).sources,
    )

    assert errors == ()


def test_docs_preparation_generates_matrix_without_checked_in_copy(tmp_path: Path) -> None:
    output_dir = tmp_path / "prepared"

    prepare_docs(_ROOT, output_dir)

    report = (output_dir / "docs" / "importer_compatibility.md").read_text(encoding="utf-8")
    assert "Generated for ai-memory-hub `1.0.0` on " in report
    assert not (_ROOT / "docs" / "importer_compatibility.md").exists()


def test_release_workflow_generates_and_attaches_matrix() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(encoding="utf-8")

    assert "run: python tools/prepare_mkdocs.py" in workflow
    assert 'gh release upload "$RELEASE_TAG"' in workflow
    assert ".docs-build/docs/importer_compatibility.md" in workflow
