from __future__ import annotations

from pathlib import Path
import tomllib


def test_containerfile_installs_project_after_copying_package() -> None:
    containerfile = Path("Containerfile").read_text(encoding="utf-8")

    dependency_sync = containerfile.index("--no-install-project")
    package_copy = containerfile.index("COPY memory ./memory")
    project_sync = containerfile.index(
        "uv sync --frozen --no-dev --extra postgres --extra tokenizer &&",
        package_copy,
    )
    console_script_check = containerfile.index("test -x /app/.venv/bin/aim")

    assert dependency_sync < package_copy < project_sync < console_script_check
    assert "COPY examples/container/config.yaml /app/config.yaml" in containerfile
    assert "useradd --uid 1001 --gid 0" in containerfile


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
