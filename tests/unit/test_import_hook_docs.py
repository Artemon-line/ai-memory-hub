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


def test_pi_recipe_is_explicit_versioned_and_reversible() -> None:
    guide = (_ROOT / "docs" / "import_hooks.md").read_text(encoding="utf-8")
    pi_section = guide.split("## Pi", 1)[1].split("## Hermes Agent", 1)[0]

    assert "0.73.0" in pi_section
    assert "tools/import_pi_session.py" in pi_section
    assert "pi-session-jsonl" in pi_section
    assert "source `pi`" in pi_section
    assert "/session" in pi_section
    assert "version 3" in pi_section
    assert "Do not" in pi_section
    assert "nothing persistent to disable" in pi_section


def test_planned_hook_sections_have_stable_anchors_without_commands() -> None:
    guide = (_ROOT / "docs" / "import_hooks.md").read_text(encoding="utf-8")

    for heading, issue in (
        ("Hermes Agent", 327),
        ("DeepSeek Harness", 328),
        ("OpenClaw", 329),
    ):
        section = guide.split(f"## {heading}", 1)[1].split("## ", 1)[0]
        assert "**Planned.**" in section
        assert f"issues/{issue}" in section
        assert "No command is published" in section
        assert "```" not in section
