from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory.importers import (
    ClaudeCodeSessionJsonlImporter,
    CodexRolloutJsonlImporter,
    ConversationImporter,
    CopilotActivityCsvImporter,
    CopilotCliEventsJsonlImporter,
    DeepSeekHarnessSessionJsonlImporter,
    DeepSeekShareJsonImporter,
    DroidExecJsonImporter,
    GeminiCliSessionJsonImporter,
    HermesSessionJsonlImporter,
    ManualPasteImporter,
    OpenCodeSessionJsonImporter,
    PiSessionJsonlImporter,
    QwenCodeSessionExportImporter,
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
        "copilot-cli-events-jsonl",
        "deepseek-harness-session-jsonl",
        "deepseek-share-json",
        "droid-exec-json",
        "gemini-cli-session-json",
        "hermes-session-jsonl",
        "manual",
        "opencode-session-json",
        "pi-session-jsonl",
        "qwen-code-session-export",
    )
    assert get_importer("manual").name == "manual"


def test_importer_registry_rejects_unknown_importer() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "claude-code-session-jsonl, codex-rollout-jsonl, copilot-activity-csv, "
            "copilot-cli-events-jsonl, deepseek-harness-session-jsonl, "
            "deepseek-share-json, droid-exec-json, gemini-cli-session-json, "
            "hermes-session-jsonl, manual, "
            "opencode-session-json, "
            "pi-session-jsonl, qwen-code-session-export"
        ),
    ):
        get_importer("unknown")


def test_hermes_fixture_maps_live_text_and_safe_provenance() -> None:
    first_line = (
        _FIXTURES / "hermes_sessions_anonymized.jsonl"
    ).read_text(encoding="utf-8").splitlines()[0]

    payload = HermesSessionJsonlImporter().import_text(first_line)[0]

    assert payload == {
        "source": "hermes",
        "title": "Garden authentication investigation",
        "timestamp": "2023-11-14T22:13:20+00:00",
        "messages": [
            {"role": "user", "text": "Why does the garden login redirect fail?"},
            {
                "role": "assistant",
                "text": "I will inspect the redirect configuration.",
            },
            {
                "role": "assistant",
                "text": "The configured redirect target is stale.",
            },
            {
                "role": "user",
                "text": "Earlier visible question retained after compaction.",
            },
            {
                "role": "assistant",
                "text": "Earlier visible answer retained after compaction.",
            },
        ],
        "metadata": {
            "importer": "hermes-session-jsonl",
            "platform": "hermes",
            "ingestion_method": "jsonl-import",
            "source_session_id": "hermes-session-synthetic",
            "hermes_source": "cli",
            "model": "provider/example-model",
            "directory": "/work/garden-app",
            "git_repo_root": "/work/garden-app",
            "git_branch": "feature/synthetic-auth",
            "parent_session_id": "hermes-parent-synthetic",
            "title": "Garden authentication investigation",
        },
    }
    serialized = json.dumps(payload)
    for excluded in (
        "Private system",
        "Private chain",
        "Private tool",
        "Removed by rewind",
        "Synthetic compressed context",
        "private-user",
        "private-billing",
        "private-message-id",
    ):
        assert excluded not in serialized


def test_hermes_multi_session_fixture_preserves_boundaries() -> None:
    text = (_FIXTURES / "hermes_sessions_anonymized.jsonl").read_text(
        encoding="utf-8"
    )

    payloads = HermesSessionJsonlImporter().import_text(text)

    assert len(payloads) == 2
    assert [payload["metadata"]["source_session_id"] for payload in payloads] == [
        "hermes-session-synthetic",
        "hermes-session-telegram-synthetic",
    ]
    assert payloads[1]["metadata"]["hermes_source"] == "telegram"
    assert payloads[1]["timestamp"] == "2023-11-14T22:15:00+00:00"


def test_hermes_importer_applies_source_and_title_overrides_to_every_session() -> None:
    text = (_FIXTURES / "hermes_sessions_anonymized.jsonl").read_text(
        encoding="utf-8"
    )

    payloads = HermesSessionJsonlImporter().import_text(
        text, source="archive", title="Reviewed Hermes export"
    )

    assert all(payload["source"] == "archive" for payload in payloads)
    assert all(payload["title"] == "Reviewed Hermes export" for payload in payloads)
    assert all(
        payload["metadata"]["title"] == "Reviewed Hermes export"
        for payload in payloads
    )


def test_hermes_importer_reports_malformed_json_line() -> None:
    text = '{"id":"ok","messages":[]}\n{not json}'

    with pytest.raises(ValueError, match="line 2 must be valid JSON"):
        HermesSessionJsonlImporter().import_text(text)


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ({"messages": []}, "'id' is a required property"),
        ({"id": "session", "messages": "bad"}, "messages.*not of type 'array'"),
        (
            {"id": "session", "messages": [{"role": "user", "content": {}}]},
            "message 1.*content",
        ),
        (
            {
                "id": "session",
                "messages": [
                    {"role": "assistant", "content": "text", "active": "yes"}
                ],
            },
            "message 1.*active",
        ),
    ],
)
def test_hermes_importer_rejects_malformed_records(
    record: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        HermesSessionJsonlImporter().import_text(json.dumps(record))


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "system", "content": "instructions"}],
        [{"role": "tool", "content": "result"}],
        [{"role": "assistant", "content": "", "tool_calls": [{}]}],
        [{"role": "user", "content": [{"type": "image", "url": "image"}]}],
        [{"role": "user", "content": "removed", "active": 0, "compacted": 0}],
    ],
)
def test_hermes_importer_rejects_sessions_without_conversational_text(
    messages: list[dict[str, object]],
) -> None:
    record = {"id": "empty-session", "messages": messages}

    with pytest.raises(ValueError, match="contains no conversational text"):
        HermesSessionJsonlImporter().import_text(json.dumps(record))


def test_hermes_importer_joins_textual_content_blocks() -> None:
    record = {
        "id": "structured-session",
        "messages": [
            {
                "role": "user",
                "content": [
                    "first",
                    {"type": "text", "text": "second"},
                    {"content": "third"},
                    {"type": "image", "text": "not imported"},
                ],
            }
        ],
    }

    payload = HermesSessionJsonlImporter().import_text(json.dumps(record))[0]

    assert payload["messages"] == [
        {"role": "user", "text": "first\n\nsecond\n\nthird"}
    ]


def test_hermes_importer_rejects_duplicate_session_ids() -> None:
    line = json.dumps(
        {"id": "duplicate", "messages": [{"role": "user", "content": "hi"}]}
    )

    with pytest.raises(ValueError, match="line 2 duplicates session id"):
        HermesSessionJsonlImporter().import_text(f"{line}\n{line}")


def test_hermes_importer_rejects_non_text_and_oversized_input() -> None:
    importer = HermesSessionJsonlImporter()
    with pytest.raises(ValueError, match="must be text"):
        importer.import_text(b"{}")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exceeds 20000000 bytes"):
        importer.import_text("{}" + (" " * 20_000_000))


def test_copilot_cli_v1_fixture_maps_text_and_safe_provenance() -> None:
    text = (_FIXTURES / "copilot_cli_events_v1_anonymized.jsonl").read_text(
        encoding="utf-8"
    )

    payload = CopilotCliEventsJsonlImporter().import_text(text)[0]

    assert payload == {
        "source": "copilot-cli",
        "title": "Garden sensor parser",
        "timestamp": "2026-09-28T20:35:15+00:00",
        "messages": [
            {"role": "user", "text": "Check the sensor parser."},
            {"role": "assistant", "text": "I will inspect the parser."},
            {
                "role": "assistant",
                "text": "The parser now rejects invalid readings.",
            },
        ],
        "metadata": {
            "importer": "copilot-cli-events-jsonl",
            "platform": "copilot-cli",
            "ingestion_method": "jsonl-import",
            "source_session_id": "copilot-session-synthetic",
            "model": "copilot-example-model",
            "cli_version": "1.0.88",
            "directory": "/work/garden-sensor",
            "git_root": "/work/garden-sensor",
            "git_repository": "garden-sensor",
            "git_branch": "feature/synthetic",
            "title": "Garden sensor parser",
        },
    }


def test_copilot_cli_resumed_fixture_accepts_legacy_message_envelopes() -> None:
    text = (_FIXTURES / "copilot_cli_events_resumed_anonymized.jsonl").read_text(
        encoding="utf-8"
    )

    payload = CopilotCliEventsJsonlImporter().import_text(text)[0]

    assert payload["title"] == "Resumed garden work"
    assert payload["metadata"]["source_session_id"] == "copilot-resumed-synthetic"
    assert payload["metadata"]["git_repository"] == "resumed-garden"
    assert payload["messages"] == [
        {"role": "user", "text": "Resume the garden notes."},
        {"role": "assistant", "text": "The notes are ready."},
    ]


def test_copilot_cli_importer_accepts_overrides() -> None:
    text = '\n'.join(
        [
            '{"type":"user.message","data":{"content":"Question"},"id":"u1"}',
            '{"type":"assistant.message","data":{"content":"Answer"},"id":"a1"}',
        ]
    )

    payload = CopilotCliEventsJsonlImporter().import_text(
        text, source="custom-copilot", title="Imported session"
    )[0]

    assert payload["source"] == "custom-copilot"
    assert payload["title"] == "Imported session"
    assert payload["metadata"]["title"] == "Imported session"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "is empty"),
        ("not json", "line 1 must be valid JSON"),
        ("[]", "line 1 must be an object"),
        (
            '{"type":"user.message","data":{}}',
            "message on line 1 does not match the importer schema",
        ),
        (
            '{"type":"session.start","data":{}}',
            "contains no conversational text",
        ),
        (
            '{"type":"tool.execution_complete","data":{"result":{"content":"first\nsecond"}}}',
            "line 1 must be valid JSON",
        ),
    ],
)
def test_copilot_cli_importer_rejects_invalid_sessions(
    text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        CopilotCliEventsJsonlImporter().import_text(text)


def test_copilot_cli_importer_rejects_non_text_and_oversized_input() -> None:
    importer = CopilotCliEventsJsonlImporter()
    with pytest.raises(ValueError, match="must be text"):
        importer.import_text(b"{}")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exceeds 20000000 bytes"):
        importer.import_text("x" * 20_000_001)


def test_deepseek_harness_v4_fixture_maps_text_and_safe_provenance() -> None:
    text = (_FIXTURES / "deepseek_harness_session_v4_anonymized.jsonl").read_text(
        encoding="utf-8"
    )

    payload = DeepSeekHarnessSessionJsonlImporter().import_text(text)[0]

    assert payload == {
        "source": "deepseek-harness",
        "title": "Sensor test investigation",
        "timestamp": "2026-10-02T09:15:23+00:00",
        "messages": [
            {"role": "user", "text": "Explain the failing sensor test."},
            {"role": "assistant", "text": "The fixture path is stale."},
        ],
        "metadata": {
            "importer": "deepseek-harness-session-jsonl",
            "platform": "deepseek-harness",
            "ingestion_method": "jsonl-import",
            "source_session_id": "dsh-root-synthetic",
            "session_format_version": 4,
            "session_relationship": "root",
            "directory": "/work/garden-sensor",
            "agent_preset": "minimal",
            "delegation_depth": 0,
            "model": "deepseek-official/deepseek-example-model",
            "title": "Sensor test investigation",
        },
    }


def test_deepseek_harness_v0_descendant_accepts_compact_messages() -> None:
    text = (
        _FIXTURES / "deepseek_harness_session_v0_descendant_anonymized.jsonl"
    ).read_text(encoding="utf-8")

    payload = DeepSeekHarnessSessionJsonlImporter().import_text(text)[0]

    assert payload["messages"] == [
        {"role": "user", "text": "Check the child parser."},
        {"role": "assistant", "text": "The child parser is valid."},
    ]
    assert payload["metadata"]["session_format_version"] == 0
    assert payload["metadata"]["session_relationship"] == "descendant"
    assert payload["metadata"]["parent_session_id"] == "dsh-root-synthetic"
    assert payload["metadata"]["session_origin"] == "subagent"
    assert payload["metadata"]["delegation_depth"] == 1


def test_deepseek_harness_importer_accepts_overrides() -> None:
    text = "\n".join(
        [
            '{"type":"session","version":4,"id":"s1","createdAt":1,"isSeeded":false,"delegationDepth":0}',
            '{"type":"user/message","data":{"content":"Question"}}',
            '{"type":"assistant/message","data":{"content":"Answer"}}',
        ]
    )

    payload = DeepSeekHarnessSessionJsonlImporter().import_text(
        text, source="custom-harness", title="Imported Harness session"
    )[0]

    assert payload["source"] == "custom-harness"
    assert payload["title"] == "Imported Harness session"
    assert payload["metadata"]["title"] == "Imported Harness session"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "is empty"),
        ("not json", "line 1 must be valid JSON"),
        ("[]", "line 1 must be an object"),
        ('{"type":"turn/start","data":{}}', "must be the session header"),
        (
            '{"type":"session","version":5,"id":"s1","createdAt":1}',
            "version 5 is not supported",
        ),
        (
            "\n".join(
                [
                    '{"type":"session","version":4,"id":"s1","createdAt":1,"isSeeded":false,"delegationDepth":0}',
                    '{"type":"turn/start","seq":0,"time":1,"data":{}}',
                    '{"type":"user/message","seq":2,"time":2,"data":{"content":"Question"}}',
                ]
            ),
            "non-monotonic sequence; expected 1, got 2",
        ),
        (
            "\n".join(
                [
                    '{"type":"session","version":4,"id":"s1","createdAt":1,"isSeeded":false,"delegationDepth":0}',
                    '{"type":"turn/start","seq":0,"time":1,"data":{}}',
                    '{"type":"user/message","data":{"content":"Question"}}',
                ]
            ),
            "mixes coordinated and physical-order events",
        ),
        (
            "\n".join(
                [
                    '{"type":"session","version":4,"id":"s1","createdAt":1,"isSeeded":false,"delegationDepth":0}',
                    '{"type":"user/message","data":{}}',
                ]
            ),
            "message on line 2 does not match the importer schema",
        ),
        (
            "\n".join(
                [
                    '{"type":"session","version":4,"id":"s1","createdAt":1,"isSeeded":false,"delegationDepth":0}',
                    '{"type":"tool/call","data":{"name":"read_file"}}',
                ]
            ),
            "contains no conversational text",
        ),
    ],
)
def test_deepseek_harness_importer_rejects_invalid_sessions(
    text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        DeepSeekHarnessSessionJsonlImporter().import_text(text)


def test_deepseek_harness_importer_rejects_non_text_and_oversized_input() -> None:
    importer = DeepSeekHarnessSessionJsonlImporter()
    with pytest.raises(ValueError, match="must be text"):
        importer.import_text(b"{}")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exceeds 20000000 bytes"):
        importer.import_text("x" * 20_000_001)


def test_droid_exec_one_shot_fixture_maps_prompt_result_and_provenance() -> None:
    text = (_FIXTURES / "droid_exec_one_shot_anonymized.json").read_text(
        encoding="utf-8"
    )

    payload = DroidExecJsonImporter().import_text(text)[0]

    assert payload == {
        "source": "droid",
        "title": "Sensor test investigation",
        "timestamp": "2026-10-02T09:15:23+00:00",
        "messages": [
            {"role": "user", "text": "Explain the failing fictional sensor test."},
            {"role": "assistant", "text": "The synthetic fixture path is stale."},
        ],
        "metadata": {
            "importer": "droid-exec-json",
            "platform": "droid",
            "ingestion_method": "json-import",
            "capture_format": "ai-memory-hub.droid-exec-capture",
            "capture_version": 1,
            "capture_kind": "one-shot",
            "source_session_id": "droid-session-synthetic",
            "duration_ms": 5657,
            "reported_turn_count": 1,
            "directory": "/workspace/garden-sensor",
            "model": "droid-example-model",
            "title": "Sensor test investigation",
        },
    }


def test_droid_exec_stream_fixture_coalesces_only_completed_visible_turns() -> None:
    text = (_FIXTURES / "droid_exec_stream_anonymized.json").read_text(
        encoding="utf-8"
    )

    payload = DroidExecJsonImporter().import_text(text)[0]

    assert payload["source"] == "droid"
    assert payload["timestamp"] == "2026-10-02T09:20:00+00:00"
    assert payload["messages"] == [
        {"role": "user", "text": "Check the sensor parser."},
        {
            "role": "assistant",
            "text": "The parser accepts the fixture.\n\nNo private fields are retained.",
        },
        {"role": "user", "text": "What should run next?"},
        {"role": "assistant", "text": "Run the focused importer tests."},
    ]
    assert payload["metadata"]["source_session_id"] == "droid-stream-session-synthetic"
    assert payload["metadata"]["directory"] == "/workspace/garden-sensor"
    assert payload["metadata"]["model"] == "droid-stream-model"
    serialized = json.dumps(payload)
    assert "Private reasoning" not in serialized
    assert "Private tool output" not in serialized
    assert "Private error detail" not in serialized
    assert '"secret"' not in serialized


def test_droid_exec_importer_accepts_source_and_title_overrides() -> None:
    text = (_FIXTURES / "droid_exec_one_shot_anonymized.json").read_text(
        encoding="utf-8"
    )

    payload = DroidExecJsonImporter().import_text(
        text, source="custom-droid", title="Imported Droid run"
    )[0]

    assert payload["source"] == "custom-droid"
    assert payload["title"] == "Imported Droid run"
    assert payload["metadata"]["title"] == "Imported Droid run"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("not json", "must be valid JSON"),
        (
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "Result only",
                }
            ),
            "version 1 ai-memory-hub capture envelope",
        ),
        (
            json.dumps(
                {
                    "format": "ai-memory-hub.droid-exec-capture",
                    "version": 1,
                    "kind": "one-shot",
                    "result": {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "Answer",
                    },
                }
            ),
            "version 1 ai-memory-hub capture envelope",
        ),
        (
            json.dumps(
                {
                    "format": "ai-memory-hub.droid-exec-capture",
                    "version": 2,
                    "kind": "one-shot",
                    "prompt": "Question",
                    "result": {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "Answer",
                    },
                }
            ),
            "version 1 ai-memory-hub capture envelope",
        ),
        (
            json.dumps(
                {
                    "format": "ai-memory-hub.droid-exec-capture",
                    "version": 1,
                    "kind": "one-shot",
                    "prompt": "Question",
                    "result": {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                        "result": "Partial private output",
                    },
                }
            ),
            "does not contain a successful result",
        ),
        (
            json.dumps(
                {
                    "format": "ai-memory-hub.droid-exec-capture",
                    "version": 1,
                    "kind": "stream-jsonrpc",
                    "frames": [
                        {
                            "direction": "client-to-droid",
                            "message": {
                                "jsonrpc": "2.0",
                                "method": "droid.add_user_message",
                                "params": {"text": "Question"},
                            },
                        },
                        {
                            "direction": "droid-to-client",
                            "message": {
                                "jsonrpc": "2.0",
                                "method": "droid.session_notification",
                                "params": {
                                    "notification": {
                                        "type": "assistant_text_delta",
                                        "messageId": "a1",
                                        "blockIndex": 0,
                                    }
                                },
                            },
                        },
                    ],
                }
            ),
            "frame 2 has an invalid assistant text delta",
        ),
        (
            json.dumps(
                {
                    "format": "ai-memory-hub.droid-exec-capture",
                    "version": 1,
                    "kind": "stream-jsonrpc",
                    "frames": [
                        {
                            "direction": "client-to-droid",
                            "message": {
                                "jsonrpc": "2.0",
                                "method": "droid.add_user_message",
                                "params": {"text": "Question"},
                            },
                        }
                    ],
                }
            ),
            "contains no completed conversational turns",
        ),
    ],
)
def test_droid_exec_importer_rejects_invalid_or_incomplete_captures(
    document: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        DroidExecJsonImporter().import_text(document)


def test_droid_exec_importer_rejects_non_text_and_oversized_input() -> None:
    importer = DroidExecJsonImporter()
    with pytest.raises(ValueError, match="must be text"):
        importer.import_text(b"{}")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exceeds 20000000 bytes"):
        importer.import_text("x" * 20_000_001)


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


def test_pi_session_fixture_reconstructs_only_the_active_branch() -> None:
    text = (_FIXTURES / "pi_session_anonymized.jsonl").read_text(encoding="utf-8")

    payload = PiSessionJsonlImporter().import_text(text)[0]

    assert payload == {
        "source": "pi",
        "timestamp": "2026-04-12T10:00:00+00:00",
        "messages": [
            {
                "role": "user",
                "text": "Draft a setup checklist for a balcony temperature sensor.",
            },
            {"role": "assistant", "text": "Mount the sensor in shade."},
            {"role": "user", "text": "Add a battery-check note."},
            {
                "role": "assistant",
                "text": "Check the battery before each season.",
            },
        ],
        "metadata": {
            "importer": "pi-session-jsonl",
            "platform": "pi",
            "detected_variant": "pi",
            "ingestion_method": "jsonl-import",
            "source_session_id": "pi-session-synthetic",
            "directory": "/workspace/garden-sensor",
            "git_branch": "feature/sensor-notes",
            "git_commit": "0123456789abcdef",
            "model": "example-provider/example-model",
        },
    }
    serialized = json.dumps(payload)
    assert "Abandoned branch" not in serialized
    assert "Private reasoning" not in serialized
    assert "Private tool output" not in serialized
    assert "Private compaction" not in serialized


@pytest.mark.parametrize(
    ("fixture_name", "variant", "expected_title"),
    [
        ("oh_my_pi_session_anonymized.jsonl", "oh-my-pi", "Garden sensor setup"),
        ("openclaw_session_anonymized.jsonl", "openclaw", None),
    ],
)
def test_pi_session_detects_variants_and_omits_private_records(
    fixture_name: str, variant: str, expected_title: str | None
) -> None:
    text = (_FIXTURES / fixture_name).read_text(encoding="utf-8")

    payload = PiSessionJsonlImporter().import_text(text)[0]

    assert payload["source"] == variant
    assert payload["metadata"]["detected_variant"] == variant
    assert payload.get("title") == expected_title
    serialized = json.dumps(payload)
    assert "example.private-state" not in serialized
    assert "private-account" not in serialized


def test_pi_session_accepts_source_and_title_overrides() -> None:
    text = (_FIXTURES / "pi_session_anonymized.jsonl").read_text(encoding="utf-8")

    payload = PiSessionJsonlImporter().import_text(
        text, source="custom-pi", title="Imported session"
    )[0]

    assert payload["source"] == "custom-pi"
    assert payload["title"] == "Imported session"
    assert payload["metadata"]["title"] == "Imported session"
    assert payload["metadata"]["detected_variant"] == "pi"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "line 1 must be valid JSON"),
        ('{"type":"session","version":2}', "must use version 3"),
        (
            '\n'.join(
                [
                    '{"type":"session","version":3,"id":"s","timestamp":"2026-04-12T10:00:00Z","cwd":"/work"}',
                    '{"type":"message","id":"a","parentId":"missing","timestamp":"2026-04-12T10:00:01Z","message":{"role":"user","content":"Question"}}',
                ]
            ),
            "references missing parent",
        ),
        (
            '\n'.join(
                [
                    '{"type":"session","version":3,"id":"s","timestamp":"2026-04-12T10:00:00Z","cwd":"/work"}',
                    '{"type":"message","id":"a","parentId":"b","timestamp":"2026-04-12T10:00:01Z","message":{"role":"user","content":"Question"}}',
                    '{"type":"message","id":"b","parentId":"a","timestamp":"2026-04-12T10:00:02Z","message":{"role":"assistant","content":"Answer"}}',
                ]
            ),
            "contains a cycle",
        ),
        (
            '\n'.join(
                [
                    '{"type":"session","version":3,"id":"s","timestamp":"2026-04-12T10:00:00Z","cwd":"/work"}',
                    '{"type":"message","id":"a","parentId":null,"timestamp":"2026-04-12T10:00:01Z","message":{"role":"user"}}',
                ]
            ),
            "message on line 2 does not match the importer schema",
        ),
    ],
)
def test_pi_session_rejects_invalid_or_corrupt_exports(
    text: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        PiSessionJsonlImporter().import_text(text)


def test_pi_session_rejects_non_text_and_oversized_input() -> None:
    importer = PiSessionJsonlImporter()
    with pytest.raises(ValueError, match="must be text"):
        importer.import_text(b"{}")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exceeds 20000000 bytes"):
        importer.import_text("x" * 20_000_001)


def test_qwen_code_json_fixture_maps_text_and_safe_provenance() -> None:
    text = (_FIXTURES / "qwen_code_session_anonymized.json").read_text(
        encoding="utf-8"
    )

    payload = QwenCodeSessionExportImporter().import_text(text)[0]

    assert payload == {
        "source": "qwen-code",
        "timestamp": "2026-04-12T10:00:00+00:00",
        "messages": [
            {
                "role": "user",
                "text": "Draft a setup checklist for a balcony temperature sensor.",
            },
            {"role": "assistant", "text": "Mount the sensor in shade."},
            {"role": "user", "text": "Add a battery-check note."},
            {
                "role": "assistant",
                "text": (
                    "Check the battery before each season.\n\n"
                    "Record the replacement date."
                ),
            },
        ],
        "metadata": {
            "importer": "qwen-code-session-export",
            "platform": "qwen-code",
            "ingestion_method": "json-import",
            "export_format": "json",
            "source_session_id": "qwen-session-synthetic",
            "directory": "/workspace/garden-sensor",
            "git_repository": "garden-sensor",
            "git_branch": "feature/sensor-notes",
            "channel": "cli",
            "model": "qwen-example-model",
        },
    }
    serialized = json.dumps(payload)
    assert "Private system" not in serialized
    assert "Private tool" not in serialized
    assert "goal-state" not in serialized
    assert "Duplicate presentation" not in serialized
    assert "uniqueFiles" not in serialized
    assert "must-not-override" not in serialized


def test_qwen_code_jsonl_fixture_maps_canonical_export() -> None:
    text = (_FIXTURES / "qwen_code_session_anonymized.jsonl").read_text(
        encoding="utf-8"
    )

    payload = QwenCodeSessionExportImporter().import_text(text)[0]

    assert payload == {
        "source": "qwen-code",
        "timestamp": "2026-04-12T11:00:00+00:00",
        "messages": [
            {"role": "user", "text": "Summarize the installation."},
            {
                "role": "assistant",
                "text": (
                    "The sensor is mounted in shade.\n\n"
                    "Its baseline is recorded."
                ),
            },
        ],
        "metadata": {
            "importer": "qwen-code-session-export",
            "platform": "qwen-code",
            "ingestion_method": "jsonl-import",
            "export_format": "jsonl",
            "source_session_id": "qwen-jsonl-synthetic",
            "directory": "/workspace/garden-sensor",
            "git_repository": "garden-sensor",
            "git_branch": "feature/jsonl-notes",
            "channel": "cli",
            "model": "qwen-jsonl-model",
        },
    }
    serialized = json.dumps(payload)
    assert "Private system" not in serialized
    assert "Private tool" not in serialized
    assert "secret.txt" not in serialized


def test_qwen_code_message_only_jsonl_accepts_overrides_and_message_time() -> None:
    text = "\n".join(
        [
            '{"uuid":"u1","timestamp":"2026-04-12T12:00:01Z","type":"user","message":{"content":"Question"}}',
            '{"uuid":"a1","timestamp":"2026-04-12T12:00:02Z","type":"assistant","model":"qwen-model","message":{"role":"assistant","content":"Answer"}}',
        ]
    )

    payload = QwenCodeSessionExportImporter().import_text(
        text, source="custom-qwen", title="Imported session"
    )[0]

    assert payload["source"] == "custom-qwen"
    assert payload["title"] == "Imported session"
    assert payload["timestamp"] == "2026-04-12T12:00:01+00:00"
    assert payload["metadata"]["title"] == "Imported session"
    assert payload["metadata"]["model"] == "qwen-model"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "line 1 must be valid JSON"),
        ("[]", "JSON must be an object"),
        (
            '{"sessionId":"s","startTime":"2026-04-12T10:00:00Z"}',
            "JSON document does not match the importer schema",
        ),
        (
            '{"sessionId":"s","startTime":"2026-04-12T10:00:00Z","messages":[]}',
            "contains no conversational text",
        ),
        (
            '{"uuid":"u1","timestamp":"2026-04-12T10:00:01Z","type":"user","message":{"role":"assistant","content":"Wrong role"}}',
            "message line 1 does not match the importer schema",
        ),
        (
            '\n'.join(
                [
                    '{"type":"session_metadata","sessionId":"s","startTime":"2026-04-12T10:00:00Z"}',
                    '{"type":"session_metadata","sessionId":"s","startTime":"2026-04-12T10:00:00Z"}',
                ]
            ),
            "metadata is duplicated",
        ),
        (
            '\n'.join(
                [
                    '{"uuid":"u1","timestamp":"2026-04-12T10:00:01Z","type":"user","message":{"role":"user","content":"Question"}}',
                    '{"type":"session_metadata","sessionId":"s","startTime":"2026-04-12T10:00:00Z"}',
                ]
            ),
            "metadata must be the first record",
        ),
    ],
)
def test_qwen_code_session_rejects_invalid_exports(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        QwenCodeSessionExportImporter().import_text(text)


def test_qwen_code_session_rejects_non_text_and_oversized_input() -> None:
    importer = QwenCodeSessionExportImporter()
    with pytest.raises(ValueError, match="must be text"):
        importer.import_text(b"{}")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exceeds 20000000 bytes"):
        importer.import_text("x" * 20_000_001)


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
