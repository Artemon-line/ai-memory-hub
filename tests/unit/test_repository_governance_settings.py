from pathlib import Path


def test_repository_governance_required_checks_match_release_plan() -> None:
    governance = Path("docs/repository_governance_settings.md").read_text(encoding="utf-8")
    readiness = Path("docs/first_release_readiness_plan.md").read_text(encoding="utf-8")

    required_checks = [
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
    ]

    for check in required_checks:
        assert check in governance
        assert check in readiness


def test_repository_governance_includes_release_discoverability_topics() -> None:
    governance = Path("docs/repository_governance_settings.md").read_text(encoding="utf-8")
    promotion_assets = Path("docs/release_promotion_assets.md").read_text(encoding="utf-8")

    topics = [
        "mcp",
        "ai-agents",
        "memory",
        "rag",
        "fastapi",
        "pgvector",
        "local-first",
        "openai-compatible",
    ]

    for topic in topics:
        assert topic in governance
        assert topic in promotion_assets


def test_repository_governance_documents_external_admin_steps() -> None:
    governance = Path("docs/repository_governance_settings.md").read_text(encoding="utf-8")

    assert "require repository admin access" in governance
    assert "DOCKERHUB_USERNAME" in governance
    assert "DOCKERHUB_TOKEN" in governance
    assert "Pages source: GitHub Actions" in governance
    assert "did not update `latest`" in governance


def test_release_ci_gap_analysis_tracks_remaining_manual_work() -> None:
    gap_analysis = Path("docs/release_ci_gap_analysis.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Release Readiness" in gap_analysis
    assert "CodeQL Analysis" in gap_analysis
    assert "requested release tag" in gap_analysis
    assert "blocks release publishing on" in gap_analysis
    assert "real Codex/opencode/Claude/Copilot/Gemini client behavior" in gap_analysis
    assert "Release CI gap analysis" in readme


def test_release_security_scan_notes_track_fixed_and_remaining_findings() -> None:
    security_notes = Path("docs/release_security_scan_notes.md").read_text(
        encoding="utf-8"
    )
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "best-effort release security scan" in security_notes
    assert "MCP write tools now require `memory:write`" in security_notes
    assert "LanceDB delete/replace filters" in security_notes
    assert "pin `uv==0.10.3`" in security_notes
    assert "GitHub Actions are version-tag pinned" in security_notes
    assert "Release security scan notes" in readme
