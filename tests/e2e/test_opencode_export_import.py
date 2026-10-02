from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.importers import OpenCodeSessionJsonImporter

OLLAMA_BASE_URL = os.environ.get("AMH_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
CHAT_MODEL = os.environ.get("AMH_OLLAMA_CHAT_MODEL", "qwen2.5:0.5b")
SMOKE_MARKER = "opencode-memory"


def test_opencode_export_round_trips_through_hub(tmp_path: Path) -> None:
    _require_ollama()
    opencode = shutil.which("opencode")
    if opencode is None:
        pytest.skip("OpenCode CLI is not installed")

    workspace = tmp_path / "workspace"
    config_dir = tmp_path / "opencode-config"
    workspace.mkdir()
    config_dir.mkdir()
    (config_dir / "opencode.json").write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "model": f"ollama/{CHAT_MODEL}",
                "provider": {
                    "ollama": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Ollama (smoke)",
                        "options": {"baseURL": f"{OLLAMA_BASE_URL}/v1"},
                        "models": {CHAT_MODEL: {"name": CHAT_MODEL}},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "OPENCODE_CONFIG_DIR": str(config_dir),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_STATE_HOME": str(tmp_path / "state"),
    }
    random_value = f"{SMOKE_MARKER}-{uuid4().hex}"
    prompt = f"Memorize this random identifier: {random_value}. Confirm briefly."

    command = [
        opencode,
        "run",
        "--pure",
        "--format",
        "json",
        "--model",
        f"ollama/{CHAT_MODEL}",
        "--title",
        "OpenCode export importer smoke",
        prompt,
    ]
    try:
        run = subprocess.run(
            command,
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        run_stdout = run.stdout
        run_stderr = run.stderr
        run_succeeded = run.returncode == 0
    except subprocess.TimeoutExpired as exc:
        run_stdout = _output_text(exc.stdout)
        run_stderr = _output_text(exc.stderr)
        run_succeeded = _has_completed_step(run_stdout)

    _write_artifact("run.stdout.jsonl", run_stdout)
    _write_artifact("run.stderr.log", run_stderr)
    assert run_succeeded, run_stderr or run_stdout

    events = [_json_line(line) for line in run_stdout.splitlines() if line.strip()]
    session_id = next(
        (
            event["sessionID"]
            for event in events
            if isinstance(event, dict)
            and isinstance(event.get("sessionID"), str)
            and event["sessionID"]
        ),
        None,
    )
    assert session_id is not None, run_stdout

    exported = subprocess.run(
        [opencode, "export", session_id, "--pure"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    _write_artifact("session-export.json", exported.stdout)
    _write_artifact("export.stderr.log", exported.stderr)
    assert exported.returncode == 0, exported.stderr

    payload = OpenCodeSessionJsonImporter().import_text(exported.stdout)[0]

    assert payload["source"] == "opencode"
    assert payload["metadata"]["source_session_id"] == session_id
    assert payload["metadata"]["model"] == f"ollama/{CHAT_MODEL}"
    assert [message["role"] for message in payload["messages"]] == [
        "user",
        "assistant",
    ]
    assert random_value in payload["messages"][0]["text"]
    assert payload["messages"][1]["text"].strip()

    with _hub_client(tmp_path / "hub") as client:
        inserted = client.post("/memory/insert", json=payload)
        _write_artifact("hub-insert.json", inserted.text)
        assert inserted.status_code == 200, inserted.text
        inserted_body = inserted.json()
        assert inserted_body["status"] == "ok"
        assert isinstance(inserted_body["id"], str)

        queried = client.post(
            "/memory/ask",
            json={
                "question": "Which random identifier was memorized?",
                "top_k": 5,
                "source": "opencode",
                "source_session_id": session_id,
            },
        )
        _write_artifact("hub-query.json", queried.text)
        assert queried.status_code == 200, queried.text
        queried_body = queried.json()
        assert queried_body["status"] == "ok"
        assert queried_body["answer"]
        assert queried_body["results"]
        assert queried_body["results"][0]["id"] == inserted_body["id"]
        assert any(
            random_value in citation["text"]
            for citation in queried_body["citations"]
        )
        assert random_value in queried_body["answer"]


def _hub_client(data_dir: Path) -> TestClient:
    return TestClient(
        create_app(
            config={
                "interfaces": {"mcp": False, "api": True},
                "embedding_endpoint": {
                    "base_url": f"{OLLAMA_BASE_URL.rstrip('/')}/v1",
                    "api_key": "ollama",
                },
                "paths": {"data_dir": str(data_dir)},
                "providers": {
                    "embeddings": "http",
                    "embedding_model": "nomic-embed-text",
                    "embedding_dimension": 768,
                    "metadata_db": "sqlite",
                    "vector_db": "memory",
                },
            }
        )
    )


def _require_ollama() -> None:
    if os.environ.get("AMH_RUN_OPENCODE_SMOKE") != "1":
        pytest.skip("set AMH_RUN_OPENCODE_SMOKE=1 to run the live OpenCode smoke")
    try:
        with urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=2) as response:
            if response.status >= 400:
                pytest.skip(f"Ollama returned HTTP {response.status}")
            installed = {
                model.get("name")
                for model in json.load(response).get("models", [])
                if isinstance(model, dict)
            }
    except (OSError, URLError) as exc:
        pytest.skip(f"Ollama is not reachable at {OLLAMA_BASE_URL}: {exc}")
    if CHAT_MODEL not in installed:
        pytest.skip(f"Ollama model {CHAT_MODEL!r} is not installed")


def _json_line(line: str) -> Any:
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"OpenCode emitted a non-JSON line: {line}") from exc


def _output_text(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _has_completed_step(output: str) -> bool:
    for line in output.splitlines():
        if not line.strip():
            continue
        event = _json_line(line)
        if not isinstance(event, dict):
            continue
        if event.get("type") == "step_finish":
            return True
        part = event.get("part")
        if isinstance(part, dict) and part.get("type") == "step-finish":
            return True
    return False


def _write_artifact(name: str, content: str) -> None:
    artifact_dir = os.environ.get("AMH_OPENCODE_SMOKE_ARTIFACT_DIR")
    if not artifact_dir:
        return
    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(content, encoding="utf-8")
