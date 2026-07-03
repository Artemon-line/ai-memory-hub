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
        "Build Documentation",
        "Bruno API/MCP Integration",
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
