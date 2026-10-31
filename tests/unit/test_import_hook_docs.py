from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).parents[2]


def test_import_hook_guide_pins_clients_and_shared_uploader_contract() -> None:
    guide = (_ROOT / "docs" / "import_hooks.md").read_text(encoding="utf-8")

    for version in ("0.152.0", "2.1.288", "1.19.0", "0.57.0"):
        assert version in guide
    for parser in (
        "codex-rollout-jsonl",
        "claude-code-session-jsonl",
        "opencode-session-json",
        "gemini-cli-session-json",
    ):
        assert parser in guide
    assert guide.count("tools/import_to_memory.py") >= 5
    assert "never prints the token" in guide
    assert "deduplicated: true" in guide
    assert "Remove" in guide


def test_hermes_recipe_uses_private_supported_export_and_cleanup() -> None:
    guide = (_ROOT / "docs" / "import_hooks.md").read_text(encoding="utf-8")
    section = guide.split("## Hermes Agent", 1)[1].split("## Failure handling", 1)[0]

    assert "0.22.5" in section
    assert "tools/import_hermes_session.py" in section
    assert "hermes-session-jsonl" in section
    assert "--session-id" in section
    assert "--ended-older-than" in section
    assert "--redact" in section
    assert "state.db" in section
    assert "temporary" in section
    assert "nothing persistent to disable" in section


def test_openclaw_recipe_separates_current_storage_from_legacy_jsonl() -> None:
    guide = (_ROOT / "docs" / "import_hooks.md").read_text(encoding="utf-8")
    section = guide.split("## OpenClaw", 1)[1].split("## Failure handling", 1)[0]

    assert "2026.10.7" in section
    assert "tools/import_openclaw_session.py" in section
    assert "pi-session-jsonl" in section
    assert "source `openclaw`" in section
    assert "SQLite" in section
    assert "WAL" in section
    assert "version 3" in section
    assert "nothing persistent to disable" in section


def test_pi_recipe_is_explicit_versioned_and_reversible() -> None:
    guide = (_ROOT / "docs" / "import_hooks.md").read_text(encoding="utf-8")
    pi_section = guide.split("## Pi", 1)[1].split("## Failure handling", 1)[0]

    assert "0.73.0" in pi_section
    assert "tools/import_pi_session.py" in pi_section
    assert "pi-session-jsonl" in pi_section
    assert "source `pi`" in pi_section
    assert "/session" in pi_section
    assert "version 3" in pi_section
    assert "Do not" in pi_section
    assert "nothing persistent to disable" in pi_section
