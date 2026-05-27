import pytest
import json
import sys
from pathlib import Path
from typing import Any
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

def _get_text_from_content(content: list[Any]) -> str:
    for item in content:
        if isinstance(item, TextContent):
            return item.text
    raise ValueError(f"No text content found in: {content}")

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
    
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                # 1. Insert memories
                memories = [
                    "Hello from SQLite!",
                    "I love VRAM and GPUs",
                    "I enjoy cooking pasta"
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
                    assert not result.isError, f"Tool call failed for '{text}': {result}"
                    
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
                
                assert not ask_result.isError, f"memory_ask failed: {ask_result}"
                
                assert len(ask_result.content) > 0
                ask_res = json.loads(_get_text_from_content(ask_result.content))
                
                assert ask_res["status"] == "ok"
                assert "answer" in ask_res
                assert "citations" in ask_res
                
                # Verify that the answer mentions GPUs or at least includes citations
                # The exact answer depends on the prompt, but it should contain our GPU memory
                citation_texts = [c["text"] for c in ask_res["citations"]]
                assert any("GPU" in t for t in citation_texts), f"GPU not found in citations: {citation_texts}"
                
                # 3. Add a Raw Text Insertion
                raw_text = "User: I am currently learning how to build MCP servers.\nAssistant: That is a great skill to have."
                raw_result = await session.call_tool("memory_insert_raw", {"text": raw_text})
                
                assert not raw_result.isError, f"Raw insert failed: {raw_result}"
                raw_res = json.loads(_get_text_from_content(raw_result.content))
                assert raw_res["status"] == "ok"    
                
                # 4. Search for the raw-inserted memory
                search_result = await session.call_tool("memory_search", {"query": "MCP servers"})
                assert not search_result.isError
                search_res = json.loads(_get_text_from_content(search_result.content))
                assert any("MCP servers" in r["text"] for r in search_res["results"])

    except Exception as e:
        if "ConnectionRefusedError" in str(e) or "Connection reset by peer" in str(e):
            pytest.skip("Ollama is not running. Skipping E2E test.")
        raise e
