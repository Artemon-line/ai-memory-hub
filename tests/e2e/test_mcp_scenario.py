import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent


def _get_text_from_content(content: list[Any]) -> str:
    for item in content:
        if isinstance(item, TextContent):
            return item.text
    raise ValueError(f"No text content found in: {content}")


def _tool_result_is_error(result: Any) -> bool:
    is_error = getattr(result, "is_error", None)
    if is_error is not None:
        return bool(is_error)
    return bool(getattr(result, "isError", False))


@pytest.mark.asyncio
async def test_memory_scenario_e2e():
    """
    End-to-end test for the ai-memory-hub MCP server.
    This test follows the scenario:
    1. Insert "Hello from SQLite!"
    2. Insert "I love VRAM and GPUs"
    3. Insert "I enjoy cooking pasta"
    4. Ask "GPU" and verify results.

    Note: This test expects Ollama to be running with nomic-embed-text model
    as configured in tests/mcp_server_entry.py.
    """
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(Path(__file__).parent / "mcp_server_entry.py")],
    )

    async with AsyncExitStack() as stack:
        try:
            read, write = await stack.enter_async_context(stdio_client(server_params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception as e:
            if _is_connection_setup_error(e):
                pytest.skip("Ollama is not running. Skipping E2E test.")
            raise

        await _run_memory_scenario(session)


async def _run_memory_scenario(session: ClientSession) -> None:
    # 1. Insert memories
    memories = [
        "Hello from SQLite!",
        "I love VRAM and GPUs",
        "I enjoy cooking pasta",
    ]

    ids = []
    for text in memories:
        payload = {
            "conversation_json": {
                "messages": [{"role": "user", "content": text}]
            }
        }
        result = await session.call_tool("memory_insert", payload)

        # Verify tool execution succeeded
        assert not _tool_result_is_error(result), f"Tool call failed for '{text}': {result}"

        # Parse the JSON response from the tool
        # FastMCP tool results have a 'content' list
        assert len(result.content) > 0
        res_data = json.loads(_get_text_from_content(result.content))
        assert res_data["status"] == "ok", f"Insert failed for '{text}': {res_data}"
        ids.append(res_data["id"])

    # 2. Ask "GPU"
    # We use memory_ask which performs search + answering
    ask_payload = {"question": "GPU", "top_k": 3}
    ask_result = await session.call_tool("memory_ask", ask_payload)

    assert not _tool_result_is_error(ask_result), f"memory_ask failed: {ask_result}"

    assert len(ask_result.content) > 0
    ask_res = json.loads(_get_text_from_content(ask_result.content))

    assert ask_res["status"] == "ok"
    assert "answer" in ask_res
    assert ask_res["results"] == []
    assert ask_res["memory_result_count"] >= 1
    assert ask_res["citation_count"] >= 1
    assert "citations" not in ask_res
    assert "evidence" not in ask_res
    assert "structured_evidence" not in ask_res
    assert "provenance" not in ask_res

    # Detailed mode still exposes the full citation evidence for audit workflows.
    detailed_result = await session.call_tool(
        "memory_ask",
        {**ask_payload, "response_format": "detailed"},
    )
    assert not _tool_result_is_error(detailed_result), f"detailed memory_ask failed: {detailed_result}"
    assert len(detailed_result.content) > 0
    detailed_res = json.loads(_get_text_from_content(detailed_result.content))

    citation_texts = [c["text"] for c in detailed_res["citations"]]
    assert any("GPU" in t for t in citation_texts), (
        f"GPU not found in citations: {citation_texts}"
    )


def _is_connection_setup_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "ConnectionRefusedError" in message
        or "Connection reset by peer" in message
        or "Connection error" in message
    )


@pytest.mark.asyncio
async def test_memory_scenario_tool_connection_error_fails_after_setup():
    class FailedToolResult:
        is_error = True
        content: list[Any] = []

        def __repr__(self) -> str:
            return "Connection error from initialized MCP tool"

    class InitializedSession:
        async def call_tool(self, name: str, payload: dict[str, Any]) -> FailedToolResult:
            _ = name, payload
            return FailedToolResult()

    with pytest.raises(AssertionError, match="Connection error from initialized MCP tool"):
        await _run_memory_scenario(InitializedSession())  # type: ignore[arg-type]
