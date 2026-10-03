from __future__ import annotations

from memory.importers.base import ConversationImporter
from memory.importers.claude_code_session_jsonl import ClaudeCodeSessionJsonlImporter
from memory.importers.codex_rollout_jsonl import CodexRolloutJsonlImporter
from memory.importers.copilot_activity_csv import CopilotActivityCsvImporter
from memory.importers.deepseek_share_json import DeepSeekShareJsonImporter
from memory.importers.manual_paste import ManualPasteImporter
from memory.importers.opencode_session_json import OpenCodeSessionJsonImporter

_IMPORTERS: dict[str, ConversationImporter] = {
    ClaudeCodeSessionJsonlImporter.name: ClaudeCodeSessionJsonlImporter(),
    CodexRolloutJsonlImporter.name: CodexRolloutJsonlImporter(),
    CopilotActivityCsvImporter.name: CopilotActivityCsvImporter(),
    DeepSeekShareJsonImporter.name: DeepSeekShareJsonImporter(),
    ManualPasteImporter.name: ManualPasteImporter(),
    OpenCodeSessionJsonImporter.name: OpenCodeSessionJsonImporter(),
}


def get_importer(name: str) -> ConversationImporter:
    try:
        return _IMPORTERS[name]
    except KeyError as exc:
        supported = ", ".join(sorted(_IMPORTERS))
        raise ValueError(f"unknown importer: {name}; supported importers: {supported}") from exc


def importer_names() -> tuple[str, ...]:
    return tuple(sorted(_IMPORTERS))
