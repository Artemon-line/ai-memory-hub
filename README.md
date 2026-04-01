# ai-memory-hub

ai-memory-hub is a local-first memory engine that lets you store, search, and reuse AI conversation history across any LLM platform. It provides interfaces, a unified JSON schema, and a config-driven provider system so you can bring your own models, databases, and tooling.

## Features

- LLM-first ingestion with a unified JSON schema
- Two ingestion modes: MCP and manual
- Local-first defaults: SQLite (metadata) and LanceDB (vectors)
- Bring Your Own Stack: choose inference, embeddings, vector DB, storage, and agent framework
- Schema validation, embedding, and indexing pipeline
- Search and retrieval interfaces via MCP and API

## Bring Your Own Stack (BYOS)

ai-memory-hub is intentionally minimal. It does not prescribe your LLM, vector DB, embeddings, or agent framework. The hub provides interfaces and a consistent schema. All providers are configured via a simple config file.

You provide:

- Inference engine (OpenAI, Llama Stack, Ollama, LM Studio, etc.)
- Vector DB (LanceDB, Chroma, Milvus, pgvector, etc.)
- Embeddings and guardrails
- Storage backends
- Agent framework

## Local-First

- SQLite for metadata by default
- LanceDB for vectors by default
- No cloud sync unless you configure it
- You own all data

## Ingestion Model (LLM-First)

All platforms export into the same JSON schema. No browser extensions, DOM scraping, or HTML parsing.

Two ingestion modes:

1. MCP ingestion: User says "Save last N messages", the agent fetches context, formats JSON, and calls `memory.insert`.
2. Manual ingestion: User copies text, an LLM formats JSON, and the user calls `memory.insert`.

## Unified JSON Schema (Core)

All ingestion flows target a single schema. The hub validates it, embeds it, stores it, and indexes it.

```json
{
  "id": "uuid",
  "source": "chatgpt",
  "timestamp": "YYYY-MM-DDTHH:MM:SSZ",
  "messages": [
    { "role": "user", "text": "..." },
    { "role": "assistant", "text": "..." }
  ],
  "metadata": {
    "tags": [],
    "topics": [],
    "imported_at": "YYYY-MM-DDTHH:MM:SSZ"
  }
}
```

## Quick Start

1. Configure providers in your config file (example: `config.yaml`).
2. Ask your LLM to format a conversation in the unified schema.
3. Insert via MCP: `memory.insert`.
4. Search via MCP: `memory.search`.

## Example Config

```yaml
storage:
  metadata: sqlite
  vectors: lancedb

providers:
  inference: openai
  embeddings: openai
  vector_db: lancedb

interfaces:
  mcp: true
  api: true
```

## High-Level Architecture

```text
Raw Messages
  -> LLM Formats JSON
  -> Schema Validation
  -> memory.insert (MCP or API)
  -> Chunk + Embed
  -> Vector Store
  -> Search / Retrieve / RAG
```

## Documentation

- Architecture: `docs/architecture.md`
- Agents: `docs/agents.md`

## License

MIT License. See `LICENSE`.

