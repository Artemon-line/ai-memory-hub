import re
from pathlib import Path


def _section(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"^## {re.escape(heading)}\s*$([\s\S]*?)(?=^## |\Z)",
        re.MULTILINE,
    )
    match = pattern.search(markdown)
    assert match is not None, f"missing section: {heading}"
    return match.group(1)


def _fenced_text_block_after(markdown: str, label: str) -> list[str]:
    pattern = re.compile(
        rf"{re.escape(label)}\s*:\s*```text\s*(.*?)\s*```",
        re.DOTALL,
    )
    match = pattern.search(markdown)
    assert match is not None, f"missing fenced text block after: {label}"
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def _backticked_items(markdown: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", markdown))


def test_markdown_section_helpers_ignore_unrelated_mentions() -> None:
    markdown = """# Doc

## Historical Notes

Required status checks for `main`:

```text
Unit and Integration Tests
```

## Branch Protection

Required status checks for `main`:

```text
Release Readiness
```
"""

    branch_protection = _section(markdown, "Branch Protection")

    assert _fenced_text_block_after(
        branch_protection, "Required status checks for `main`"
    ) == ["Release Readiness"]


def test_repository_governance_required_checks_match_release_plan() -> None:
    governance = Path("docs/repository_governance_settings.md").read_text(encoding="utf-8")
    readiness = Path("docs/first_release_readiness_plan.md").read_text(encoding="utf-8")

    branch_protection = _section(governance, "Branch Protection")
    required_checks = _fenced_text_block_after(
        branch_protection, "Required status checks for `main`"
    )
    readiness_check_items = _backticked_items(
        _section(readiness, "Current State")
    ) | _backticked_items(_section(readiness, "P0: Repository Governance"))
    expected_checks = [
        "Unit and Integration Tests",
        "E2E Scenario (Ollama)",
        "Storage Config Variations",
        "Storage Postgres Integration",
        "Containerfile Lint",
        "Container Build and Smoke",
        "Release Readiness",
        "Build Documentation",
        "Bruno API/MCP Integration",
        "Dependency Review",
        "Image Scan and SBOM",
        "CodeQL Analysis",
        "Real-Client MCP Smoke",
    ]

    assert required_checks == expected_checks
    assert set(required_checks) <= readiness_check_items


def test_repository_governance_includes_release_discoverability_topics() -> None:
    governance = Path("docs/repository_governance_settings.md").read_text(encoding="utf-8")
    promotion_assets = Path("docs/release_promotion_assets.md").read_text(encoding="utf-8")

    governance_topics = _fenced_text_block_after(
        _section(governance, "Repository Description And Topics"), "Topics"
    )
    promotion_topics = _fenced_text_block_after(
        _section(promotion_assets, "GitHub Repository Settings"), "Recommended topics"
    )
    expected_topics = [
        "mcp",
        "ai-agents",
        "memory",
        "rag",
        "fastapi",
        "pgvector",
        "local-first",
        "openai-compatible",
    ]

    assert governance_topics == expected_topics
    assert promotion_topics == expected_topics


def test_repository_governance_documents_external_admin_steps() -> None:
    governance = Path("docs/repository_governance_settings.md").read_text(encoding="utf-8")

    docker_secrets = set(
        _fenced_text_block_after(
            _section(governance, "Docker Hub Secrets"),
            "Add these GitHub Actions secrets",
        )
    )
    pages_settings = _section(governance, "GitHub Pages")
    release_candidate_checks = _section(governance, "Release Candidate Checks")

    assert "require repository admin access" in governance.split(
        "## Repository Description And Topics", maxsplit=1
    )[0]
    assert {"DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN"} <= docker_secrets
    assert "- Pages source: GitHub Actions." in pages_settings
    assert "did not update `latest`" in release_candidate_checks


def test_release_ci_gap_analysis_tracks_remaining_manual_work() -> None:
    gap_analysis = Path("docs/release_ci_gap_analysis.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Release Readiness" in gap_analysis
    assert "CodeQL Analysis" in gap_analysis
    assert "requested release tag" in gap_analysis
    assert "blocks release publishing on" in gap_analysis
    assert "Updates existing GitHub release notes" in gap_analysis
    assert "Runs on pull requests, pushes to `main`" in gap_analysis
    assert "Release CI gap analysis" in readme


def test_release_security_scan_notes_track_fixed_and_remaining_findings() -> None:
    security_notes = Path("docs/release_security_scan_notes.md").read_text(
        encoding="utf-8"
    )
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "best-effort release security scan" in security_notes
    assert "MCP write tools now require `memory:write`" in security_notes
    assert "LanceDB delete/replace filters" in security_notes
    assert "ghcr.io/astral-sh/uv:0.11.32-python3.14-trixie-slim" in security_notes
    assert "GitHub Actions are commit-SHA pinned" in security_notes
    assert "JSON schema validation now enforces declared formats" in security_notes
    assert "Release security scan notes" in readme
