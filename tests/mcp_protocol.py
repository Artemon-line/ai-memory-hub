from __future__ import annotations

from fastapi.testclient import TestClient


def initialize_mcp_session(client: TestClient) -> dict[str, str]:
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
                "clientInfo": {"name": "pytest", "version": "0.1"},
            },
        },
        headers=headers,
    )
    assert response.status_code == 200
    session_id = response.headers.get("mcp-session-id")
    assert session_id
    session_headers = {**headers, "Mcp-Session-Id": session_id}
    initialized = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=session_headers,
    )
    assert initialized.status_code in {200, 202, 204}
    return session_headers
