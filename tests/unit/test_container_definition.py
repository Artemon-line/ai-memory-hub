from __future__ import annotations

import tomllib
from pathlib import Path


def test_containerfile_installs_project_after_copying_package() -> None:
    containerfile = Path("Containerfile").read_text(encoding="utf-8")

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
        "observability",
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
        assert f"--extra {extra}" in containerfile
    assert "COPY examples/container/config.yaml /app/config.yaml" in containerfile
    assert "useradd --uid 1001 --gid 0" in containerfile
    assert 'CMD ["/app/.venv/bin/aim", "serve", "--host", "0.0.0.0", "--port", "8000"]' in containerfile
    assert 'CMD ["uv", "run", "aim"' not in containerfile


def test_pgvector_example_uses_slim_containerfile() -> None:
    compose = Path("examples/storage_providers/postgres-pgvector/compose.yaml").read_text(
        encoding="utf-8"
    )
    containerfile = Path("examples/storage_providers/postgres-pgvector/Containerfile").read_text(
        encoding="utf-8"
    )

    assert "dockerfile: examples/storage_providers/postgres-pgvector/Containerfile" in compose
    assert "http://127.0.0.1:8000/ready" in compose
    assert "--extra postgres" in containerfile
    assert "--extra tokenizer" in containerfile
    assert 'CMD ["/app/.venv/bin/aim", "serve", "--host", "0.0.0.0", "--port", "8000"]' in containerfile
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


def test_free_provider_examples_use_provider_local_containerfiles() -> None:
    expected_extras = {
        "chromadb": {"chromadb"},
        "mongodb": {"mongodb"},
        "redis": {"redis"},
        "sqlite-lancedb": set(),
    }
    all_optional_extras = {
        "chromadb",
        "elasticsearch",
        "milvus",
        "mongodb",
        "opensearch",
        "observability",
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
        assert 'CMD ["/app/.venv/bin/aim", "serve", "--host", "0.0.0.0", "--port", "8000"]' in containerfile
        assert 'CMD ["uv", "run", "aim"' not in containerfile
        for extra in enabled_extras:
            assert f"--extra {extra}" in containerfile
        for extra in all_optional_extras - enabled_extras:
            assert f"--extra {extra}" not in containerfile


def test_observability_compose_profile_is_wired() -> None:
    compose = Path("examples/observability/compose.yaml").read_text(encoding="utf-8")
    config = Path("examples/observability/config.yaml").read_text(encoding="utf-8")
    collector = Path("examples/observability/otel-collector.yaml").read_text(
        encoding="utf-8"
    )
    prometheus = Path("examples/observability/prometheus.yaml").read_text(
        encoding="utf-8"
    )

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
        line for line in workflow.splitlines() if "docker run" in line
    )
    assert "--rm" not in run_command
    assert ".State.ExitCode" in workflow
    assert "docker exec ai-memory-hub-ci" in workflow
    assert "docker run --rm --user 12345:0 --entrypoint sh" in workflow


def test_supply_chain_workflow_scans_without_blocking_prs() -> None:
    workflow = Path(".github/workflows/supply-chain.yml").read_text(encoding="utf-8")

    assert "aquasecurity/trivy-action@v0.36.0" in workflow
    assert "exit-code: \"0\"" in workflow
    assert "format: cyclonedx" in workflow
    assert "ai-memory-hub.cdx.json" in workflow
    assert "actions/upload-artifact@v7" in workflow


def test_dependency_review_workflow_reports_in_warning_mode() -> None:
    workflow = Path(".github/workflows/dependency-review.yml").read_text(encoding="utf-8")

    assert "actions/dependency-review-action@v5" in workflow
    assert "warn-only: true" in workflow
    assert "vulnerability-check: true" in workflow


def test_docker_publish_attests_published_image() -> None:
    workflow = Path(".github/workflows/docker-publish.yml").read_text(encoding="utf-8")

    assert "attestations: write" in workflow
    assert "artifact-metadata: write" in workflow
    assert "actions/attest@v4" in workflow
    assert "push-to-registry: true" in workflow
