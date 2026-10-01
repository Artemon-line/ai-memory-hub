from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from memory.importers import OpenCodeSessionJsonImporter

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
CHAT_MODEL = os.environ.get("AMH_OLLAMA_CHAT_MODEL", "qwen2.5:0.5b")
SMOKE_MARKER = "opencode export importer smoke"


def test_opencode_export_is_accepted_by_native_importer(tmp_path: Path) -> None:
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
    prompt = f"Reply with exactly these four words: {SMOKE_MARKER}"

    run = subprocess.run(
        [
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
        ],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    _write_artifact("run.stdout.jsonl", run.stdout)
    _write_artifact("run.stderr.log", run.stderr)
    assert run.returncode == 0, run.stderr

    events = [_json_line(line) for line in run.stdout.splitlines() if line.strip()]
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
    assert session_id is not None, run.stdout

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
    assert SMOKE_MARKER in payload["messages"][0]["text"].lower()
    assert payload["messages"][1]["text"].strip()


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


def _write_artifact(name: str, content: str) -> None:
    artifact_dir = os.environ.get("AMH_OPENCODE_SMOKE_ARTIFACT_DIR")
    if not artifact_dir:
        return
    path = Path(artifact_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(content, encoding="utf-8")
