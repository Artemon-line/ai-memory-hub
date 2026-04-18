# ai-memory-hub

ai-memory-hub is a local-first memory engine to store, search, and reuse AI conversation history across LLM platforms. It is schema-first and provider-driven.

## Features

- Deterministic MVP ingestion pipeline (no LangGraph)
- Unified conversation JSON schema (`memory/schema/conversation.schema.json`)
- LLM formatting with validation retry (up to 3 attempts)
- Schema validation before storage
- Chunking + embedding + vector storage pipeline
- Local-first defaults: SQLite (metadata) + LanceDB (vectors)
- MCP and API interface support

## MVP Ingestion Pipeline (Phase 1)

The ingestion pipeline is linear and deterministic:

1. Accept raw messages (MCP or API)
2. Preprocess messages (optional hook)
3. Format with LLM into unified schema
4. Validate JSON against `conversation.schema.json`
5. Retry format + validate up to 3 attempts if invalid
6. Attach metadata (`source`, timestamps, optional title/metadata)
7. Chunk messages
8. Embed chunks
9. Store conversation + vectors
10. Postprocess result (optional hook)

Implementation files:

- `memory/ingestion/mvp_ingestion.py`
- `memory/ingestion/mvp_ingestion_agent.py`
- `memory/ingestion/provider_loader.py`

## Configuration

Use the MVP ingestion agent in config:

```yaml
storage:
  metadata: sqlite
  vectors: lancedb

providers:
  inference: openai
  embeddings: openai
  vector_db: lancedb
  agent: mvp

interfaces:
  mcp: true
  api: true
```

See `example.config.yaml` for the full example.

## Ollama Configuration

To run with Ollama instead of OpenAI, update your config:

```yaml
providers:
  inference: ollama
  embeddings: ollama
  vector_db: lancedb
  agent: mvp

ollama:
  host: "http://host.docker.internal:11434"  # Docker
  chat_model: "llama3.1:8b"
  embedding_model: "nomic-embed-text"
```

If you run outside Docker, use:

```yaml
ollama:
  host: "http://localhost:11434"
```

Model setup:

```bash
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```

You can also mix providers, for example:

```yaml
providers:
  inference: ollama
  embeddings: openai
```

## Unified JSON Schema (Core)

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
    "imported_at": "YYYY-MM-DDTHH:MM:SSZ",
    "tags": [],
    "topics": []
  }
}
```

## Tests

MVP tests are in:

- `tests/test_mvp_ingestion.py`
- `tests/test_mvp_ingestion_agent.py`

These cover:

- valid JSON path
- invalid JSON retry -> success
- invalid JSON retry -> fail after 3 attempts
- embedding + storage calls
- deterministic behavior

## Documentation

- Architecture: `docs/architecture.md`
- Agents: `docs/agents.md`

## License

MIT License. See `LICENSE`.
