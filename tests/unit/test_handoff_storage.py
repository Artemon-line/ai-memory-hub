from __future__ import annotations

from copy import deepcopy

from memory.backend.metadata_store import SQLiteMetadataStore


def _packet(handoff_id: str, *, goal: str = "Continue the release") -> dict[str, object]:
    return {
        "handoff_id": handoff_id,
        "project_id": None,
        "thread_id": "thread-a",
        "source_agent": "codex",
        "target_agent": None,
        "goal": {"text": goal, "citations": ["memory-a:0"]},
        "status": "active",
        "summary": [{"text": "Implementation is ready", "citations": ["memory-a:0"]}],
        "decisions": [],
        "changed_files": [],
        "commands_run": [],
        "validation": [],
        "blockers": [],
        "next_steps": [{"text": "Run focused tests", "citations": ["memory-a:0"]}],
        "citations": [
            {"memory_id": "memory-a", "chunk_index": 0, "text": "Evidence", "score": 1.0}
        ],
        "created_at": "2026-09-26T00:00:00Z",
        "updated_at": "2026-09-26T00:00:00Z",
        "expires_at": None,
        "confidence": "high",
        "completeness_notes": [],
        "context_tokens_used": 12,
        "context_token_budget": 100,
        "context_truncated": False,
    }


def test_sqlite_handoff_storage_redacts_and_scopes_records(tmp_path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    store.create_project(project_id="project-a", owner_id="owner-a")
    packet = _packet("handoff-a", goal="Use Bearer sk-secret-value when resuming")

    created = store.create_handoff(packet, owner_id="owner-a", project_id="project-a")

    assert "sk-secret-value" not in str(created)
    assert store.get_handoff(
        "handoff-a", owner_id="owner-a", project_id="project-a"
    ) == created
    assert store.get_handoff(
        "handoff-a", owner_id="owner-a", project_id="project-b"
    ) is None


def test_sqlite_handoff_supersession_preserves_original_payload(tmp_path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    store.create_project(project_id="project-a", owner_id="owner-a")
    original = store.create_handoff(
        _packet("handoff-a"), owner_id="owner-a", project_id="project-a"
    )
    replacement_payload = _packet("handoff-b", goal="Continue from reviewed state")

    replacement = store.supersede_handoff(
        "handoff-a",
        replacement_payload,
        owner_id="owner-a",
        project_id="project-a",
    )

    assert replacement is not None
    assert replacement["supersedes_handoff_id"] == "handoff-a"
    superseded = store.get_handoff(
        "handoff-a", owner_id="owner-a", project_id="project-a"
    )
    assert superseded is not None
    assert superseded["status"] == "superseded"
    assert superseded["superseded_by_handoff_id"] == "handoff-b"
    assert superseded["goal"] == original["goal"]
    assert [item["handoff_id"] for item in store.search_handoffs(
        owner_id="owner-a", project_id="project-a"
    )] == ["handoff-b"]
    assert [item["handoff_id"] for item in store.search_handoffs(
        owner_id="owner-a", project_id="project-a", status="superseded"
    )] == ["handoff-a"]
    assert store.supersede_handoff(
        "handoff-a",
        _packet("handoff-c"),
        owner_id="owner-a",
        project_id="project-a",
    ) is None


def test_handoff_create_does_not_mutate_input(tmp_path) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    store.create_project(project_id="project-a", owner_id="owner-a")
    packet = _packet("handoff-a")
    before = deepcopy(packet)

    store.create_handoff(packet, owner_id="owner-a", project_id="project-a")

    assert packet == before
