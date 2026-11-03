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


def test_responses_provider_config_is_written_for_codex(tmp_path: Path) -> None:
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
    assert "handoff_at" in tool_call["function"]["arguments"]


def test_gateway_moves_to_next_tool_after_prior_tool_result() -> None:
    messages = [
        {"role": "assistant", "tool_calls": [{"function": {"name": "memory_validate"}}]},
        {"role": "tool", "name": "memory_validate", "content": '{"status":"ok"}'},
    ]

    response = real_client_smoke._openai_chat_response({"model": "m", "messages": messages})

    tool_call = response["choices"][0]["message"]["tool_calls"][0]
    assert tool_call["function"]["name"] == "memory_insert"


def test_responses_gateway_requests_fact_search_after_ask() -> None:
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
    assert output["name"] == "memory_fact_search"
    assert output["namespace"] == "mcp__ai_memory_hub"
    assert arguments == {"subject": "user", "predicate": "likes", "response_format": "concise"}
    assert real_client_smoke.SMOKE_FACT_OBJECT in json.dumps(
        real_client_smoke._tool_input("memory_insert")
    )


def test_responses_gateway_uses_the_clients_exposed_mcp_tool_name() -> None:
    response = real_client_smoke._openai_responses_response(
        {
            "input": [{"type": "message", "content": "go"}],
            "tools": [{"type": "function", "name": "ai-memory-hub_memory_validate"}],
        }
    )

    assert response["output"][0]["name"] == "ai-memory-hub_memory_validate"


def test_responses_gateway_waits_for_opencode_mcp_discovery() -> None:
    response = real_client_smoke._openai_responses_response(
        {
            "input": [{"type": "message", "content": "go"}],
            "tools": [
                {"type": "function", "name": "execute"},
                {"type": "function", "name": "experimental_image_generate"},
                {"type": "function", "name": "shell"},
            ],
        }
    )

    output = response["output"][0]
    assert output["name"] == "shell"
    assert "time.sleep(2)" in output["arguments"]


def test_tool_sequence_ignores_names_in_injected_mcp_instructions() -> None:
    messages = [
        {"type": "message", "role": "user", "content": "go"},
        {
            "type": "message",
            "role": "developer",
            "content": "Use memory_validate, memory_insert, memory_search, memory_retrieve, memory_ask, and memory_fact_search.",
        },
    ]

    assert real_client_smoke._next_tool_name(messages) == "memory_validate"


def test_real_client_smoke_retrieve_uses_concise_response_format() -> None:
    arguments = real_client_smoke._tool_input(
        "memory_retrieve",
        {"insert_result": {"id": "11111111-2222-4333-8444-555555555555"}},
    )

    assert arguments == {
        "id": "11111111-2222-4333-8444-555555555555",
        "response_format": "concise",
    }


def test_real_client_smoke_search_resumes_only_handoffs() -> None:
    arguments = real_client_smoke._tool_input("memory_search")

    assert arguments["handoff_only"] is True
    assert arguments["query"] == real_client_smoke.SMOKE_MARKER


def test_responses_stream_emits_tool_arguments_and_completed_event() -> None:
    response = real_client_smoke._openai_responses_response(
        {"model": "amh-smoke-model", "input": [{"type": "message", "content": "go"}]}
    )

    events = list(real_client_smoke._openai_responses_stream_events(response))
    event_types = [event["type"] for event in events]

    assert response["output"][0]["type"] == "function_call"
    assert response["output"][0]["name"] == "memory_validate"
    assert response["output"][0]["namespace"] == "mcp__ai_memory_hub"
    assert "response.function_call_arguments.done" in event_types
    assert event_types[-1] == "response.completed"
    assert events[-1]["response"]["status"] == "completed"


def test_sse_frames_encode_events_and_done_marker() -> None:
    frames = list(
        real_client_smoke._sse_frames([{"type": "response.completed", "ok": True}])
    )

    assert frames[0].startswith(b"event: response.completed\n")
    assert b'data: {"type": "response.completed", "ok": true}\n\n' in frames[0]
    assert frames[-1] == b"data: [DONE]\n\n"


def test_unsupported_tool_call_is_reported_as_dispatch_error(tmp_path: Path) -> None:
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
        allow_all_skipped=True,
    )

    result = real_client_smoke.run_harness(args)
    real_client_smoke._write_json(tmp_path / "summary.json", real_client_smoke._result_to_dict(result))

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "skipped"
    assert summary["clients"][0]["status"] == "skipped"


def test_all_skipped_clients_fail_without_explicit_local_opt_out() -> None:
    results = [
        real_client_smoke.ClientResult("claude", "skipped", "not configured", [])
    ]

    assert (
        real_client_smoke._aggregate_status(results, require_success_for=())
        == "failed"
    )
    assert (
        real_client_smoke._aggregate_status(
            results, require_success_for=(), allow_all_skipped=True
        )
        == "skipped"
    )


def test_real_client_workflow_explicitly_requires_a_success() -> None:
    workflow = (
        Path(__file__).parents[2] / ".github/workflows/real-client-mcp-smoke.yml"
    ).read_text(encoding="utf-8")

    assert "npm install --global @opencode/cli@2.2.5" in workflow
    assert "--client opencode" in workflow
    assert "--require-success-for opencode" in workflow


def test_opencode_has_a_headless_local_gateway_command(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    artifact_dir = tmp_path / "artifacts"
    spec = real_client_smoke._client_spec(
        name="opencode",
        gateway_url="http://127.0.0.1:9000",
        hub_url="http://127.0.0.1:8000",
        workspace=workspace,
    )

    command = real_client_smoke._resolve_command(
        spec, prompt="Use the memory tools.", artifact_dir=artifact_dir
    )
    config = json.loads(Path(spec.env["OPENCODE_CONFIG"]).read_text(encoding="utf-8"))

    assert command == [
        "opencode",
        "run",
        "--standalone",
        "--model",
        "local-smoke/amh-smoke-model",
        "Use the memory tools.",
    ]
    assert config["provider"]["local-smoke"]["npm"] == "@ai-sdk/openai"
    assert config["provider"]["local-smoke"]["options"]["apiKey"] == "{env:AMH_REAL_CLIENT_TEST_API_KEY}"
    assert config["mcp"]["servers"]["ai-memory-hub"]["url"] == "http://127.0.0.1:8000/mcp/"
    assert config["mcp"]["servers"]["ai-memory-hub"]["codemode"] is False


def test_run_harness_removes_temporary_workspace(tmp_path: Path, monkeypatch) -> None:
    artifact_dir = tmp_path / "artifacts"
    workspace = tmp_path / "workspace"
    created_paths = [str(artifact_dir), str(workspace)]
    hub_process = object()
    gateway_process = object()
    terminated: list[object] = []

    monkeypatch.setattr(real_client_smoke.tempfile, "mkdtemp", lambda prefix: created_paths.pop(0))
    monkeypatch.setattr(real_client_smoke, "_start_hub", lambda **kwargs: hub_process)
    monkeypatch.setattr(real_client_smoke, "_start_gateway", lambda **kwargs: gateway_process)
    monkeypatch.setattr(real_client_smoke, "_wait_for_hub", lambda *args, **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_wait_for_gateway", lambda *args, **kwargs: None)
    monkeypatch.setattr(real_client_smoke, "_terminate_process", terminated.append)
    monkeypatch.delenv("AMH_REAL_CLIENT_CLAUDE_COMMAND", raising=False)
    args = argparse.Namespace(
        artifact_dir=None,
        client=["claude"],
        hub_url=None,
        gateway_url=None,
        startup_timeout=1,
        client_timeout=1,
        require_configured=False,
        require_success_for=[],
        allow_all_skipped=True,
    )

    result = real_client_smoke.run_harness(args)

    assert result.status == "skipped"
    assert artifact_dir.exists()
    assert not workspace.exists()
    assert terminated == [gateway_process, hub_process]


def test_run_harness_removes_temporary_workspace_after_startup_failure(
    tmp_path: Path, monkeypatch
) -> None:
    artifact_dir = tmp_path / "artifacts"
    workspace = tmp_path / "workspace"
    created_paths = [str(artifact_dir), str(workspace)]
    hub_process = object()
    terminated: list[object | None] = []

    monkeypatch.setattr(real_client_smoke.tempfile, "mkdtemp", lambda prefix: created_paths.pop(0))
    monkeypatch.setattr(real_client_smoke, "_start_hub", lambda **kwargs: hub_process)

    def fail_gateway(**kwargs) -> None:
        _ = kwargs
        raise RuntimeError("gateway failed")

    monkeypatch.setattr(real_client_smoke, "_start_gateway", fail_gateway)
    monkeypatch.setattr(real_client_smoke, "_terminate_process", terminated.append)
    args = argparse.Namespace(
        artifact_dir=None,
        client=["claude"],
        hub_url=None,
        gateway_url=None,
        startup_timeout=1,
        client_timeout=1,
        require_configured=False,
        require_success_for=[],
        allow_all_skipped=True,
    )

    with pytest.raises(RuntimeError, match="gateway failed"):
        real_client_smoke.run_harness(args)

    assert artifact_dir.exists()
    assert not workspace.exists()
    assert terminated == [None, hub_process]
