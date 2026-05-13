# Agent Integration

ai-memory-hub exposes a small MCP and API surface so agents can write and read memory without owning storage or ingestion logic.

Related docs:
- Architecture: `architecture.md`
- Project overview: `../README.md`

## How Agents Interact With The Hub

Agents act as ingestion and retrieval clients.

- Ingest: format JSON -> call `memory_insert`
- Search: call `memory_search` with a query
- Retrieve: call `memory_retrieve` by ID

## MCP Tools

Tool names and schemas are implementation-specific, but the intended core surface is:

### `memory_insert`

```json
{
  "conversation": {
    "id": "uuid",
    "source": "copilot",
    "timestamp": "YYYY-MM-DDTHH:MM:SSZ",
    "messages": [
      { "role": "user", "text": "..." },
      { "role": "assistant", "text": "..." }
    ],
    "metadata": { "tags": [], "topics": [], "imported_at": "YYYY-MM-DDTHH:MM:SSZ" }
  }
}
```

Return should include a status and the stored conversation id.

### `memory_search`

```json
{
  "query": "gpu upgrade",
  "top_k": 5
}
```

### `memory_retrieve`

```json
{
  "id": "uuid"
}
```

## LangGraph Ingestion Loop (Agentic)

This loop is LangGraph-friendly and works for both MCP and manual ingestion.

```text
fetch -> format_json -> validate -> insert -> embed -> store -> confirm
```

Step details:

1. Fetch messages (MCP context or manual input)
2. Format into JSON schema (LLM)
3. Validate schema (Python)
4. Insert via `memory_insert`
5. Embed chunks
6. Store vectors
7. Confirm success

## MCP Automatic Ingestion ("Save last N messages")

Example intent:

- User: "Save last 20 messages"
- Agent fetches last 20 turns
- Agent formats JSON
- Agent calls `memory_insert`
- Hub stores and confirms

## Manual Ingestion Fallback

When MCP context is unavailable:

1. User copies raw text
2. LLM formats JSON schema
3. User or agent calls `memory_insert`

## Example Agent Workflow

```text
User -> Agent
  "Save last 10 messages"
Agent -> Fetch context
Agent -> Format JSON
Agent -> memory_insert
Hub -> Validate, embed, store
Agent -> "Memory saved"
```

## Future Agent Features

- Memory consolidation
- Topic clustering
- Timeline extraction
- Long-term memory distillation
- Multi-agent shared memory


