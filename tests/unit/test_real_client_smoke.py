from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from memory.tools import real_client_smoke


def test_unconfigured_client_skips_with_clear_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AMH_REAL_CLIENT_CLAUDE_COMMAND", raising=False)
    spec = real_client_smoke._client_spec(
        name="claude",
        hub_url="http://127.0.0.1:8000",
        gateway_url="http://127.0.0.1:9000",
        workspace=tmp_path,
    )

    result = real_client_smoke.run_client(
        spec=spec,
        prompt=real_client_smoke.SMOKE_PROMPT,
        hub_url="http://127.0.0.1:8000",
        artifact_dir=tmp_path,
        timeout_seconds=1,
        require_configured=False,
    )

    assert result.status == "skipped"
    assert "AMH_REAL_CLIENT_CLAUDE_COMMAND" in result.reason


def test_command_template_writes_prompt_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMH_REAL_CLIENT_CLAUDE_COMMAND", "claude -p {prompt_file}")
    spec = real_client_smoke._client_spec(
        name="claude",
        hub_url="http://127.0.0.1:8000",
        gateway_url="http://127.0.0.1:9000",
        workspace=tmp_path,
    )

    command = real_client_smoke._resolve_command(
        spec,
        prompt=real_client_smoke.SMOKE_PROMPT,
        artifact_dir=tmp_path,
    )

    assert command == ["claude", "-p", str(tmp_path / "claude.prompt.txt")]
    assert real_client_smoke.SMOKE_MARKER in (tmp_path / "claude.prompt.txt").read_text(encoding="utf-8")


def test_codex_config_uses_responses_wire_api(tmp_path: Path) -> None:
    spec = real_client_smoke._client_spec(
        name="codex",
        hub_url="http://127.0.0.1:8000",
        gateway_url="http://127.0.0.1:9000",
        workspace=tmp_path,
    )

    config = (tmp_path / "codex-home" / "config.toml").read_text(encoding="utf-8")

    assert spec.executable == "codex"
    assert 'wire_api = "responses"' in config
    assert 'base_url = "http://127.0.0.1:9000/v1"' in config
    assert 'url = "http://127.0.0.1:8000/mcp/"' in config


def test_gateway_chat_completion_requests_tool_call() -> None:
    response = real_client_smoke._openai_chat_response({"model": "m", "messages": [{"role": "user", "content": "go"}]})

    tool_call = response["choices"][0]["message"]["tool_calls"][0]
    assert tool_call["function"]["name"] == "memory_validate"
    assert real_client_smoke.SMOKE_MARKER in tool_call["function"]["arguments"]


def test_gateway_moves_to_next_tool_after_prior_tool_result() -> None:
    messages = [
        {"role": "assistant", "tool_calls": [{"function": {"name": "memory_validate"}}]},
        {"role": "tool", "name": "memory_validate", "content": '{"status":"ok"}'},
    ]

    response = real_client_smoke._openai_chat_response({"model": "m", "messages": messages})

    tool_call = response["choices"][0]["message"]["tool_calls"][0]
    assert tool_call["function"]["name"] == "memory_insert"


def test_gateway_responses_moves_to_fact_search_after_ask() -> None:
    response = real_client_smoke._openai_responses_response(
        {
            "input": [
                {"type": "function_call", "name": "memory_validate"},
                {"type": "function_call", "name": "memory_insert"},
                {"type": "function_call", "name": "memory_search"},
                {"type": "function_call", "name": "memory_retrieve"},
                {"type": "function_call", "name": "memory_ask"},
            ]
        }
    )

    output = response["output"][0]
    arguments = json.loads(output["arguments"])
    assert output["name"] == "mcp__ai_memory_hub.memory_fact_search"
    assert arguments == {"subject": "user", "predicate": "likes", "response_format": "concise"}
    assert real_client_smoke.SMOKE_FACT_OBJECT in json.dumps(
        real_client_smoke._tool_input("memory_insert")
    )


def test_gateway_responses_stream_emits_namespaced_function_call_and_completed_event() -> None:
    response = real_client_smoke._openai_responses_response(
        {"model": "amh-smoke-model", "input": [{"type": "message", "content": "go"}]}
    )

    events = list(real_client_smoke._openai_responses_stream_events(response))
    event_types = [event["type"] for event in events]

    assert response["output"][0]["type"] == "function_call"
    assert response["output"][0]["name"] == "mcp__ai_memory_hub.memory_validate"
    assert "response.function_call_arguments.done" in event_types
    assert event_types[-1] == "response.completed"
    assert events[-1]["response"]["status"] == "completed"


def test_client_dispatch_error_detects_unsupported_tool_call(tmp_path: Path) -> None:
    log = tmp_path / "codex.stderr.log"
    log.write_text("ERROR router: unsupported call: mcp__ai_memory_hub.memory_insert\n", encoding="utf-8")

    reason = real_client_smoke._client_dispatch_error(log)

    assert reason == (
        "client did not dispatch MCP tool call: unsupported call "
        "mcp__ai_memory_hub.memory_insert"
    )


def test_summary_is_written_for_all_skipped_clients(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(real_client_smoke, "_start_hub", lambda **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_start_gateway", lambda **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_wait_for_hub", lambda *args, **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_wait_for_gateway", lambda *args, **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_terminate_process", lambda process: None)
    monkeypatch.delenv("AMH_REAL_CLIENT_CLAUDE_COMMAND", raising=False)
    args = argparse.Namespace(
        artifact_dir=str(tmp_path),
        client=["claude"],
        hub_url="http://127.0.0.1:8000",
        gateway_url="http://127.0.0.1:9000",
        startup_timeout=1,
        client_timeout=1,
        require_configured=False,
        require_success_for=[],
    )

    result = real_client_smoke.run_harness(args)
    real_client_smoke._write_json(tmp_path / "summary.json", real_client_smoke._result_to_dict(result))

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "ok"
    assert summary["clients"][0]["status"] == "skipped"


def test_run_harness_removes_temporary_workspace(tmp_path: Path, monkeypatch) -> None:
    artifact_dir = tmp_path / "artifacts"
    workspace = tmp_path / "workspace"
    created_paths = [str(artifact_dir), str(workspace)]

    monkeypatch.setattr(real_client_smoke.tempfile, "mkdtemp", lambda prefix: created_paths.pop(0))
    monkeypatch.setattr(real_client_smoke, "_start_hub", lambda **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_start_gateway", lambda **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_wait_for_hub", lambda *args, **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_wait_for_gateway", lambda *args, **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_terminate_process", lambda process: None)
    monkeypatch.delenv("AMH_REAL_CLIENT_CLAUDE_COMMAND", raising=False)
    args = argparse.Namespace(
        artifact_dir=None,
        client=["claude"],
        hub_url="http://127.0.0.1:8000",
        gateway_url="http://127.0.0.1:9000",
        startup_timeout=1,
        client_timeout=1,
        require_configured=False,
        require_success_for=[],
    )

    result = real_client_smoke.run_harness(args)

    assert result.status == "ok"
    assert artifact_dir.exists()
    assert not workspace.exists()


def test_run_harness_removes_temporary_workspace_after_startup_failure(
    tmp_path: Path, monkeypatch
) -> None:
    artifact_dir = tmp_path / "artifacts"
    workspace = tmp_path / "workspace"
    created_paths = [str(artifact_dir), str(workspace)]

    monkeypatch.setattr(real_client_smoke.tempfile, "mkdtemp", lambda prefix: created_paths.pop(0))
    monkeypatch.setattr(real_client_smoke, "_start_hub", lambda **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_start_gateway", lambda **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_wait_for_hub", lambda *args, **kwargs: None)

    def fail_gateway(*args, **kwargs) -> None:
        _ = args, kwargs
        raise RuntimeError("gateway failed")

    monkeypatch.setattr(real_client_smoke, "_wait_for_gateway", fail_gateway)
    monkeypatch.setattr(real_client_smoke, "_terminate_process", lambda process: None)
    args = argparse.Namespace(
        artifact_dir=None,
        client=["claude"],
        hub_url="http://127.0.0.1:8000",
        gateway_url="http://127.0.0.1:9000",
        startup_timeout=1,
        client_timeout=1,
        require_configured=False,
        require_success_for=[],
    )

    with pytest.raises(RuntimeError, match="gateway failed"):
        real_client_smoke.run_harness(args)

    assert artifact_dir.exists()
    assert not workspace.exists()
