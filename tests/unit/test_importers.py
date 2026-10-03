from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory.importers import (
    ClaudeCodeSessionJsonlImporter,
    CodexRolloutJsonlImporter,
    ConversationImporter,
    CopilotActivityCsvImporter,
    DeepSeekShareJsonImporter,
    GeminiCliSessionJsonImporter,
    ManualPasteImporter,
    OpenCodeSessionJsonImporter,
    get_importer,
    importer_names,
)

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "importers"


def test_manual_paste_importer_returns_unified_payload() -> None:
    importer = ManualPasteImporter()

    payloads = importer.import_text(
        "You: Can you remember this?\ncontinued detail\nCopilot: Yes, I can.",
        source="vscode-copilot",
        title="Memory discussion",
    )

    assert isinstance(importer, ConversationImporter)
    assert payloads == [
        {
            "source": "vscode-copilot",
            "title": "Memory discussion",
            "messages": [
                {"role": "user", "text": "Can you remember this?\ncontinued detail"},
                {"role": "assistant", "text": "Yes, I can."},
            ],
            "metadata": {"importer": "manual"},
        }
    ]


@pytest.mark.parametrize("speaker", ["User", "You", "Human"])
def test_manual_paste_importer_normalizes_user_aliases(speaker: str) -> None:
    payload = ManualPasteImporter().import_text(f"{speaker}: Hello\nAssistant: Hi")[0]

    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"]


@pytest.mark.parametrize("speaker", ["Assistant", "AI", "Bot", "Claude", "Gemini", "ChatGPT"])
def test_manual_paste_importer_normalizes_assistant_aliases(speaker: str) -> None:
    payload = ManualPasteImporter().import_text(f"Human: Hello\n{speaker}: Hi")[0]

    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"]


def test_manual_paste_importer_preserves_multiline_content() -> None:
    payload = ManualPasteImporter().import_text(
        "\n"
        "user: show this snippet\n"
        "```python\n"
        "print('Assistant: still code')\n"
        "```\n"
        "\n"
        "chatgpt:Sure\n"
        "Note: unsupported labels stay in the message body"
    )[0]

    assert payload == {
        "source": "manual-paste",
        "messages": [
            {
                "role": "user",
                "text": (
                    "show this snippet\n"
                    "```python\n"
                    "print('Assistant: still code')\n"
                    "```\n"
                ),
            },
            {
                "role": "assistant",
                "text": "Sure\nNote: unsupported labels stay in the message body",
            },
        ],
        "metadata": {"importer": "manual"},
    }


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("unlabelled text", "first speaker label"),
        ("", "supported speaker labels"),
        ("User:\nAssistant: response", "empty message"),
        ("User:   \nAssistant: response", "empty message"),
    ],
)
def test_manual_paste_importer_rejects_ambiguous_input(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ManualPasteImporter().import_text(text)


def test_manual_paste_importer_rejects_non_text_input() -> None:
    with pytest.raises(ValueError, match="must be text"):
        ManualPasteImporter().import_text(b"User: hello")  # type: ignore[arg-type]


def test_manual_paste_importer_rejects_oversized_input() -> None:
    with pytest.raises(ValueError, match="exceeds 5000000 bytes"):
        ManualPasteImporter().import_text(f"User: {'x' * 5_000_000}")


def test_importer_registry_exposes_manual_importer() -> None:
    assert importer_names() == (
        "claude-code-session-jsonl",
        "codex-rollout-jsonl",
        "copilot-activity-csv",
        "deepseek-share-json",
        "gemini-cli-session-json",
        "manual",
        "opencode-session-json",
    )
    assert get_importer("manual").name == "manual"


def test_importer_registry_rejects_unknown_importer() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "claude-code-session-jsonl, codex-rollout-jsonl, copilot-activity-csv, "
            "deepseek-share-json, gemini-cli-session-json, manual, "
            "opencode-session-json"
        ),
    ):
        get_importer("unknown")


def test_copilot_activity_csv_fixture_is_grouped_and_chronological() -> None:
    text = (_FIXTURES / "copilot_activity_anonymized.csv").read_text(encoding="utf-8")

    payloads = CopilotActivityCsvImporter().import_text(text)

    assert [payload["title"] for payload in payloads] == [
        "Garden sensor setup",
        "Recipe notes",
    ]
    assert payloads[0]["timestamp"] == "2026-04-12T10:05:00"
    assert [message["role"] for message in payloads[0]["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert payloads[1]["messages"][0]["text"].endswith("emoji 🥣.")
    assert "```text\nmix for 30 seconds\n```" in payloads[1]["messages"][1]["text"]
    assert payloads[0]["metadata"] == {
        "importer": "copilot-activity-csv",
        "platform": "web",
        "ingestion_method": "csv-import",
    }


def test_codex_rollout_jsonl_fixture_maps_only_canonical_messages_and_provenance() -> None:
    text = (_FIXTURES / "codex_rollout_anonymized.jsonl").read_text(encoding="utf-8")

    payload = CodexRolloutJsonlImporter().import_text(text)[0]

    assert payload == {
        "source": "codex",
        "timestamp": "2026-04-12T10:00:00+00:00",
        "messages": [
            {
                "role": "user",
                "text": "Draft a setup checklist for a balcony temperature sensor.",
            },
            {
                "role": "assistant",
                "text": "1. Mount the sensor in shade.\n\n2. Record a baseline reading.",
            },
        ],
        "metadata": {
            "importer": "codex-rollout-jsonl",
            "platform": "codex",
            "ingestion_method": "jsonl-import",
            "source_session_id": "0198f000-0000-7000-8000-000000000001",
            "directory": "/workspace/garden-sensor",
            "session_source": "cli",
            "originator": "codex-tui",
            "cli_version": "0.152.0",
            "model_provider": "example-provider",
            "git_branch": "feature/sensor-notes",
            "git_commit": "0123456789abcdef",
        },
    }
    serialized = json.dumps(payload)
    assert "Private reasoning" not in serialized
    assert "private tool output" not in serialized
    assert "creator_user_id" not in serialized
    assert "base_instructions" not in serialized
    assert "Injected runtime context" not in serialized


def test_codex_rollout_jsonl_accepts_overrides_and_uses_message_timestamp() -> None:
    payload = CodexRolloutJsonlImporter().import_text(
        "\n".join(
            [
                '{"type":"response_item","timestamp":"2026-04-12T10:00:02Z","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"Question"}]}}',
                '{"type":"response_item","timestamp":"2026-04-12T10:00:03Z","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Answer"}]}}',
            ]
        ),
        source="custom-codex",
        title="Imported rollout",
    )[0]

    assert payload["source"] == "custom-codex"
    assert payload["title"] == "Imported rollout"
    assert payload["timestamp"] == "2026-04-12T10:00:02+00:00"
    assert payload["metadata"]["title"] == "Imported rollout"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "line 1 must be valid JSON"),
        ("[]", "line 1 must be an object"),
        (
            '{"type":"session_meta","payload":[]}\n',
            "line 1 has invalid session metadata",
        ),
        (
            '{"type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Result only"}]}}',
            "must contain user and assistant",
        ),
        (
            '{"type":"response_item","payload":{"type":"message","role":"user","content":[]}}',
            "must contain user and assistant",
        ),
    ],
)
def test_codex_rollout_jsonl_rejects_invalid_or_incomplete_exports(
    text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CodexRolloutJsonlImporter().import_text(text)


def test_codex_rollout_jsonl_rejects_non_text_and_oversized_input() -> None:
    importer = CodexRolloutJsonlImporter()
    with pytest.raises(ValueError, match="must be text"):
        importer.import_text(b"{}")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exceeds 20000000 bytes"):
        importer.import_text("x" * 20_000_001)


def test_codex_rollout_rich_fixture_keeps_visible_messages_only() -> None:
    text = (_FIXTURES / "codex_rollout_rich_anonymized.jsonl").read_text(
        encoding="utf-8"
    )

    payload = CodexRolloutJsonlImporter().import_text(text)[0]

    assert len(payload["messages"]) == 15
    assert [message["role"] for message in payload["messages"]] == [
        role for _ in range(5) for role in ("user", "assistant", "assistant")
    ]
    assert payload["messages"][-1]["text"].startswith("The fictional workflow")


def test_claude_code_session_fixture_keeps_visible_text_and_safe_provenance() -> None:
    text = (_FIXTURES / "claude_code_session_anonymized.jsonl").read_text(
        encoding="utf-8"
    )

    payload = ClaudeCodeSessionJsonlImporter().import_text(text)[0]

    assert payload == {
        "source": "claude-code",
        "timestamp": "2026-04-12T10:00:01+00:00",
        "messages": [
            {
                "role": "user",
                "text": "Draft a setup checklist for a balcony temperature sensor.",
            },
            {
                "role": "assistant",
                "text": "Mount the sensor in shade.\n\nRecord a baseline reading.",
            },
            {"role": "user", "text": "Add a battery-check note."},
            {
                "role": "assistant",
                "text": "Check the battery before each season.",
            },
        ],
        "metadata": {
            "importer": "claude-code-session-jsonl",
            "platform": "claude-code",
            "ingestion_method": "jsonl-import",
            "source_session_id": "claude-session-synthetic",
            "directory": "/workspace/garden-sensor",
            "git_branch": "feature/sensor-notes",
            "model": "claude-example-model",
        },
    }
    serialized = json.dumps(payload)
    assert "Private" not in serialized
    assert "Duplicate presentation" not in serialized


def test_claude_code_session_accepts_overrides() -> None:
    payload = ClaudeCodeSessionJsonlImporter().import_text(
        '{"type":"user","timestamp":"2026-04-12T10:00:01Z",'
        '"message":{"role":"user","content":"Question"}}',
        source="custom-claude",
        title="Imported session",
    )[0]

    assert payload["source"] == "custom-claude"
    assert payload["title"] == "Imported session"
    assert payload["metadata"]["title"] == "Imported session"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "line 1 must be valid JSON"),
        ("[]", "line 1 must be an object"),
        (
            '{"type":"user","message":[]}',
            "line 1 does not match the importer schema",
        ),
        (
            '{"type":"user","message":{"role":"assistant","content":"wrong"}}',
            "line 1 does not match the importer schema",
        ),
        (
            '{"type":"assistant","message":{"role":"assistant","content":[]}}',
            "contains no conversational text",
        ),
        (
            '{"type":"user","timestamp":"not-a-time",'
            '"message":{"role":"user","content":"Question"}}',
            "timestamp on line 1 is invalid",
        ),
    ],
)
def test_claude_code_session_rejects_invalid_exports(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ClaudeCodeSessionJsonlImporter().import_text(text)


def test_claude_code_session_rejects_non_text_and_oversized_input() -> None:
    importer = ClaudeCodeSessionJsonlImporter()
    with pytest.raises(ValueError, match="must be text"):
        importer.import_text(b"{}")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exceeds 20000000 bytes"):
        importer.import_text("x" * 20_000_001)


def test_gemini_cli_export_session_fixture_keeps_visible_text_and_provenance() -> None:
    text = (_FIXTURES / "gemini_cli_export_session_anonymized.json").read_text(
        encoding="utf-8"
    )

    payload = GeminiCliSessionJsonImporter().import_text(text)[0]

    assert payload == {
        "source": "gemini-cli",
        "timestamp": "2026-10-02T09:15:23+00:00",
        "messages": [
            {"role": "user", "text": "Explain the fictional sensor failure."},
            {
                "role": "assistant",
                "text": "The synthetic fixture has a stale calibration value.",
            },
        ],
        "metadata": {
            "importer": "gemini-cli-session-json",
            "platform": "gemini-cli",
            "ingestion_method": "json-import",
            "export_format": "session",
            "source_session_id": "7d0f0000-1111-4222-8333-444444444444",
            "project_hash": "synthetic-project-hash",
            "model": "gemini-example-model",
            "workspace_directories": ["/workspace/weather-station"],
        },
    }
    serialized = json.dumps(payload)
    assert "Private reasoning" not in serialized
    assert "private tool output" not in serialized
    assert "unknownFutureField" not in serialized


def test_gemini_cli_shared_history_fixture_excludes_injected_context_and_tools() -> None:
    text = (_FIXTURES / "gemini_cli_shared_history_anonymized.json").read_text(
        encoding="utf-8"
    )

    payload = GeminiCliSessionJsonImporter().import_text(text)[0]

    assert payload == {
        "source": "gemini-cli",
        "messages": [
            {
                "role": "user",
                "text": "Summarize the fictional launch checklist.",
            },
            {
                "role": "assistant",
                "text": "Verify the dry run, owner, and rollback window.",
            },
        ],
        "metadata": {
            "importer": "gemini-cli-session-json",
            "platform": "gemini-cli",
            "ingestion_method": "json-import",
            "export_format": "shared-history",
        },
    }
    serialized = json.dumps(payload)
    assert "Private workspace tree" not in serialized
    assert "private tool output" not in serialized
    assert "launch.md" not in serialized


def test_gemini_cli_session_accepts_overrides_and_message_timestamp() -> None:
    payload = GeminiCliSessionJsonImporter().import_text(
        json.dumps(
            {
                "messages": [
                    {
                        "type": "user",
                        "timestamp": "2026-04-12T10:00:01Z",
                        "content": "Question",
                    },
                    {"type": "gemini", "content": [{"text": "Answer"}]},
                ]
            }
        ),
        source="custom-gemini",
        title="Imported session",
    )[0]

    assert payload["source"] == "custom-gemini"
    assert payload["title"] == "Imported session"
    assert payload["timestamp"] == "2026-04-12T10:00:01+00:00"
    assert payload["metadata"]["title"] == "Imported session"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "must be valid JSON"),
        ("{}", "must be an object or array"),
        ('{"messages":[1]}', "message 1 must be an object"),
        (
            '{"messages":[{"type":"user","content":1}]}',
            "message 1 has invalid content",
        ),
        (
            '[{"role":"user","parts":"invalid"}]',
            "shared history item 1 has invalid parts",
        ),
        ('{"messages":[]}', "contains no conversational text"),
        (
            '{"startTime":"not-a-time","messages":['
            '{"type":"user","content":"Question"}]}',
            "start time is invalid",
        ),
    ],
)
def test_gemini_cli_session_rejects_invalid_exports(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        GeminiCliSessionJsonImporter().import_text(text)


def test_gemini_cli_session_rejects_non_text_and_oversized_input() -> None:
    importer = GeminiCliSessionJsonImporter()
    with pytest.raises(ValueError, match="must be text"):
        importer.import_text(b"{}")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exceeds 20000000 bytes"):
        importer.import_text("x" * 20_000_001)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Conversation,Time,Author\nA,2026-01-01T00:00:00,Human", "must have headers"),
        (
            "Conversation,Time,Author,Message\nA,not-a-time,Human,hello",
            "invalid time",
        ),
        (
            "Conversation,Time,Author,Message\nA,2026-01-01T00:00:00,System,hello",
            "unknown author",
        ),
    ],
)
def test_copilot_activity_csv_rejects_invalid_exports(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        CopilotActivityCsvImporter().import_text(text)


def test_deepseek_share_json_fixture_flattens_conversational_fragments() -> None:
    text = (_FIXTURES / "deepseek_share_anonymized.json").read_text(encoding="utf-8")

    payload = DeepSeekShareJsonImporter().import_text(text)[0]

    assert payload["source"] == "deepseek"
    assert payload["title"] == "Balcony herb plan"
    assert payload["timestamp"] == "2026-04-12T10:00:00.237000+00:00"
    assert payload["messages"] == [
        {"role": "user", "text": "Suggest herbs for a windy balcony."},
        {
            "role": "assistant",
            "text": "Try rosemary and thyme in weighted pots [1].",
        },
        {"role": "user", "text": "Attachments: balcony-layout.webp"},
        {
            "role": "assistant",
            "text": "Place the heavier pots along the exposed edge.",
        },
    ]
    assert payload["metadata"] == {
        "importer": "deepseek-share-json",
        "platform": "web",
        "ingestion_method": "json-import",
        "model": "default",
    }


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "valid JSON"),
        ("{}", "unsupported envelope"),
        ('{"data":{"biz_data":{"messages":[]}}}', "contains no messages"),
    ],
)
def test_deepseek_share_json_rejects_invalid_exports(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        DeepSeekShareJsonImporter().import_text(text)


def test_opencode_session_json_fixture_maps_text_and_provenance() -> None:
    text = (_FIXTURES / "opencode_session_anonymized.json").read_text(encoding="utf-8")

    payload = OpenCodeSessionJsonImporter().import_text(text)[0]

    assert payload == {
        "source": "opencode",
        "title": "Garden sensor setup",
        "timestamp": "2026-04-12T10:00:00+00:00",
        "messages": [
            {
                "role": "user",
                "text": "Draft a setup checklist for a balcony temperature sensor.",
            },
            {
                "role": "assistant",
                "text": (
                    "1. Mount the sensor in shade.\n2. Record a baseline reading.\n\n"
                    "Keep the enclosure clear of standing water."
                ),
            },
            {"role": "user", "text": "Add a note about battery checks."},
        ],
        "metadata": {
            "importer": "opencode-session-json",
            "platform": "cli",
            "ingestion_method": "json-import",
            "source_session_id": "ses_synthetic_garden",
            "title": "Garden sensor setup",
            "directory": "/workspace/garden-sensor",
            "model": "example-provider/example-model",
        },
    }


def test_opencode_session_json_accepts_overrides_and_message_model() -> None:
    payload = OpenCodeSessionJsonImporter().import_text(
        """{
          "info": {"id": "ses_1", "path": "/workspace/example"},
          "messages": [{
            "info": {
              "role": "assistant",
              "providerID": "provider",
              "modelID": "model",
              "time": {"created": "2026-04-12T10:00:00Z"}
            },
            "parts": [{"type": "text", "text": "Done."}]
          }]
        }""",
        source="custom-opencode",
        title="Override",
    )[0]

    assert payload["source"] == "custom-opencode"
    assert payload["title"] == "Override"
    assert payload["timestamp"] == "2026-04-12T10:00:00+00:00"
    assert payload["metadata"]["title"] == "Override"
    assert payload["metadata"]["directory"] == "/workspace/example"
    assert payload["metadata"]["model"] == "provider/model"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "valid JSON"),
        ("{}", "valid info object"),
        ('{"info": {}, "messages": []}', "contains no messages"),
        (
            '{"info": {}, "messages": [{"info": {"role": "system"}, "parts": []}]}',
            "unknown role",
        ),
        (
            '{"info": {}, "messages": [{"info": {"role": "user"}, "parts": []}]}',
            "no conversational text",
        ),
    ],
)
def test_opencode_session_json_rejects_invalid_exports(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        OpenCodeSessionJsonImporter().import_text(text)
