from __future__ import annotations

from memory.importers.base import ConversationImporter
from memory.importers.claude_code_session_jsonl import ClaudeCodeSessionJsonlImporter
from memory.importers.codex_rollout_jsonl import CodexRolloutJsonlImporter
from memory.importers.copilot_activity_csv import CopilotActivityCsvImporter
from memory.importers.copilot_cli_events_jsonl import CopilotCliEventsJsonlImporter
from memory.importers.deepseek_harness_session_jsonl import (
    DeepSeekHarnessSessionJsonlImporter,
)
from memory.importers.deepseek_share_json import DeepSeekShareJsonImporter
from memory.importers.gemini_cli_session_json import GeminiCliSessionJsonImporter
from memory.importers.manual_paste import ManualPasteImporter
from memory.importers.opencode_session_json import OpenCodeSessionJsonImporter
from memory.importers.pi_session_jsonl import PiSessionJsonlImporter
from memory.importers.qwen_code_session_export import QwenCodeSessionExportImporter

_IMPORTERS: dict[str, ConversationImporter] = {
    ClaudeCodeSessionJsonlImporter.name: ClaudeCodeSessionJsonlImporter(),
    CodexRolloutJsonlImporter.name: CodexRolloutJsonlImporter(),
    CopilotActivityCsvImporter.name: CopilotActivityCsvImporter(),
    CopilotCliEventsJsonlImporter.name: CopilotCliEventsJsonlImporter(),
    DeepSeekHarnessSessionJsonlImporter.name: DeepSeekHarnessSessionJsonlImporter(),
    DeepSeekShareJsonImporter.name: DeepSeekShareJsonImporter(),
    GeminiCliSessionJsonImporter.name: GeminiCliSessionJsonImporter(),
    ManualPasteImporter.name: ManualPasteImporter(),
    OpenCodeSessionJsonImporter.name: OpenCodeSessionJsonImporter(),
    PiSessionJsonlImporter.name: PiSessionJsonlImporter(),
    QwenCodeSessionExportImporter.name: QwenCodeSessionExportImporter(),
}


def get_importer(name: str) -> ConversationImporter:
    try:
        return _IMPORTERS[name]
    except KeyError as exc:
        supported = ", ".join(sorted(_IMPORTERS))
        raise ValueError(f"unknown importer: {name}; supported importers: {supported}") from exc


def importer_names() -> tuple[str, ...]:
    return tuple(sorted(_IMPORTERS))
