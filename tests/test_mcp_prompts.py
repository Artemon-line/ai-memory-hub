from __future__ import annotations

import json

from fastapi.testclient import TestClient

from memory.api.server import create_app


def _event_data_json(event_stream_body: str) -> dict[str, object]:
    for line in event_stream_body.splitlines():
        if line.startswith("data: "):
            return json.loads(line.removeprefix("data: "))
    raise AssertionError("No event-stream data payload found")


def _mcp_client() -> TestClient:
    app = create_app(config={"interfaces": {"mcp": True, "api": False}})
    return TestClient(app)


def _init_headers(client: TestClient) -> dict[str, str]:
    headers = {"Accept": "application/json, text/event-stream"}
    initialize_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.1"},
        },
    }
    response = client.post("/mcp/", json=initialize_payload, headers=headers)
    assert response.status_code == 200
    session_id = response.headers.get("mcp-session-id")
    assert session_id
    return {**headers, "Mcp-Session-Id": session_id}


def test_mcp_prompts_list_and_get() -> None:
    with _mcp_client() as client:
        headers = _init_headers(client)
        list_response = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "prompts/list", "params": {}},
            headers=headers,
        )
        assert list_response.status_code == 200
        list_result = _event_data_json(list_response.text)["result"]
        assert isinstance(list_result, dict)
        prompts = list_result.get("prompts")
        assert isinstance(prompts, list)
        prompt_names = {item.get("name") for item in prompts if isinstance(item, dict)}
        assert {"save_conversation", "search_memory", "ask_memory", "summarize_conversation"}.issubset(prompt_names)

        get_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "prompts/get",
                "params": {"name": "search_memory", "arguments": {"query": "hello"}},
            },
            headers=headers,
        )
        assert get_response.status_code == 200
        get_result = _event_data_json(get_response.text)["result"]
        assert isinstance(get_result, dict)
        messages = get_result.get("messages")
        assert isinstance(messages, list)
        assert messages
        text_content = messages[0]["content"]["text"]
        assert "memory_search" in text_content
        assert "query=\"hello\"" in text_content

        ask_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "prompts/get",
                "params": {"name": "ask_memory", "arguments": {"question": "what changed?", "top_k": "3"}},
            },
            headers=headers,
        )
        assert ask_response.status_code == 200
        ask_result = _event_data_json(ask_response.text)["result"]
        assert isinstance(ask_result, dict)
        ask_messages = ask_result.get("messages")
        assert isinstance(ask_messages, list)
        assert ask_messages
        ask_text = ask_messages[0]["content"]["text"]
        assert "memory_ask" in ask_text
        assert "question=\"what changed?\"" in ask_text
