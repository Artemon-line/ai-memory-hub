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


def test_deepseek_harness_recipe_is_bounded_explicit_and_reversible() -> None:
    guide = (_ROOT / "docs" / "import_hooks.md").read_text(encoding="utf-8")
    section = guide.split("## DeepSeek Harness", 1)[1].split(
        "## Failure handling", 1
    )[0]

    assert "formats 0 through 4" in section
    assert "tools/import_deepseek_harness_export.py" in section
    assert "deepseek-harness-session-jsonl" in section
    assert "--member" in section
    assert "subagents/" in section
    assert "path traversal" in section
    assert "nothing persistent to disable" in section
