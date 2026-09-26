from __future__ import annotations

from pathlib import Path

import pytest

from memory.importers import (
    ConversationImporter,
    CopilotActivityCsvImporter,
    DeepSeekShareJsonImporter,
    ManualPasteImporter,
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
        "copilot-activity-csv",
        "deepseek-share-json",
        "manual",
    )
    assert get_importer("manual").name == "manual"


def test_importer_registry_rejects_unknown_importer() -> None:
    with pytest.raises(ValueError, match="copilot-activity-csv, deepseek-share-json, manual"):
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
