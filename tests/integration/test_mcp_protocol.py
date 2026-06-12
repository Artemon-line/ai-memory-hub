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


def _mcp_client_with_page_size(page_size: int) -> TestClient:
    app = create_app(
        config={
            "interfaces": {"mcp": True, "api": False},
            "mcp": {"list_page_size": page_size},
        }
    )
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
    initialize_response = client.post("/mcp/", json=initialize_payload, headers=headers)
    assert initialize_response.status_code == 200
    session_id = initialize_response.headers.get("mcp-session-id")
    assert session_id
    return {**headers, "Mcp-Session-Id": session_id}


def test_mcp_initialize_and_tools_list_with_session() -> None:
    with _mcp_client() as client:
        headers = _init_headers(client)

        tools_list_payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        tools_list_response = client.post(
            "/mcp/",
            json=tools_list_payload,
            headers=headers,
        )
        assert tools_list_response.status_code == 200
        result = _event_data_json(tools_list_response.text)["result"]
        assert isinstance(result, dict)
        tools = result.get("tools")
        assert isinstance(tools, list)
        tool_names = {tool["name"] for tool in tools if isinstance(tool, dict) and "name" in tool}
        assert {"memory_validate", "memory_insert", "memory_search", "memory_retrieve"}.issubset(tool_names)
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict):
                continue
            properties = schema.get("properties", {})
            assert isinstance(properties, dict)
            assert "ctx" not in properties


def test_mcp_tools_list_cursor_pagination_is_stable() -> None:
    with _mcp_client_with_page_size(3) as client:
        headers = _init_headers(client)

        first_response = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
        )
        assert first_response.status_code == 200
        first_result = _event_data_json(first_response.text)["result"]
        assert isinstance(first_result, dict)
        first_tools = first_result.get("tools")
        assert isinstance(first_tools, list)
        assert len(first_tools) == 3
        next_cursor = first_result.get("nextCursor")
        assert isinstance(next_cursor, str)

        second_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/list",
                "params": {"cursor": next_cursor},
            },
            headers=headers,
        )
        assert second_response.status_code == 200
        second_result = _event_data_json(second_response.text)["result"]
        assert isinstance(second_result, dict)
        second_tools = second_result.get("tools")
        assert isinstance(second_tools, list)
        assert second_tools

        first_names = [tool["name"] for tool in first_tools if isinstance(tool, dict)]
        second_names = [tool["name"] for tool in second_tools if isinstance(tool, dict)]
        assert not set(first_names).intersection(second_names)


def test_mcp_list_invalid_cursor_returns_protocol_error() -> None:
    with _mcp_client_with_page_size(3) as client:
        headers = _init_headers(client)
        response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {"cursor": "not-a-valid-cursor"},
            },
            headers=headers,
        )
        assert response.status_code == 200
        payload = _event_data_json(response.text)
        assert payload["jsonrpc"] == "2.0"
        assert payload["id"] == 2
        error = payload["error"]
        assert error["code"] == -32602


def test_mcp_resource_templates_list_cursor_pagination() -> None:
    with _mcp_client_with_page_size(2) as client:
        headers = _init_headers(client)
        first_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "resources/templates/list",
                "params": {},
            },
            headers=headers,
        )
        assert first_response.status_code == 200
        first_result = _event_data_json(first_response.text)["result"]
        assert isinstance(first_result, dict)
        templates = first_result.get("resourceTemplates")
        assert isinstance(templates, list)
        assert len(templates) == 2
        assert isinstance(first_result.get("nextCursor"), str)


def test_mcp_prompts_and_resources_list_cursor_pagination() -> None:
    with _mcp_client_with_page_size(1) as client:
        headers = _init_headers(client)
        for method, result_key in (
            ("prompts/list", "prompts"),
            ("resources/list", "resources"),
        ):
            first_response = client.post(
                "/mcp/",
                json={"jsonrpc": "2.0", "id": 2, "method": method, "params": {}},
                headers=headers,
            )
            assert first_response.status_code == 200
            first_result = _event_data_json(first_response.text)["result"]
            assert isinstance(first_result, dict)
            first_items = first_result.get(result_key)
            assert isinstance(first_items, list)
            assert len(first_items) == 1
            next_cursor = first_result.get("nextCursor")
            assert isinstance(next_cursor, str)

            second_response = client.post(
                "/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": method,
                    "params": {"cursor": next_cursor},
                },
                headers=headers,
            )
            assert second_response.status_code == 200
            second_result = _event_data_json(second_response.text)["result"]
            assert isinstance(second_result, dict)
            assert isinstance(second_result.get(result_key), list)


def test_mcp_logging_set_level_validation() -> None:
    with _mcp_client() as client:
        headers = _init_headers(client)
        valid_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "logging/setLevel",
                "params": {"level": "info"},
            },
            headers=headers,
        )
        assert valid_response.status_code == 200
        assert "error" not in _event_data_json(valid_response.text)

        invalid_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "logging/setLevel",
                "params": {"level": "definitely-invalid"},
            },
            headers=headers,
        )
        assert invalid_response.status_code == 200
        payload = _event_data_json(invalid_response.text)
        assert payload["error"]["code"] == -32602


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
