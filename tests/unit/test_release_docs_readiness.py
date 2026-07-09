from pathlib import Path


def test_readme_first_screen_positions_project_for_release() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    first_screen = "\n".join(readme.splitlines()[:24])

    assert "Local-first memory for AI agents." in first_screen
    assert "Codex, opencode, Claude, Copilot" in first_screen
    assert "shared memory backend through MCP and HTTP" in first_screen


def test_readme_quick_start_has_source_and_docker_paths() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "git clone https://github.com/Artemon-line/ai-memory-hub.git" in readme
    assert "uv sync --dev" in readme
    assert "uv run aim serve --host 127.0.0.1 --port 8000" in readme
    assert "curl -X POST http://127.0.0.1:8000/memory/insert" in readme
    assert "docker build -t ai-memory-hub:local -f Containerfile ." in readme
    assert "docker run --rm -p 127.0.0.1:8000:8000 ai-memory-hub:local" in readme
    assert "cd examples/storage_providers/postgres-pgvector" in readme
    assert "docker compose up --build" in readme


def test_readme_mcp_and_security_guidance_are_visible_before_lan_docs() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    mcp_index = readme.index("MCP endpoint:")
    auth_index = readme.index("Before exposing")
    exposure_guidance_index = readme.index("Before exposing\nit beyond loopback")

    assert mcp_index < exposure_guidance_index
    assert auth_index == exposure_guidance_index
    assert "api.auth: oauth_resource_server" in readme


def test_readme_known_first_release_limits_are_explicit() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    limitations = readme.index("Known first-release limits")
    assert "does not ship as a hosted memory" in readme[limitations:]
    assert "bring-your-own embedding model" in readme[limitations:]
    assert "browser extensions are planned as separate adapters" in readme[limitations:]
    assert "UI dashboards" in readme[limitations:]
    assert "SDKs are\nfuture work" in readme[limitations:]


def test_release_assets_do_not_market_unreleased_surfaces_as_shipped() -> None:
    assets = Path("docs/release_promotion_assets.md").read_text(encoding="utf-8")

    assert "do not advertise unreleased browser" in assets
    assert "browser extensions are future adapters" in assets
    assert "UI dashboards and SDKs are future work" in assets
    assert "What ships:" in assets
