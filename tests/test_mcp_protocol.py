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


def test_mcp_initialize_and_tools_list_with_session() -> None:
    with _mcp_client() as client:
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
        initialize_response = client.post("/mcp/", json=initialize_payload, headers=headers)
        assert initialize_response.status_code == 200
        session_id = initialize_response.headers.get("mcp-session-id")
        assert session_id

        tools_list_payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        tools_list_response = client.post(
            "/mcp/",
            json=tools_list_payload,
            headers={**headers, "Mcp-Session-Id": session_id},
        )
        assert tools_list_response.status_code == 200
        result = _event_data_json(tools_list_response.text)["result"]
        assert isinstance(result, dict)
        tools = result.get("tools")
        assert isinstance(tools, list)
        tool_names = {tool["name"] for tool in tools if isinstance(tool, dict) and "name" in tool}
        assert {"memory_insert", "memory_search", "memory_retrieve"}.issubset(tool_names)


def test_mcp_tools_list_requires_session_id() -> None:
    with _mcp_client() as client:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        response = client.post(
            "/mcp/",
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["jsonrpc"] == "2.0"
        assert body["error"]["code"] == -32600
        assert "Missing session ID" in body["error"]["message"]
