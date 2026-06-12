from __future__ import annotations

import argparse
import json
from pathlib import Path

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
