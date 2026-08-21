from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

from memory.config import parse_config

PINNED_UV_IMAGE = "FROM ghcr.io/astral-sh/uv:0.11.32-python3.14-trixie-slim"
PINNED_PGVECTOR_IMAGE = (
    "pgvector/pgvector:pg16"
    "@sha256:a36250871de0833b8757561c72f2477ef1ddd1101afa4e617fb552e0de514c6b"
)
LOCAL_STACK_NGROK_PLACEHOLDER = "https://YOUR-NGROK-DOMAIN.ngrok-free.app"


def test_containerfile_installs_project_after_copying_package() -> None:
    containerfile = Path("Containerfile").read_text(encoding="utf-8")

    assert PINNED_UV_IMAGE in containerfile
    assert "python -m pip install --no-cache-dir uv" not in containerfile
    dependency_sync = containerfile.index("--no-install-project")
    package_copy = containerfile.index("COPY memory ./memory")
    project_sync = containerfile.index("uv sync --frozen --no-dev", package_copy)
    console_script_check = containerfile.index("test -x /app/.venv/bin/aim")

    assert dependency_sync < package_copy < project_sync < console_script_check
    for extra in (
        "chromadb",
        "elasticsearch",
        "milvus",
        "mongodb",
        "opensearch",
        "postgres",
        "pinecone",
        "qdrant",
        "redis",
        "tokenizer",
        "turbopuffer",
        "vespa",
        "typesense",
        "weaviate",
    ):
        assert f"--extra {extra}" not in containerfile
    assert "TIKTOKEN_CACHE_DIR" not in containerfile
    assert "tiktoken.get_encoding" not in containerfile
    assert "COPY examples/container/config.yaml /app/config.yaml" in containerfile
    assert "useradd --uid 1001 --gid 0" in containerfile
    assert 'CMD ["/app/.venv/bin/aim", "serve", "--host", "0.0.0.0", "--port", "8000"]' in containerfile
    assert 'CMD ["uv", "run", "aim"' not in containerfile

    config = Path("examples/container/config.yaml").read_text(encoding="utf-8")
    assert "metadata_db: sqlite" in config
    assert "vector_db: lancedb" in config


def test_local_stack_uses_oauth_containerfile() -> None:
    compose = Path("examples/local-stack/compose.yaml").read_text(encoding="utf-8")
    containerfile = Path("examples/local-stack/Containerfile.oauth").read_text(encoding="utf-8")

    assert "dockerfile: examples/local-stack/Containerfile.oauth" in compose
    assert "GOOGLE_CLIENT_ID: ${GOOGLE_CLIENT_ID:-}" in compose
    assert "GOOGLE_CLIENT_SECRET: ${GOOGLE_CLIENT_SECRET:-}" in compose
    assert "AMH_OAUTH_JWT_SECRET: ${AMH_OAUTH_JWT_SECRET:-}" in compose
    assert "AMH_SESSION_SECRET: ${AMH_SESSION_SECRET:-}" in compose
    assert "./${AMH_CONFIG_FILE:-config.yaml}:/app/config.yaml:ro,Z" in compose
    assert f"image: {PINNED_PGVECTOR_IMAGE}" in compose
    assert "image: pgvector/pgvector:pg16\n" not in compose
    assert "http://127.0.0.1:8000/ready" in compose
    assert PINNED_UV_IMAGE in containerfile
    assert "python -m pip install --no-cache-dir uv" not in containerfile
    assert "--extra postgres" in containerfile
    assert "--extra tokenizer" in containerfile
    assert "--extra oauth" in containerfile
    assert "--extra observability" in containerfile
    assert "uv pip install --python /app/.venv/bin/python --no-deps --reinstall ." in containerfile
    assert 'cd /tmp && /app/.venv/bin/python -c "import memory; import memory.cli"' in containerfile
    assert (
        'CMD ["/app/.venv/bin/aim", "serve", "--config", "/app/config.yaml", '
        '"--host", "0.0.0.0", "--port", "8000"]'
    ) in containerfile
    assert 'CMD ["uv", "run", "aim"' not in containerfile
    for extra in (
        "chromadb",
        "elasticsearch",
        "milvus",
        "mongodb",
        "opensearch",
        "pinecone",
        "qdrant",
        "redis",
        "turbopuffer",
        "vespa",
        "typesense",
        "weaviate",
    ):
        assert f"--extra {extra}" not in containerfile

    oauth_config = Path(
        "examples/local-stack/config.oauth-ngrok.yaml"
    ).read_text(encoding="utf-8")
    assert "auth: oauth_resource_server" in oauth_config
    assert "embedding_model: nomic-embed-text" in oauth_config
    assert "base_url: http://host.docker.internal:11434/v1" in oauth_config


def test_local_stack_google_oauth_config_parses_after_public_url_substitution() -> None:
    public_base_url = "https://memory-dev.example.test"
    oauth_template = Path("examples/local-stack/config.oauth-ngrok.yaml").read_text(
        encoding="utf-8"
    )
    oauth_config = yaml.safe_load(
        oauth_template.replace(LOCAL_STACK_NGROK_PLACEHOLDER, public_base_url)
    )

    config = parse_config(oauth_config)

    assert config.api.auth == "oauth_resource_server"
    assert config.api.public_base_url == public_base_url
    assert config.api.oauth.authorization_servers == [public_base_url]
    assert config.api.oauth.resource == f"{public_base_url}/mcp"
    assert config.api.oauth.jwt_secret_env == "AMH_OAUTH_JWT_SECRET"
    assert config.api.connect.enabled is True
    assert config.api.connect.session_secret_env == "AMH_SESSION_SECRET"
    assert config.api.connect.passport.providers == ["google"]
    assert config.api.connect.passport.google.enabled is True
    assert config.api.connect.passport.google.callback_url == (
        f"{public_base_url}/auth/google/callback"
    )
    assert config.api.connect.passport.google.client_id_env == "GOOGLE_CLIENT_ID"
    assert (
        config.api.connect.passport.google.client_secret_env == "GOOGLE_CLIENT_SECRET"
    )
    assert config.providers.embeddings == "http"
    assert config.providers.metadata_db == "postgres"
    assert config.providers.vector_db == "pgvector"
    assert config.embedding_endpoint.base_url == "http://host.docker.internal:11434/v1"


def test_postgres_service_images_are_digest_pinned() -> None:
    for workflow_path in (
        Path(".github/workflows/pipeline.yml"),
        Path(".github/workflows/bruno-integration.yml"),
    ):
        workflow = workflow_path.read_text(encoding="utf-8")

        assert f"image: {PINNED_PGVECTOR_IMAGE}" in workflow
        assert "image: pgvector/pgvector:pg16\n" not in workflow


def test_google_oauth_example_uses_uv_base_image() -> None:
    containerfile = Path("examples/google-oauth-connect/Containerfile").read_text(
        encoding="utf-8"
    )

    assert PINNED_UV_IMAGE in containerfile
    assert "python -m pip install --no-cache-dir uv" not in containerfile
    assert "--extra oauth" in containerfile
    assert "--no-install-project" in containerfile
    assert "test -x /app/.venv/bin/aim" in containerfile
    assert (
        'CMD ["/app/.venv/bin/aim", "serve", "--config", "/app/config.yaml", '
        '"--host", "0.0.0.0", "--port", "8000"]'
    ) in containerfile
    assert 'CMD ["uv", "run", "aim"' not in containerfile


def test_free_provider_examples_use_provider_local_containerfiles() -> None:
    expected_extras = {
        "mongodb": {"mongodb"},
        "redis": {"redis"},
        "sqlite-lancedb": set(),
    }
    all_optional_extras = {
        "elasticsearch",
        "milvus",
        "mongodb",
        "opensearch",
        "pinecone",
        "postgres",
        "qdrant",
        "redis",
        "tokenizer",
        "turbopuffer",
        "vespa",
        "typesense",
        "weaviate",
    }

    for example, enabled_extras in expected_extras.items():
        example_dir = Path("examples/storage_providers") / example
        compose = (example_dir / "compose.yaml").read_text(encoding="utf-8")
        containerfile = (example_dir / "Containerfile").read_text(encoding="utf-8")

        assert f"dockerfile: examples/storage_providers/{example}/Containerfile" in compose
        assert PINNED_UV_IMAGE in containerfile
        assert "python -m pip install --no-cache-dir uv" not in containerfile
        assert 'CMD ["/app/.venv/bin/aim", "serve", "--host", "0.0.0.0", "--port", "8000"]' in containerfile
        assert 'CMD ["uv", "run", "aim"' not in containerfile
        for extra in enabled_extras:
            assert f"--extra {extra}" in containerfile
        for extra in all_optional_extras - enabled_extras:
            assert f"--extra {extra}" not in containerfile


def test_local_stack_observability_is_wired() -> None:
    compose = Path("examples/local-stack/compose.yaml").read_text(encoding="utf-8")
    config = Path("examples/local-stack/config.oauth-ngrok.yaml").read_text(encoding="utf-8")
    collector = Path("examples/local-stack/otel-collector.yaml").read_text(encoding="utf-8")
    prometheus = Path("examples/local-stack/prometheus.yaml").read_text(encoding="utf-8")

    assert "otel-collector" in compose
    assert "jaeger" in compose
    assert "prometheus" in compose
    assert "127.0.0.1:16686:16686" in compose
    assert "127.0.0.1:9090:9090" in compose
    assert "http://127.0.0.1:8000/ready" in compose
    assert "tracing:" in config
    assert "metrics:" in config
    assert "endpoint: http://otel-collector:4317" in config
    assert "otlp/jaeger" in collector
    assert "otel-collector:8889" in prometheus


def test_project_declares_build_backend_for_console_script() -> None:
    with Path("pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    assert pyproject["build-system"]["build-backend"] == "hatchling.build"
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "memory"
    ]


def test_container_smoke_retains_stopped_container_for_logs() -> None:
    workflow = Path(".github/workflows/pipeline.yml").read_text(encoding="utf-8")

    run_command = next(
        line for line in workflow.splitlines() if "ai-memory-hub-ci" in line and "docker run" in line
    )
    assert "--rm" not in run_command
    assert "127.0.0.1:8000:8000" in run_command
    assert ".State.ExitCode" in workflow
    assert "docker exec ai-memory-hub-ci" in workflow
    assert "docker run --rm --user 12345:0 --entrypoint sh" in workflow
    assert "test -w /app/.uv-cache" in workflow
    assert "TIKTOKEN_CACHE_DIR" not in workflow


def test_compose_example_smoke_exercises_default_and_oauth_configs() -> None:
    workflow = Path(".github/workflows/pipeline.yml").read_text(encoding="utf-8")

    assert "name: Compose Example Smoke" in workflow
    assert "docker compose -f \"$COMPOSE_FILE\" config" in workflow
    assert "Smoke default local stack" in workflow
    assert "amber-vector" in workflow
    assert "Smoke OAuth/Ollama local stack" in workflow
    assert "config.oauth-ci.yaml" in workflow
    assert 'example_dir="$(dirname "$COMPOSE_FILE")"' in workflow
    assert "PUBLIC_BASE_URL:" in workflow
    assert '"iss": "https://ci-token-issuer.example.test"' in workflow
    assert "ollama/ollama:0.22.1@sha256:" in workflow
    assert "docker exec ollama-compose-ci ollama pull nomic-embed-text" in workflow
    assert "compose OAuth smoke phrase is blue-lantern" in workflow
    assert "Authorization: Bearer $CI_OAUTH_TOKEN" in workflow
    assert 'test "$unauth_status" = "401" -o "$unauth_status" = "403"' in workflow


def test_supply_chain_workflow_blocks_high_and_critical_vulnerabilities() -> None:
    workflow = Path(".github/workflows/supply-chain.yml").read_text(encoding="utf-8")

    assert "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0" in workflow
    report_start = workflow.index("Write Trivy image scan report")
    sbom_start = workflow.index("Generate CycloneDX SBOM")
    upload_start = workflow.index("Upload supply-chain artifacts")
    scan_start = workflow.index("Run Trivy image scan")
    scan_block = workflow[scan_start:]
    assert report_start < sbom_start < upload_start < scan_start
    assert "severity: CRITICAL,HIGH" in scan_block
    assert "exit-code: \"1\"" in scan_block
    assert "exit-code: \"0\"" in workflow
    assert "format: cyclonedx" in workflow
    assert "ai-memory-hub.cdx.json" in workflow
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7" in workflow


def test_dependency_review_workflow_blocks_disallowed_dependency_changes() -> None:
    workflow_text = Path(".github/workflows/dependency-review.yml").read_text(encoding="utf-8")
    workflow = yaml.safe_load(workflow_text)
    steps = workflow["jobs"]["dependency-review"]["steps"]
    review_step = next(
        step
        for step in steps
        if step.get("uses")
        == "actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294"
    )
    review_config = review_step["with"]

    assert "actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294 # v5.0.0" in workflow_text
    assert review_config["warn-only"] is False
    assert review_config["vulnerability-check"] is True
    assert review_config["license-check"] is True


def test_storage_provider_live_jobs_wait_for_services() -> None:
    workflow = Path(".github/workflows/storage-providers.yml").read_text(encoding="utf-8")

    mongodb_wait = workflow.index("Wait for MongoDB")
    mongodb_test = workflow.index("Run MongoDB live test")
    assert mongodb_wait < mongodb_test
    assert "MongoClient('mongodb://127.0.0.1:27017'" in workflow
    assert ".admin.command('ping')" in workflow


def test_bruno_oauth_discovery_check_fails_with_diagnostics() -> None:
    workflow = Path(".github/workflows/bruno-integration.yml").read_text(encoding="utf-8")

    oauth_start = workflow.index("Verify OAuth resource-server challenge")
    auth_start = workflow.index("Seed bearer auth users and shared project")
    oauth_block = workflow[oauth_start:auth_start]
    assert "ready=false" in oauth_block
    assert "ready=true" in oauth_block
    assert 'if [ "$ready" != "true" ]; then' in oauth_block
    assert "challenge_status" in oauth_block
    assert 'test "$challenge_status" = "401"' in oauth_block
    assert "resource_metadata=" in oauth_block
    assert "metadata_status" in oauth_block
    assert 'test "$metadata_status" = "200"' in oauth_block
    assert "cat bruno-oauth-server.log" in oauth_block
    assert "rm bruno-oauth-server.pid" in oauth_block


def test_docker_publish_attests_published_image() -> None:
    workflow = Path(".github/workflows/docker-publish.yml").read_text(encoding="utf-8")

    assert "attestations: write" in workflow
    assert "artifact-metadata: write" in workflow
    assert "contents: write" in workflow
    assert "actions/attest@f6bf1532d7d6793fce74eac584813a8eee607999 # v4" in workflow
    assert "push-to-registry: true" in workflow
    assert "Checkout requested release tag" in workflow
    assert 'git checkout --detach "refs/tags/${RELEASE_TAG}"' in workflow
    assert "Build local image for release scan" in workflow
    assert "Scan release image before push" in workflow
    assert "exit-code: \"1\"" in workflow
    assert "Smoke published image" in workflow
    assert '"${IMAGE}@${DIGEST}"' in workflow
    assert "127.0.0.1:8000:8000" in workflow
    assert "curl -fsS http://127.0.0.1:8000/ready" in workflow
    assert "Update release notes with published image digest" in workflow
    assert "gh release edit \"$RELEASE_TAG\" --notes-file release-notes.md" in workflow
    assert "ai-memory-hub-docker-digest:start" in workflow


def test_release_readiness_and_codeql_workflows_exist() -> None:
    release_readiness = Path(".github/workflows/release-readiness.yml").read_text(
        encoding="utf-8"
    )
    codeql = Path(".github/workflows/codeql.yml").read_text(encoding="utf-8")

    assert "name: release-readiness" in release_readiness
    assert "uv run python -m ruff check memory tests tools" in release_readiness
    assert "uv run python -m pyright" in release_readiness
    assert "uv run python tests/bruno/validate_files.py" in release_readiness
    assert "python -m pip install -r docs/requirements.txt" in release_readiness
    assert "mkdocs build --strict" in release_readiness
    assert "tools/validate_release_version.py" in release_readiness
    assert "github/codeql-action/init@1ad29ea4a422cce9a242a9fae469541dcd08addc # v4" in codeql
    assert "security-events: write" in codeql


def test_workflows_pin_third_party_actions_to_shas() -> None:
    for workflow_path in Path(".github/workflows").glob("*.yml"):
        workflow = workflow_path.read_text(encoding="utf-8")
        assert not re.search(r"uses:\s+[^#\s]+@v[0-9]", workflow), workflow_path


def test_e2e_ollama_uses_pinned_container_instead_of_install_script() -> None:
    workflow = Path(".github/workflows/pipeline.yml").read_text(encoding="utf-8")

    assert "curl -fsSL https://ollama.com/install.sh" not in workflow
    assert "OLLAMA_IMAGE: >-" in workflow
    assert "ollama/ollama:0.22.1@sha256:" in workflow
    assert "3ca37ec2b9cb6341b62554074205c616778fe98abcf9e4fc50361b79a07407ae" in workflow
    assert "docker run -d" in workflow
    assert "docker exec ollama-ci ollama pull nomic-embed-text" in workflow
    assert "docker rm -f ollama-ci" in workflow


def test_real_client_smoke_runs_on_prs_and_pushes() -> None:
    workflow = Path(".github/workflows/real-client-mcp-smoke.yml").read_text(
        encoding="utf-8"
    )

    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "name: Real-Client MCP Smoke" in workflow
