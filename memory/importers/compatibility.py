from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ArtifactContract(StrEnum):
    """Describe who owns an import artifact's compatibility contract."""

    DOCUMENTED_EXPORT = "Documented user-facing export"
    OBSERVED_INTERNAL = "Observed internal format"
    HUB_DEFINED = "ai-memory-hub-defined capture"
    USER_AUTHORED = "User-authored transcript"


@dataclass(frozen=True)
class EvidenceReference:
    """Point to repository evidence for one compatibility statement."""

    path: str
    symbol: str | None = None


@dataclass(frozen=True)
class ImporterCompatibility:
    """Document the bounded compatibility contract for one registered importer."""

    importer: str
    source: str
    artifact: str
    default_location: str
    contract: ArtifactContract
    verified_client_version: str | None
    verification_date: str | None
    supported_envelope: str
    fixtures: tuple[EvidenceReference, ...]
    tests: tuple[EvidenceReference, ...]
    known_omissions: str
    fallback: str


IMPORTER_COMPATIBILITY: tuple[ImporterCompatibility, ...] = (
    ImporterCompatibility(
        importer="claude-code-session-jsonl",
        source="Claude Code",
        artifact="Copy a closed top-level session JSONL file.",
        default_location="$CLAUDE_CONFIG_DIR/projects/<project>/*.jsonl (defaults to ~/.claude)",
        contract=ArtifactContract.OBSERVED_INTERNAL,
        verified_client_version=None,
        verification_date=None,
        supported_envelope=(
            "Observed JSONL message envelopes with session-scoped UUIDs and structured user or "
            "assistant content."
        ),
        fixtures=(
            EvidenceReference("tests/fixtures/importers/claude_code_session_anonymized.jsonl"),
        ),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_claude_code_session_fixture_keeps_visible_text_and_safe_provenance",
            ),
        ),
        known_omissions=(
            "Subagent files, system prompts, thinking, tools, usage, compaction summaries, and "
            "file snapshots are not imported. claude.ai web chats are a different format."
        ),
        fallback="Export a readable transcript and use `manual`.",
    ),
    ImporterCompatibility(
        importer="codex-rollout-jsonl",
        source="Codex CLI and Codex app",
        artifact="Copy a closed persisted rollout JSONL file.",
        default_location="$CODEX_HOME/sessions/YYYY/MM/DD/ (defaults to ~/.codex/sessions)",
        contract=ArtifactContract.OBSERVED_INTERNAL,
        verified_client_version="Codex CLI 0.152.0",
        verification_date="2026-10-02",
        supported_envelope=(
            "Observed `session_meta` plus canonical user/assistant `response_item` messages; "
            "older simplified records without turn IDs remain accepted."
        ),
        fixtures=(
            EvidenceReference("tests/fixtures/importers/codex_rollout_anonymized.jsonl"),
            EvidenceReference("tests/fixtures/importers/codex_rollout_rich_anonymized.jsonl"),
        ),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_codex_rollout_jsonl_fixture_maps_only_canonical_messages_and_provenance",
            ),
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_codex_rollout_rich_fixture_keeps_visible_messages_only",
            ),
        ),
        known_omissions=(
            "The rollout is not a public API. Reasoning, instructions, tools, approvals, usage, "
            "startup context, and result-only `codex exec --json` streams are excluded."
        ),
        fallback="Create a speaker-labelled transcript and use `manual`.",
    ),
    ImporterCompatibility(
        importer="copilot-activity-csv",
        source="Microsoft Copilot",
        artifact="Export all activity history from the Microsoft privacy dashboard.",
        default_location="Browser download directory; no stable local application path.",
        contract=ArtifactContract.DOCUMENTED_EXPORT,
        verified_client_version=None,
        verification_date=None,
        supported_envelope="CSV with exact `Conversation`, `Time`, `Author`, and `Message` columns.",
        fixtures=(EvidenceReference("tests/fixtures/importers/copilot_activity_anonymized.csv"),),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_copilot_activity_csv_fixture_is_grouped_and_chronological",
            ),
        ),
        known_omissions=(
            "Work or school accounts may expose different controls. Extra export artifacts and "
            "non-conversation activity are not supported."
        ),
        fallback="Convert reviewed content to a speaker-labelled transcript and use `manual`.",
    ),
    ImporterCompatibility(
        importer="copilot-cli-events-jsonl",
        source="GitHub Copilot CLI",
        artifact="Copy an inactive session's `events.jsonl`.",
        default_location="$COPILOT_HOME/session-state/<id>/events.jsonl (defaults to ~/.copilot)",
        contract=ArtifactContract.OBSERVED_INTERNAL,
        verified_client_version=None,
        verification_date=None,
        supported_envelope=(
            "Observed `session.start` and `session.resume` metadata plus minimal and current "
            "top-level message envelopes. Fixtures declare Copilot CLI 1.0.54 and 1.0.88, but "
            "those versions were not independently verified."
        ),
        fixtures=(
            EvidenceReference(
                "tests/fixtures/importers/copilot_cli_events_resumed_anonymized.jsonl"
            ),
            EvidenceReference("tests/fixtures/importers/copilot_cli_events_v1_anonymized.jsonl"),
        ),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_copilot_cli_v1_fixture_maps_text_and_safe_provenance",
            ),
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_copilot_cli_resumed_fixture_accepts_legacy_message_envelopes",
            ),
        ),
        known_omissions=(
            "System and synthetic prompts, reasoning, subagents, tools, permissions, telemetry, "
            "and malformed historical multiline events are excluded."
        ),
        fallback="Use Copilot CLI `/share file <PATH>` and import the Markdown with `manual`.",
    ),
    ImporterCompatibility(
        importer="deepseek-harness-session-jsonl",
        source="DeepSeek Harness",
        artifact="Download a session log, safely extract it, and select the root session JSONL.",
        default_location="Extracted `session.jsonl` or `session.vN.jsonl`; no stable local path.",
        contract=ArtifactContract.DOCUMENTED_EXPORT,
        verified_client_version=None,
        verification_date=None,
        supported_envelope="Canonical Harness session format versions 0 through 4.",
        fixtures=(
            EvidenceReference(
                "tests/fixtures/importers/deepseek_harness_session_v0_descendant_anonymized.jsonl"
            ),
            EvidenceReference(
                "tests/fixtures/importers/deepseek_harness_session_v4_anonymized.jsonl"
            ),
        ),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_deepseek_harness_v4_fixture_maps_text_and_safe_provenance",
            ),
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_deepseek_harness_v0_descendant_accepts_compact_messages",
            ),
        ),
        known_omissions=(
            "ZIP files, attachments, system/request context, internal messages, reasoning, tools, "
            "usage, and automatic merging of descendant sessions are excluded."
        ),
        fallback="Export visible content as a speaker-labelled transcript and use `manual`.",
    ),
    ImporterCompatibility(
        importer="deepseek-share-json",
        source="DeepSeek web",
        artifact="Save one conversation's share-content response as JSON.",
        default_location="Browser download or explicitly saved API response; no stable local path.",
        contract=ArtifactContract.OBSERVED_INTERNAL,
        verified_client_version=None,
        verification_date=None,
        supported_envelope="Observed successful share response with messages under `data.biz_data`.",
        fixtures=(EvidenceReference("tests/fixtures/importers/deepseek_share_anonymized.json"),),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_deepseek_share_json_fixture_flattens_conversational_fragments",
            ),
        ),
        known_omissions=(
            "This is not an account-history export contract. Search snippets are discarded, and "
            "unrelated browser-extension JSON is unsupported."
        ),
        fallback="Copy reviewed visible text into a speaker-labelled file and use `manual`.",
    ),
    ImporterCompatibility(
        importer="droid-exec-json",
        source="Factory Droid",
        artifact="Record a `droid exec` run in the versioned ai-memory-hub capture envelope.",
        default_location="Recorder-selected output path; Droid has no portable history export.",
        contract=ArtifactContract.HUB_DEFINED,
        verified_client_version=None,
        verification_date=None,
        supported_envelope=(
            "`ai-memory-hub.droid-exec-capture` version 1, with `one-shot` and "
            "`stream-jsonrpc` capture kinds."
        ),
        fixtures=(
            EvidenceReference("tests/fixtures/importers/droid_exec_one_shot_anonymized.json"),
            EvidenceReference("tests/fixtures/importers/droid_exec_stream_anonymized.json"),
        ),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_droid_exec_one_shot_fixture_maps_prompt_result_and_provenance",
            ),
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_droid_exec_stream_fixture_coalesces_only_completed_visible_turns",
            ),
        ),
        known_omissions=(
            "Raw result-only JSON, TUI history, organization share links, telemetry captures, "
            "incomplete turns, reasoning, tools, permissions, and usage are excluded."
        ),
        fallback="Capture prompt and response in a speaker-labelled file and use `manual`.",
    ),
    ImporterCompatibility(
        importer="gemini-cli-session-json",
        source="Gemini CLI",
        artifact="Run `/chat share <FILE>` or legacy `/export-session <FILE>`.",
        default_location="User-selected export path; do not use autosaved internal JSONL.",
        contract=ArtifactContract.DOCUMENTED_EXPORT,
        verified_client_version=None,
        verification_date=None,
        supported_envelope=(
            "Session-object and shared-history JSON export envelopes, including compatible legacy "
            "saved-chat objects."
        ),
        fixtures=(
            EvidenceReference("tests/fixtures/importers/gemini_cli_export_session_anonymized.json"),
            EvidenceReference("tests/fixtures/importers/gemini_cli_shared_history_anonymized.json"),
        ),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_gemini_cli_export_session_fixture_keeps_visible_text_and_provenance",
            ),
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_gemini_cli_shared_history_fixture_excludes_injected_context_and_tools",
            ),
        ),
        known_omissions=(
            "Autosaved internal JSONL, injected context, thoughts, tools, token counts, diagnostics, "
            "and unknown fields are excluded."
        ),
        fallback="Export or copy a readable transcript and use `manual`.",
    ),
    ImporterCompatibility(
        importer="hermes-session-jsonl",
        source="Hermes Agent",
        artifact="Run `hermes sessions export <FILE> [--redact]`.",
        default_location="User-selected export path; live state defaults to ~/.hermes/state.db.",
        contract=ArtifactContract.DOCUMENTED_EXPORT,
        verified_client_version=None,
        verification_date=None,
        supported_envelope="One complete Hermes session object per JSONL line.",
        fixtures=(EvidenceReference("tests/fixtures/importers/hermes_sessions_anonymized.jsonl"),),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_hermes_fixture_maps_live_text_and_safe_provenance",
            ),
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_hermes_multi_session_fixture_preserves_boundaries",
            ),
        ),
        known_omissions=(
            "Live database files, Markdown exports, rewound rows, synthetic compressed context, "
            "system prompts, tools, reasoning, billing, and identifiers are excluded."
        ),
        fallback="Use a reviewed Hermes Markdown export with `manual`.",
    ),
    ImporterCompatibility(
        importer="manual",
        source="Any readable transcript",
        artifact="Create UTF-8 text with supported speaker labels.",
        default_location="User-selected text file or standard input.",
        contract=ArtifactContract.USER_AUTHORED,
        verified_client_version=None,
        verification_date=None,
        supported_envelope="Speaker-labelled text with non-empty user and assistant messages.",
        fixtures=(EvidenceReference("tests/fixtures/importers/manual_transcript_synthetic.txt"),),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_manual_transcript_fixture_returns_unified_payload",
            ),
        ),
        known_omissions=(
            "Structured provenance, branches, tools, attachments, and unlabelled or ambiguous text "
            "cannot be recovered."
        ),
        fallback="Correct the speaker labels or write a source-specific importer.",
    ),
    ImporterCompatibility(
        importer="opencode-session-json",
        source="OpenCode",
        artifact="Run `opencode session export <ID> --sanitize` (legacy: `opencode export <ID>`).",
        default_location="Standard output or user-selected file; do not copy internal storage.",
        contract=ArtifactContract.DOCUMENTED_EXPORT,
        verified_client_version=None,
        verification_date=None,
        supported_envelope="OpenCode session export JSON with `info` and ordered message records.",
        fixtures=(EvidenceReference("tests/fixtures/importers/opencode_session_anonymized.json"),),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_opencode_session_json_fixture_maps_text_and_provenance",
            ),
        ),
        known_omissions=(
            "Internal storage, reasoning, tool-only turns, synthetic/ignored text, and unsupported "
            "future export shapes are excluded."
        ),
        fallback="Export Markdown or copy visible text and use `manual`.",
    ),
    ImporterCompatibility(
        importer="pi-session-jsonl",
        source="Pi, Oh My Pi, and archived/legacy OpenClaw",
        artifact="Copy a closed version 3 JSONL session tree or coordinated OpenClaw archive.",
        default_location=(
            "$PI_CODING_AGENT_DIR/sessions (defaults to ~/.pi/agent or ~/.omp/agent); OpenClaw "
            "archive location varies."
        ),
        contract=ArtifactContract.OBSERVED_INTERNAL,
        verified_client_version=None,
        verification_date=None,
        supported_envelope=(
            "Version 3 parent-linked Pi-family JSONL trees, optional Oh My Pi title headers, and "
            "archived/legacy OpenClaw variants."
        ),
        fixtures=(
            EvidenceReference("tests/fixtures/importers/pi_session_anonymized.jsonl"),
            EvidenceReference("tests/fixtures/importers/oh_my_pi_session_anonymized.jsonl"),
            EvidenceReference("tests/fixtures/importers/openclaw_session_anonymized.jsonl"),
        ),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_pi_session_fixture_reconstructs_only_the_active_branch",
            ),
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_pi_session_detects_variants_and_omits_private_records",
            ),
        ),
        known_omissions=(
            "HTML exports, abandoned branches, live OpenClaw SQLite, system/developer messages, "
            "thinking, tools, compaction bodies, usage, and provider-private data are excluded."
        ),
        fallback="Export visible content as a speaker-labelled transcript and use `manual`.",
    ),
    ImporterCompatibility(
        importer="qwen-code-session-export",
        source="Qwen Code",
        artifact="Run `/export json` or `/export jsonl`.",
        default_location="$QWEN_HOME/projects/<project>/chats (defaults to ~/.qwen); export path varies.",
        contract=ArtifactContract.DOCUMENTED_EXPORT,
        verified_client_version=None,
        verification_date=None,
        supported_envelope=(
            "Self-contained JSON session export, canonical metadata-first JSONL, or bounded "
            "message-only JSONL with metadata supplied separately."
        ),
        fixtures=(
            EvidenceReference("tests/fixtures/importers/qwen_code_session_anonymized.json"),
            EvidenceReference("tests/fixtures/importers/qwen_code_session_anonymized.jsonl"),
        ),
        tests=(
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_qwen_code_json_fixture_maps_text_and_safe_provenance",
            ),
            EvidenceReference(
                "tests/unit/test_importers.py",
                "test_qwen_code_jsonl_fixture_maps_canonical_export",
            ),
        ),
        known_omissions=(
            "Session listings, HTML/Markdown presentation exports, system records, goal audits, "
            "tools, usage, aggregate file lists, and unknown message types are excluded."
        ),
        fallback="Use a reviewed Markdown export with `manual`.",
    ),
)
