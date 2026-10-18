from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from memory.import_discovery import (
    DISCOVERY_PROVIDERS,
    DiscoveryError,
    DiscoverySource,
    build_discovery_service,
    default_root_for,
)

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "importers"
_NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)

_PROVIDER_CASES = (
    (
        "codex",
        "CODEX_HOME",
        Path("sessions/2026/04/12/rollout-synthetic.jsonl"),
        "codex_rollout_anonymized.jsonl",
        "0198f000-0000-7000-8000-000000000001",
    ),
    (
        "claude-code",
        "CLAUDE_CONFIG_DIR",
        Path("projects/project-hash/claude-session-synthetic.jsonl"),
        "claude_code_session_anonymized.jsonl",
        "claude-session-synthetic",
    ),
    (
        "copilot-cli",
        "COPILOT_HOME",
        Path("session-state/copilot-session-synthetic/events.jsonl"),
        "copilot_cli_events_v1_anonymized.jsonl",
        "copilot-session-synthetic",
    ),
    (
        "pi",
        "PI_CODING_AGENT_DIR",
        Path("sessions/project-hash/pi-session-synthetic.jsonl"),
        "pi_session_anonymized.jsonl",
        "pi-session-synthetic",
    ),
    (
        "oh-my-pi",
        "PI_CODING_AGENT_DIR",
        Path("sessions/project-hash/omp-session-synthetic.jsonl"),
        "oh_my_pi_session_anonymized.jsonl",
        "omp-session-synthetic",
    ),
)


@pytest.mark.parametrize(
    ("source", "environment_variable", "relative_path", "fixture", "session_id"),
    _PROVIDER_CASES,
)
def test_discovery_provider_matrix_finds_latest_safe_artifact(
    tmp_path: Path,
    source: str,
    environment_variable: str,
    relative_path: Path,
    fixture: str,
    session_id: str,
) -> None:
    configured_root = tmp_path / source
    artifact = configured_root / relative_path
    artifact.parent.mkdir(parents=True)
    shutil.copyfile(_FIXTURES / fixture, artifact)
    old_time = (_NOW - timedelta(minutes=10)).timestamp()
    os.utime(artifact, (old_time, old_time))
    service = build_discovery_service(
        environment={environment_variable: str(configured_root)}, home=tmp_path / "home"
    )

    discovered = service.discover_latest(source, now=_NOW)

    assert discovered.path == artifact.resolve()
    assert discovered.session_id == session_id
    assert discovered.importer in {
        "codex-rollout-jsonl",
        "claude-code-session-jsonl",
        "copilot-cli-events-jsonl",
        "pi-session-jsonl",
    }
    service.verify_unchanged(discovered)


def test_recent_non_terminal_artifact_is_refused_as_active(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    artifact = root / "sessions/2026/04/12/rollout-active.jsonl"
    artifact.parent.mkdir(parents=True)
    shutil.copyfile(_FIXTURES / "codex_rollout_anonymized.jsonl", artifact)
    recent = (_NOW - timedelta(seconds=30)).timestamp()
    os.utime(artifact, (recent, recent))
    service = build_discovery_service(environment={"CODEX_HOME": str(root)}, home=tmp_path)

    with pytest.raises(DiscoveryError, match="may still be active"):
        service.discover_latest("codex", now=_NOW)


def test_newer_active_artifact_does_not_fall_back_to_older_safe_session(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    sessions = root / "sessions/2026/04/12"
    sessions.mkdir(parents=True)
    old_artifact = sessions / "rollout-old.jsonl"
    shutil.copyfile(_FIXTURES / "codex_rollout_anonymized.jsonl", old_artifact)
    old_time = (_NOW - timedelta(minutes=10)).timestamp()
    os.utime(old_artifact, (old_time, old_time))
    active_artifact = sessions / "rollout-active.jsonl"
    newer_text = (_FIXTURES / "codex_rollout_anonymized.jsonl").read_text(encoding="utf-8")
    active_artifact.write_text(
        newer_text.replace("2026-04-12T10:00:00Z", "2026-04-12T11:00:00Z", 1),
        encoding="utf-8",
    )
    recent = (_NOW - timedelta(seconds=30)).timestamp()
    os.utime(active_artifact, (recent, recent))
    service = build_discovery_service(environment={"CODEX_HOME": str(root)}, home=tmp_path)

    with pytest.raises(DiscoveryError, match="rollout-active.jsonl"):
        service.discover_latest("codex", now=_NOW)


def test_terminal_copilot_artifact_is_safe_without_waiting(tmp_path: Path) -> None:
    root = tmp_path / "copilot"
    artifact = root / "session-state/id/events.jsonl"
    artifact.parent.mkdir(parents=True)
    shutil.copyfile(_FIXTURES / "copilot_cli_events_v1_anonymized.jsonl", artifact)
    recent = (_NOW - timedelta(seconds=10)).timestamp()
    os.utime(artifact, (recent, recent))

    discovered = build_discovery_service(
        environment={"COPILOT_HOME": str(root)}, home=tmp_path
    ).discover_latest("copilot-cli", now=_NOW)

    assert "terminal" in discovered.safe_reason


def test_equal_semantic_timestamps_are_ambiguous(tmp_path: Path) -> None:
    root = tmp_path / "claude"
    for project in ("one", "two"):
        artifact = root / "projects" / project / f"{project}.jsonl"
        artifact.parent.mkdir(parents=True)
        shutil.copyfile(_FIXTURES / "claude_code_session_anonymized.jsonl", artifact)
        old_time = (_NOW - timedelta(minutes=10)).timestamp()
        os.utime(artifact, (old_time, old_time))

    service = build_discovery_service(
        environment={"CLAUDE_CONFIG_DIR": str(root)}, home=tmp_path
    )

    with pytest.raises(DiscoveryError, match="ambiguous latest claude-code session"):
        service.discover_latest("claude-code", now=_NOW)


def test_changed_artifact_is_refused_before_import(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    artifact = root / "sessions/2026/04/12/rollout-changed.jsonl"
    artifact.parent.mkdir(parents=True)
    shutil.copyfile(_FIXTURES / "codex_rollout_anonymized.jsonl", artifact)
    old_time = (_NOW - timedelta(minutes=10)).timestamp()
    os.utime(artifact, (old_time, old_time))
    service = build_discovery_service(environment={"CODEX_HOME": str(root)}, home=tmp_path)
    discovered = service.discover_latest("codex", now=_NOW)

    artifact.write_text(artifact.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(DiscoveryError, match="changed before import"):
        service.verify_unchanged(discovered)


def test_export_only_source_returns_actionable_instruction(tmp_path: Path) -> None:
    service = build_discovery_service(environment={}, home=tmp_path)

    with pytest.raises(DiscoveryError, match=r"hermes sessions export"):
        service.discover_latest("hermes", now=_NOW)


def test_relative_environment_override_is_rejected(tmp_path: Path) -> None:
    service = build_discovery_service(environment={"CODEX_HOME": "relative"}, home=tmp_path)

    with pytest.raises(DiscoveryError, match="CODEX_HOME must be an absolute path"):
        service.discover_latest("codex", now=_NOW)


def test_default_roots_preserve_posix_and_windows_path_semantics() -> None:
    providers = {provider.source: provider for provider in DISCOVERY_PROVIDERS}

    assert default_root_for(
        providers[DiscoverySource.CODEX], PurePosixPath("/home/person")
    ) == PurePosixPath("/home/person/.codex")
    assert default_root_for(
        providers[DiscoverySource.CLAUDE_CODE], PureWindowsPath("C:/Users/person")
    ) == PureWindowsPath("C:/Users/person/.claude")
    assert default_root_for(
        providers[DiscoverySource.OH_MY_PI], PureWindowsPath("C:/Users/person")
    ) == PureWindowsPath("C:/Users/person/.omp/agent")


def test_discovery_does_not_follow_symlinked_session_file(tmp_path: Path) -> None:
    root = tmp_path / "codex"
    sessions = root / "sessions/2026/04/12"
    sessions.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    shutil.copyfile(_FIXTURES / "codex_rollout_anonymized.jsonl", outside)
    link = sessions / "rollout-linked.jsonl"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available")

    service = build_discovery_service(environment={"CODEX_HOME": str(root)}, home=tmp_path)

    with pytest.raises(DiscoveryError, match="no supported codex session"):
        service.discover_latest("codex", now=_NOW)
