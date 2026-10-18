from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ImporterId(StrEnum):
    CLAUDE_CODE_SESSION_JSONL = "claude-code-session-jsonl"
    CODEX_ROLLOUT_JSONL = "codex-rollout-jsonl"
    COPILOT_ACTIVITY_CSV = "copilot-activity-csv"
    COPILOT_CLI_EVENTS_JSONL = "copilot-cli-events-jsonl"
    DEEPSEEK_HARNESS_SESSION_JSONL = "deepseek-harness-session-jsonl"
    DEEPSEEK_SHARE_JSON = "deepseek-share-json"
    DROID_EXEC_JSON = "droid-exec-json"
    GEMINI_CLI_SESSION_JSON = "gemini-cli-session-json"
    HERMES_SESSION_JSONL = "hermes-session-jsonl"
    MANUAL = "manual"
    OPENCODE_SESSION_JSON = "opencode-session-json"
    PI_SESSION_JSONL = "pi-session-jsonl"
    QWEN_CODE_SESSION_EXPORT = "qwen-code-session-export"


class SourceClient(StrEnum):
    CLAUDE_CODE = "Claude Code"
    CODEX = "Codex CLI and app"
    MICROSOFT_COPILOT = "Microsoft Copilot"
    GITHUB_COPILOT_CLI = "GitHub Copilot CLI"
    DEEPSEEK_HARNESS = "DeepSeek Harness"
    DEEPSEEK_WEB = "DeepSeek web"
    FACTORY_DROID = "Factory Droid"
    GEMINI_CLI = "Gemini CLI"
    HERMES_AGENT = "Hermes Agent"
    MANUAL_TRANSCRIPT = "Manual transcript"
    OPENCODE = "OpenCode"
    PI = "Pi"
    OH_MY_PI = "Oh My Pi"
    OPENCLAW = "OpenClaw"
    QWEN_CODE = "Qwen Code"


class ArtifactContract(StrEnum):
    DOCUMENTED_EXPORT = "Documented user-facing export"
    OBSERVED_INTERNAL = "Observed internal format"
    HUB_DEFINED = "ai-memory-hub-defined capture"
    USER_AUTHORED = "User-authored transcript"


class PinKind(StrEnum):
    EXACT_CLIENT_RELEASE = "Exact client release"
    FORMAT_VERSION = "Format version"
    ROLLING_SERVICE = "Rolling service contract"
    REPOSITORY_CONTRACT = "Repository-owned contract"
    USER_AUTHORED = "User-authored input"


class PinValue(StrEnum):
    CLAUDE_OBSERVED_JSONL = "observed-jsonl"
    CODEX_0_152_0 = "0.152.0"
    COPILOT_ACTIVITY_HISTORY_CSV = "activity-history-csv"
    COPILOT_CLI_1_0_88 = "1.0.88"
    DEEPSEEK_HARNESS_0_TO_4 = "session-format-0..4"
    DEEPSEEK_SHARE_API_V0 = "share-content-api-v0"
    DROID_CAPTURE_V1 = "capture-v1"
    GEMINI_EXPORT_ENVELOPES = "session-object+shared-history"
    HERMES_SESSIONS_EXPORT = "sessions-export-jsonl"
    MANUAL_SPEAKER_LABELLED = "speaker-labelled-text"
    OPENCODE_SESSION_EXPORT = "session-export-json"
    PI_SESSION_V3 = "pi-session-v3"
    OH_MY_PI_SESSION_V3 = "oh-my-pi-session-v3"
    OPENCLAW_LEGACY_V3 = "openclaw-legacy-session-v3"
    QWEN_JSON_AND_JSONL = "json+jsonl-export"


class Fixture(StrEnum):
    CLAUDE_CODE_SESSION = "claude_code_session_anonymized.jsonl"
    CODEX_ROLLOUT = "codex_rollout_anonymized.jsonl"
    CODEX_ROLLOUT_RICH = "codex_rollout_rich_anonymized.jsonl"
    COPILOT_ACTIVITY = "copilot_activity_anonymized.csv"
    COPILOT_CLI_RESUMED = "copilot_cli_events_resumed_anonymized.jsonl"
    COPILOT_CLI_V1 = "copilot_cli_events_v1_anonymized.jsonl"
    DEEPSEEK_HARNESS_V0 = "deepseek_harness_session_v0_descendant_anonymized.jsonl"
    DEEPSEEK_HARNESS_V4 = "deepseek_harness_session_v4_anonymized.jsonl"
    DEEPSEEK_SHARE = "deepseek_share_anonymized.json"
    DROID_ONE_SHOT = "droid_exec_one_shot_anonymized.json"
    DROID_STREAM = "droid_exec_stream_anonymized.json"
    GEMINI_EXPORT = "gemini_cli_export_session_anonymized.json"
    GEMINI_SHARED = "gemini_cli_shared_history_anonymized.json"
    HERMES_SESSIONS = "hermes_sessions_anonymized.jsonl"
    MANUAL_TRANSCRIPT = "manual_transcript_synthetic.txt"
    OH_MY_PI_SESSION = "oh_my_pi_session_anonymized.jsonl"
    OPENCLAW_SESSION = "openclaw_session_anonymized.jsonl"
    OPENCODE_SESSION = "opencode_session_anonymized.json"
    PI_SESSION = "pi_session_anonymized.jsonl"
    QWEN_JSON = "qwen_code_session_anonymized.json"
    QWEN_JSONL = "qwen_code_session_anonymized.jsonl"


class FocusedTest(StrEnum):
    CLAUDE_CODE = "test_claude_code_session_fixture_keeps_visible_text_and_safe_provenance"
    CODEX = "test_codex_rollout_jsonl_fixture_maps_only_canonical_messages_and_provenance"
    CODEX_RICH = "test_codex_rollout_rich_fixture_keeps_visible_messages_only"
    COPILOT_ACTIVITY = "test_copilot_activity_csv_fixture_is_grouped_and_chronological"
    COPILOT_CLI = "test_copilot_cli_v1_fixture_maps_text_and_safe_provenance"
    COPILOT_CLI_RESUMED = "test_copilot_cli_resumed_fixture_accepts_legacy_message_envelopes"
    DEEPSEEK_HARNESS = "test_deepseek_harness_v4_fixture_maps_text_and_safe_provenance"
    DEEPSEEK_HARNESS_V0 = "test_deepseek_harness_v0_descendant_accepts_compact_messages"
    DEEPSEEK_SHARE = "test_deepseek_share_json_fixture_flattens_conversational_fragments"
    DROID_ONE_SHOT = "test_droid_exec_one_shot_fixture_maps_prompt_result_and_provenance"
    DROID_STREAM = "test_droid_exec_stream_fixture_coalesces_only_completed_visible_turns"
    GEMINI_EXPORT = "test_gemini_cli_export_session_fixture_keeps_visible_text_and_provenance"
    GEMINI_SHARED = "test_gemini_cli_shared_history_fixture_excludes_injected_context_and_tools"
    HERMES = "test_hermes_fixture_maps_live_text_and_safe_provenance"
    HERMES_MULTI = "test_hermes_multi_session_fixture_preserves_boundaries"
    MANUAL = "test_manual_transcript_fixture_returns_unified_payload"
    OPENCODE = "test_opencode_session_json_fixture_maps_text_and_provenance"
    PI = "test_pi_session_fixture_reconstructs_only_the_active_branch"
    PI_VARIANTS = "test_pi_session_detects_variants_and_omits_private_records"
    QWEN_JSON = "test_qwen_code_json_fixture_maps_text_and_safe_provenance"
    QWEN_JSONL = "test_qwen_code_jsonl_fixture_maps_canonical_export"


@dataclass(frozen=True)
class ClientPin:
    client: SourceClient
    kind: PinKind
    value: PinValue


@dataclass(frozen=True)
class ImporterCompatibility:
    importer: ImporterId
    pins: tuple[ClientPin, ...]
    artifact: str
    default_location: str
    contract: ArtifactContract
    supported_envelope: str
    fixtures: tuple[Fixture, ...]
    tests: tuple[FocusedTest, ...]
    known_omissions: str
    fallback: str


def _pin(client: SourceClient, kind: PinKind, value: PinValue) -> ClientPin:
    return ClientPin(client=client, kind=kind, value=value)


IMPORTER_COMPATIBILITY: tuple[ImporterCompatibility, ...] = (
    ImporterCompatibility(
        ImporterId.CLAUDE_CODE_SESSION_JSONL,
        (_pin(SourceClient.CLAUDE_CODE, PinKind.FORMAT_VERSION, PinValue.CLAUDE_OBSERVED_JSONL),),
        "Copy a closed top-level session JSONL file.",
        "$CLAUDE_CONFIG_DIR/projects/<project>/*.jsonl (defaults to ~/.claude)",
        ArtifactContract.OBSERVED_INTERNAL,
        "Observed session-scoped JSONL user and assistant message envelopes.",
        (Fixture.CLAUDE_CODE_SESSION,),
        (FocusedTest.CLAUDE_CODE,),
        "Subagents, system prompts, thinking, tools, usage, compaction, and snapshots.",
        "Export a readable transcript and use `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.CODEX_ROLLOUT_JSONL,
        (_pin(SourceClient.CODEX, PinKind.EXACT_CLIENT_RELEASE, PinValue.CODEX_0_152_0),),
        "Copy a closed persisted rollout JSONL file.",
        "$CODEX_HOME/sessions/YYYY/MM/DD/ (defaults to ~/.codex/sessions)",
        ArtifactContract.OBSERVED_INTERNAL,
        "Observed `session_meta` plus canonical user/assistant `response_item` messages.",
        (Fixture.CODEX_ROLLOUT, Fixture.CODEX_ROLLOUT_RICH),
        (FocusedTest.CODEX, FocusedTest.CODEX_RICH),
        "Reasoning, instructions, tools, approvals, usage, startup context, and result-only streams.",
        "Create a speaker-labelled transcript and use `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.COPILOT_ACTIVITY_CSV,
        (
            _pin(
                SourceClient.MICROSOFT_COPILOT,
                PinKind.ROLLING_SERVICE,
                PinValue.COPILOT_ACTIVITY_HISTORY_CSV,
            ),
        ),
        "Export all activity history from the Microsoft privacy dashboard.",
        "Browser download directory; no stable local application path.",
        ArtifactContract.DOCUMENTED_EXPORT,
        "CSV with exact `Conversation`, `Time`, `Author`, and `Message` columns.",
        (Fixture.COPILOT_ACTIVITY,),
        (FocusedTest.COPILOT_ACTIVITY,),
        "Non-conversation activity and account-specific alternate export shapes.",
        "Convert reviewed content to a speaker-labelled transcript and use `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.COPILOT_CLI_EVENTS_JSONL,
        (
            _pin(
                SourceClient.GITHUB_COPILOT_CLI,
                PinKind.EXACT_CLIENT_RELEASE,
                PinValue.COPILOT_CLI_1_0_88,
            ),
        ),
        "Copy an inactive session's `events.jsonl`.",
        "$COPILOT_HOME/session-state/<id>/events.jsonl (defaults to ~/.copilot)",
        ArtifactContract.OBSERVED_INTERNAL,
        "Observed start/resume metadata and top-level message envelopes; legacy 1.0.54 is tested.",
        (Fixture.COPILOT_CLI_V1, Fixture.COPILOT_CLI_RESUMED),
        (FocusedTest.COPILOT_CLI, FocusedTest.COPILOT_CLI_RESUMED),
        "System/synthetic prompts, reasoning, subagents, tools, permissions, and telemetry.",
        "Use `/share file <PATH>` and import the Markdown with `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.DEEPSEEK_HARNESS_SESSION_JSONL,
        (
            _pin(
                SourceClient.DEEPSEEK_HARNESS,
                PinKind.FORMAT_VERSION,
                PinValue.DEEPSEEK_HARNESS_0_TO_4,
            ),
        ),
        "Download a session log, safely extract it, and select the root session JSONL.",
        "Extracted `session.jsonl` or `session.vN.jsonl`; no stable local path.",
        ArtifactContract.DOCUMENTED_EXPORT,
        "Canonical Harness session formats 0 through 4.",
        (Fixture.DEEPSEEK_HARNESS_V0, Fixture.DEEPSEEK_HARNESS_V4),
        (FocusedTest.DEEPSEEK_HARNESS, FocusedTest.DEEPSEEK_HARNESS_V0),
        "ZIPs, attachments, internal context, reasoning, tools, usage, and merged descendants.",
        "Export visible content as a speaker-labelled transcript and use `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.DEEPSEEK_SHARE_JSON,
        (
            _pin(
                SourceClient.DEEPSEEK_WEB,
                PinKind.ROLLING_SERVICE,
                PinValue.DEEPSEEK_SHARE_API_V0,
            ),
        ),
        "Save one conversation's share-content response as JSON.",
        "Browser download or saved API response; no stable local path.",
        ArtifactContract.OBSERVED_INTERNAL,
        "Successful share response with messages under `data.biz_data`.",
        (Fixture.DEEPSEEK_SHARE,),
        (FocusedTest.DEEPSEEK_SHARE,),
        "Account history, search snippets, and unrelated browser-extension JSON.",
        "Copy reviewed visible text into a speaker-labelled file and use `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.DROID_EXEC_JSON,
        (
            _pin(
                SourceClient.FACTORY_DROID,
                PinKind.REPOSITORY_CONTRACT,
                PinValue.DROID_CAPTURE_V1,
            ),
        ),
        "Record `droid exec` in the versioned ai-memory-hub capture envelope.",
        "Recorder-selected path; Droid has no portable history export.",
        ArtifactContract.HUB_DEFINED,
        "Capture version 1 with `one-shot` and `stream-jsonrpc` kinds.",
        (Fixture.DROID_ONE_SHOT, Fixture.DROID_STREAM),
        (FocusedTest.DROID_ONE_SHOT, FocusedTest.DROID_STREAM),
        "Raw result JSON, TUI history, share links, telemetry, and incomplete turns.",
        "Capture prompt and response in a speaker-labelled file and use `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.GEMINI_CLI_SESSION_JSON,
        (
            _pin(
                SourceClient.GEMINI_CLI,
                PinKind.FORMAT_VERSION,
                PinValue.GEMINI_EXPORT_ENVELOPES,
            ),
        ),
        "Run `/chat share <FILE>` or legacy `/export-session <FILE>`.",
        "User-selected export path; do not use autosaved internal JSONL.",
        ArtifactContract.DOCUMENTED_EXPORT,
        "Session-object and shared-history JSON export envelopes.",
        (Fixture.GEMINI_EXPORT, Fixture.GEMINI_SHARED),
        (FocusedTest.GEMINI_EXPORT, FocusedTest.GEMINI_SHARED),
        "Autosaved JSONL, injected context, thoughts, tools, token counts, and diagnostics.",
        "Export or copy a readable transcript and use `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.HERMES_SESSION_JSONL,
        (
            _pin(
                SourceClient.HERMES_AGENT,
                PinKind.FORMAT_VERSION,
                PinValue.HERMES_SESSIONS_EXPORT,
            ),
        ),
        "Run `hermes sessions export <FILE> [--redact]`.",
        "User-selected path; live state defaults to ~/.hermes/state.db.",
        ArtifactContract.DOCUMENTED_EXPORT,
        "One complete Hermes session object per JSONL line.",
        (Fixture.HERMES_SESSIONS,),
        (FocusedTest.HERMES, FocusedTest.HERMES_MULTI),
        "Live databases, Markdown, rewound rows, synthetic context, tools, and identifiers.",
        "Use a reviewed Hermes Markdown export with `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.MANUAL,
        (
            _pin(
                SourceClient.MANUAL_TRANSCRIPT,
                PinKind.USER_AUTHORED,
                PinValue.MANUAL_SPEAKER_LABELLED,
            ),
        ),
        "Create UTF-8 text with supported speaker labels.",
        "User-selected text file or standard input.",
        ArtifactContract.USER_AUTHORED,
        "Speaker-labelled text with non-empty user and assistant messages.",
        (Fixture.MANUAL_TRANSCRIPT,),
        (FocusedTest.MANUAL,),
        "Structured provenance, branches, tools, attachments, and ambiguous text.",
        "Correct the speaker labels or write a source-specific importer.",
    ),
    ImporterCompatibility(
        ImporterId.OPENCODE_SESSION_JSON,
        (
            _pin(
                SourceClient.OPENCODE,
                PinKind.FORMAT_VERSION,
                PinValue.OPENCODE_SESSION_EXPORT,
            ),
        ),
        "Run `opencode session export <ID> --sanitize` (legacy: `opencode export <ID>`).",
        "Standard output or user-selected file; do not copy internal storage.",
        ArtifactContract.DOCUMENTED_EXPORT,
        "OpenCode session export JSON with `info` and ordered messages.",
        (Fixture.OPENCODE_SESSION,),
        (FocusedTest.OPENCODE,),
        "Internal storage, reasoning, tool-only turns, and synthetic/ignored text.",
        "Export Markdown or copy visible text and use `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.PI_SESSION_JSONL,
        (
            _pin(SourceClient.PI, PinKind.FORMAT_VERSION, PinValue.PI_SESSION_V3),
            _pin(SourceClient.OH_MY_PI, PinKind.FORMAT_VERSION, PinValue.OH_MY_PI_SESSION_V3),
            _pin(SourceClient.OPENCLAW, PinKind.FORMAT_VERSION, PinValue.OPENCLAW_LEGACY_V3),
        ),
        "Copy a closed version 3 JSONL tree or coordinated OpenClaw archive.",
        "$PI_CODING_AGENT_DIR/sessions; OpenClaw archive location varies.",
        ArtifactContract.OBSERVED_INTERNAL,
        "Version 3 Pi-family trees, Oh My Pi headers, and legacy OpenClaw variants.",
        (Fixture.PI_SESSION, Fixture.OH_MY_PI_SESSION, Fixture.OPENCLAW_SESSION),
        (FocusedTest.PI, FocusedTest.PI_VARIANTS),
        "HTML, abandoned branches, live OpenClaw SQLite, thinking, tools, and usage.",
        "Export visible content as a speaker-labelled transcript and use `manual`.",
    ),
    ImporterCompatibility(
        ImporterId.QWEN_CODE_SESSION_EXPORT,
        (
            _pin(
                SourceClient.QWEN_CODE,
                PinKind.FORMAT_VERSION,
                PinValue.QWEN_JSON_AND_JSONL,
            ),
        ),
        "Run `/export json` or `/export jsonl`.",
        "$QWEN_HOME/projects/<project>/chats; export path varies.",
        ArtifactContract.DOCUMENTED_EXPORT,
        "Self-contained JSON, metadata-first JSONL, or bounded message-only JSONL.",
        (Fixture.QWEN_JSON, Fixture.QWEN_JSONL),
        (FocusedTest.QWEN_JSON, FocusedTest.QWEN_JSONL),
        "Session listings, presentation exports, system records, tools, usage, and file lists.",
        "Use a reviewed Markdown export with `manual`.",
    ),
)
