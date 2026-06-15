from __future__ import annotations

import copy
import json
import os
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from fastapi.testclient import TestClient

from memory.api.server import create_app

OLLAMA_BASE_URL = os.getenv("AMH_OLLAMA_BASE_URL", "http://localhost:11434")
OPENAI_BASE_URL = f"{OLLAMA_BASE_URL.rstrip('/')}/v1"
CHAT_MODEL = os.getenv("AMH_OLLAMA_CHAT_MODEL", "qwen2.5:0.5b")


CLIENT_PROFILES: list[dict[str, Any]] = [
    {
        "name": "codex",
        "version": "smoke",
        "payload": {
            "source": "codex-cli",
            "conversation": [
                {
                    "role": "user",
                    "content": "Codex smoke memory: pytest should use MCP tools.",
                },
                {
                    "role": "assistant",
                    "content": "Codex smoke response: MCP insert and search are working.",
                },
            ],
            "metadata": {"saved_at": "2026-06-06T00:00:00Z"},
            "tags": ["codex", "smoke"],
        },
        "query": "Codex pytest MCP",
        "expected": {
            "source": "codex-cli",
            "metadata": {"saved_at": "2026-06-06T00:00:00Z"},
            "tags": ["codex", "smoke"],
            "first_text": "Codex smoke memory: pytest should use MCP tools.",
        },
    },
    {
        "name": "gemini-cli",
        "version": "smoke",
        "payload": {
            "source": "gemini",
            "messages": [
                {
                    "role": "user",
                    "content": "Gemini smoke memory: compare vector retrieval.",
                },
                {
                    "role": "assistant",
                    "content": "Gemini smoke response: retrieval cites the stored turn.",
                },
            ],
            "metadata": {"model": "gemini-smoke"},
        },
        "query": "Gemini vector retrieval",
        "expected": {
            "source": "gemini",
            "metadata": {"model": "gemini-smoke"},
            "tags": [],
            "first_text": "Gemini smoke memory: compare vector retrieval.",
        },
    },
    {
        "name": "vscode-copilot",
        "version": "smoke",
        "payload": {
            "source": "vscode-copilot",
            "messages": [
                {
                    "role": "user",
                    "text": "VS Code Copilot smoke memory: workspace tests run in CI.",
                },
                {
                    "role": "assistant",
                    "text": "VS Code Copilot smoke response: client metadata is preserved.",
                },
            ],
            "metadata": {"workspace": "ai-memory-hub"},
            "tags": ["copilot", "vscode", "smoke"],
        },
        "query": "Copilot workspace CI",
        "expected": {
            "source": "vscode-copilot",
            "metadata": {"workspace": "ai-memory-hub"},
            "tags": ["copilot", "vscode", "smoke"],
            "first_text": "VS Code Copilot smoke memory: workspace tests run in CI.",
        },
    },
    {
        "name": "claude-code",
        "version": "smoke",
        "payload": {
            "source": "claude-code",
            "messages": [
                {
                    "role": "user",
                    "content": "Claude smoke memory: verify Anthropic-style client context.",
                },
                {
                    "role": "assistant",
                    "content": "Claude smoke response: ai-memory-hub stores normalized messages.",
                },
            ],
            "metadata": {
                "provider": "anthropic",
                "model": "claude-smoke",
                "session_id": "claude-smoke-session",
            },
            "tags": ["claude", "smoke"],
        },
        "query": "Claude Anthropic normalized messages",
        "expected": {
            "source": "claude-code",
            "metadata": {
                "provider": "anthropic",
                "model": "claude-smoke",
                "session_id": "claude-smoke-session",
            },
            "tags": ["claude", "smoke"],
            "first_text": "Claude smoke memory: verify Anthropic-style client context.",
        },
    },
    {
        "name": "opencode",
        "version": "smoke",
        "payload": {
            "source": "opencode",
            "messages": [
                {
                    "role": "user",
                    "content": "opencode smoke memory: store local coding-agent context.",
                },
                {
                    "role": "assistant",
                    "content": "opencode smoke response: memory_ask returns citations.",
                },
            ],
            "metadata": {"provider": "ollama", "model": "opencode-smoke"},
        },
        "query": "opencode coding-agent citations",
        "expected": {
            "source": "opencode",
            "metadata": {"provider": "ollama", "model": "opencode-smoke"},
            "tags": [],
            "first_text": "opencode smoke memory: store local coding-agent context.",
        },
    },
]

MALFORMED_PAYLOAD_CASES: list[dict[str, Any]] = [
    {
        "name": "non-object-conversation-json",
        "arguments": {"conversation_json": "not an object"},
        "expected_message": "conversation_json",
    },
    {
        "name": "missing-messages",
        "arguments": {
            "conversation_json": {
                "source": "codex-cli",
                "metadata": {"imported_at": "2026-06-06T00:00:00Z"},
            }
        },
        "expected_message": "messages",
    },
    {
        "name": "messages-not-array",
        "arguments": {
            "conversation_json": {
                "source": "gemini",
                "messages": "bad messages",
                "metadata": {"imported_at": "2026-06-06T00:00:00Z"},
            }
        },
        "expected_message": "messages",
    },
    {
        "name": "invalid-role",
        "arguments": {
            "conversation_json": {
                "source": "vscode-copilot",
                "messages": [{"role": "tool", "text": "unsupported role"}],
                "metadata": {"imported_at": "2026-06-06T00:00:00Z"},
            }
        },
        "expected_message": "role",
    },
    {
        "name": "empty-messages",
        "arguments": {
            "conversation_json": {
                "source": "claude-code",
                "messages": [],
                "metadata": {"imported_at": "2026-06-06T00:00:00Z"},
            }
        },
        "expected_message": "messages",
    },
    {
        "name": "invalid-explicit-id",
        "arguments": {
            "conversation_json": {
                "id": "not-a-uuid",
                "source": "opencode",
                "messages": [{"role": "user", "text": "bad id"}],
                "metadata": {"imported_at": "2026-06-06T00:00:00Z"},
            }
        },
        "expected_message": "id must be a valid UUID",
    },
]


def _require_ollama() -> None:
    try:
        with urlopen(OLLAMA_BASE_URL, timeout=2) as response:
            if response.status >= 400:
                pytest.skip(f"Ollama returned HTTP {response.status}")
    except (OSError, URLError) as exc:
        pytest.skip(f"Ollama is not reachable at {OLLAMA_BASE_URL}: {exc}")


def _event_data_json(event_stream_body: str) -> dict[str, Any]:
    for line in event_stream_body.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError(f"No event-stream data payload found: {event_stream_body}")


def _event_result_json(event_stream_body: str) -> dict[str, Any]:
    for line in event_stream_body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line.removeprefix("data: "))
        if "result" in payload:
            return payload
    raise AssertionError(f"No event-stream result payload found: {event_stream_body}")


def _tool_payload(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    result = _event_result_json(response.text)["result"]
    content = result["content"]
    assert isinstance(content, list) and content
    text = content[0]["text"]
    assert isinstance(text, str)
    return json.loads(text)


def _mcp_client(tmp_path: Any) -> TestClient:
    app = create_app(
        config={
            "interfaces": {"mcp": True, "api": False},
            "openai": {"base_url": OPENAI_BASE_URL, "api_key": "ollama"},
            "paths": {"data_dir": str(tmp_path)},
            "providers": {
                "embeddings": "openai",
                "embedding_model": "nomic-embed-text",
                "embedding_dimension": 768,
                "metadata_db": "sqlite",
                "vector_db": "memory",
            },
        }
    )
    return TestClient(app)


def _initialize(client: TestClient, profile: dict[str, Any]) -> dict[str, str]:
    headers = {"Accept": "application/json, text/event-stream"}
    response = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {
                    "name": profile["name"],
                    "version": profile["version"],
                },
            },
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    session_id = response.headers.get("mcp-session-id")
    assert session_id
    return {**headers, "Mcp-Session-Id": session_id}


def _call_tool(
    client: TestClient,
    headers: dict[str, str],
    *,
    request_id: int,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        "/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers,
    )
    return _tool_payload(response)


def _fresh_payload(profile: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(profile["payload"])


def _assert_public_memory_shape(memory: dict[str, Any], profile: dict[str, Any]) -> None:
    expected = profile["expected"]
    assert memory["source"] == expected["source"]
    assert memory["messages"][0]["text"] == expected["first_text"]
    assert "hash" not in memory["messages"][0]

    metadata = memory["metadata"]
    assert "conversation_hash" not in metadata
    assert "message_hashes" not in metadata
    for key, value in expected["metadata"].items():
        assert metadata[key] == value
    if expected["tags"]:
        assert metadata["tags"] == expected["tags"]


@pytest.mark.parametrize(
    "profile",
    CLIENT_PROFILES,
    ids=[profile["name"] for profile in CLIENT_PROFILES],
)
def test_mcp_client_profile_smoke_with_ollama_embeddings(
    profile: dict[str, Any], tmp_path: Any
) -> None:
    _require_ollama()

    with _mcp_client(tmp_path / profile["name"]) as client:
        headers = _initialize(client, profile)

        validate = _call_tool(
            client,
            headers,
            request_id=2,
            name="memory_validate",
            arguments={"conversation_json": _fresh_payload(profile)},
        )
        assert validate["status"] == "ok"
        assert validate["valid"] is True
        assert validate["id"] is None
        assert validate["results"] == []
        assert validate["cursor"] is None
        assert validate["error_code"] is None
        assert validate["error_message"] is None

        insert = _call_tool(
            client,
            headers,
            request_id=3,
            name="memory_insert",
            arguments={"conversation_json": _fresh_payload(profile)},
        )
        assert insert["status"] == "ok"
        assert isinstance(insert["id"], str)
        assert insert["embedded_chunks"] == 2
        assert insert["error_code"] is None
        assert insert["error_message"] is None

        search = _call_tool(
            client,
            headers,
            request_id=4,
            name="memory_search",
            arguments={"query": profile["query"], "top_k": 5, "limit": 5},
        )
        assert search["status"] == "ok"
        assert search["results"]
        assert search["results"][0]["id"] == insert["id"]
        assert search["cursor"] is None
        assert search["error_code"] is None
        assert search["error_message"] is None
        _assert_public_memory_shape(search["results"][0]["conversation"], profile)

        retrieve = _call_tool(
            client,
            headers,
            request_id=5,
            name="memory_retrieve",
            arguments={"id": insert["id"]},
        )
        assert retrieve["status"] == "ok"
        assert retrieve["id"] == insert["id"]
        assert retrieve["results"] == []
        assert retrieve["cursor"] is None
        assert retrieve["error_code"] is None
        assert retrieve["error_message"] is None
        assert retrieve["memory"]["id"] == insert["id"]
        _assert_public_memory_shape(retrieve["memory"], profile)

        ask = _call_tool(
            client,
            headers,
            request_id=6,
            name="memory_ask",
            arguments={"question": profile["query"], "top_k": 5},
        )
        assert ask["status"] == "ok"
        assert ask["citations"]
        assert ask["citations"][0]["id"] == insert["id"]
        assert ask["error_code"] is None
        assert ask["error_message"] is None
        assert "hash" not in ask["results"][0]["conversation"]["messages"][0]


@pytest.mark.parametrize(
    "case",
    MALFORMED_PAYLOAD_CASES,
    ids=[case["name"] for case in MALFORMED_PAYLOAD_CASES],
)
def test_mcp_client_malformed_payloads_return_invalid_input_envelope(
    case: dict[str, Any], tmp_path: Any
) -> None:
    profile = {"name": f"negative-{case['name']}", "version": "smoke"}
    with _mcp_client(tmp_path / case["name"]) as client:
        headers = _initialize(client, profile)
        for request_id, tool_name in enumerate(("memory_validate", "memory_insert"), start=2):
            result = _call_tool(
                client,
                headers,
                request_id=request_id,
                name=tool_name,
                arguments=copy.deepcopy(case["arguments"]),
            )

            assert result["status"] == "error"
            assert result["error_code"] == "invalid_input"
            assert case["expected_message"] in result["error_message"]
            assert result["id"] is None
            assert result["results"] == []
            assert result["cursor"] is None
            if tool_name == "memory_validate":
                assert result["valid"] is False


def test_ollama_chat_completion_smoke() -> None:
    _require_ollama()
    openai = pytest.importorskip("openai")
    client = openai.OpenAI(base_url=OPENAI_BASE_URL, api_key="ollama")

    try:
        response = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly two words: memory smoke",
                }
            ],
            max_tokens=8,
            temperature=0,
        )
    except openai.NotFoundError as exc:
        pytest.skip(f"Ollama chat model is not available locally: {exc}")

    content = response.choices[0].message.content
    assert isinstance(content, str)
    assert content.strip()
