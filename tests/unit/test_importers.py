from __future__ import annotations

import pytest

from memory.importers import ConversationImporter, ManualPasteImporter, get_importer, importer_names


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


@pytest.mark.parametrize("speaker", ["Assistant", "AI", "Bot", "Claude", "Gemini", "ChatGPT"])
def test_manual_paste_importer_normalizes_assistant_aliases(speaker: str) -> None:
    payload = ManualPasteImporter().import_text(f"Human: Hello\n{speaker}: Hi")[0]

    assert [message["role"] for message in payload["messages"]] == ["user", "assistant"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("unlabelled text", "first speaker label"),
        ("", "supported speaker labels"),
        ("User:\nAssistant: response", "empty message"),
    ],
)
def test_manual_paste_importer_rejects_ambiguous_input(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ManualPasteImporter().import_text(text)


def test_importer_registry_exposes_manual_importer() -> None:
    assert importer_names() == ("manual",)
    assert get_importer("manual").name == "manual"


def test_importer_registry_rejects_unknown_importer() -> None:
    with pytest.raises(ValueError, match="supported importers: manual"):
        get_importer("unknown")

