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
