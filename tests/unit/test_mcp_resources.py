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


def test_mcp_resources_list_and_read_conversation_resource() -> None:
    with _mcp_client() as client:
        headers = _init_headers(client)
        list_response = client.post(
            "/mcp/",
            json={"jsonrpc": "2.0", "id": 2, "method": "resources/list", "params": {}},
            headers=headers,
        )
        assert list_response.status_code == 200
        list_result = _event_data_json(list_response.text)["result"]
        assert isinstance(list_result, dict)
        resources = list_result.get("resources")
        assert isinstance(resources, list)
        uris = {resource.get("uri") for resource in resources if isinstance(resource, dict)}
        assert "memory://conversation/example" in uris

        read_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "resources/read",
                "params": {"uri": "memory://conversation/example"},
            },
            headers=headers,
        )
        assert read_response.status_code == 200
        read_result = _event_data_json(read_response.text)["result"]
        assert isinstance(read_result, dict)
        contents = read_result.get("contents")
        assert isinstance(contents, list)
        assert contents
        payload = json.loads(contents[0]["text"])
        assert payload["status"] == "ok"
        assert payload["metadata"]["id"] == "example"
        assert payload["metadata"]["source"] == "example"
        assert "timestamp" in payload["metadata"]
        assert "tags" in payload["metadata"]
        assert "updated_at" in payload["metadata"]


def test_mcp_resource_templates_list_non_empty() -> None:
    with _mcp_client() as client:
        headers = _init_headers(client)
        templates_response = client.post(
            "/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "resources/templates/list",
                "params": {},
            },
            headers=headers,
        )
        assert templates_response.status_code == 200
        result = _event_data_json(templates_response.text)["result"]
        assert isinstance(result, dict)
        templates = result.get("resourceTemplates")
        assert isinstance(templates, list)
        template_uris = {
            template.get("uriTemplate") for template in templates if isinstance(template, dict)
        }
        assert "memory://conversation/{id}" in template_uris
        assert "memory://search/{query}" in template_uris
        assert "memory://timeline/{day}" in template_uris
