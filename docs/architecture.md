# Architecture

ai-memory-hub is a local-first memory engine built around a unified JSON schema and a config-driven provider system.
It exposes interfaces for ingestion and retrieval, while letting you bring your own embedding modes, storage, and agent framework.

Related docs:
 - Project overview: `../README.md`
 - Agent integration: `agents.md`

## High-Level System Diagram

```text
Source Messages
  -> LLM Formats JSON
  -> Schema Validation
  -> memory_insert (MCP or API)
  -> Chunk + Embed
  -> Vector Store
  -> Search / Retrieve / RAG
```

## Ingestion Pipeline

The ingestion path is LLM-first and schema-first. The hub does not scrape HTML or parse DOM exports.

Steps:

1. Fetch messages (MCP or manual input)
2. Format into the unified JSON schema (LLM)
3. Validate schema (Python)
4. Insert via `memory_insert`
5. Embed chunks
6. Store vectors
7. Confirm success

## Normalization Layer

The normalization layer enforces a single JSON schema across platforms. This ensures consistent storage, search, and retrieval regardless of source.

Responsibilities:

- Validate schema
- Normalize roles and timestamps
- Chunk messages for embeddings
- Attach metadata

## Storage Layer

Local-first by default. No cloud sync unless configured.

Defaults:

- Metadata store: SQLite
- Vector store: LanceDB


Core MCP tools:

- `memory_insert`
- `memory_search`
- `memory_retrieve`

## Bring Your Own Stack (BYOS) Model

Bring Your Own Stack (BYOS) means you supply embedding models, and storage via config.

You can replace any provider through config.

```text
[Ingestion]
  LLM (yours) -> JSON Schema
       |
       v
[Hub Interfaces]
  MCP / API
       |
       v
[Storage]
  Metadata DB (yours)
  Vector DB (yours)
       |
       v
[Client]
  Search (yours)
```

## Config-Driven Provider System

All providers are defined in a single config file. The hub reads this at startup and wires the interfaces to your choices.

Example:

```yaml
storage:
  metadata: sqlite
  vectors: lancedb

providers:
  embeddings: openai
  vector_db: lancedb

interfaces:
  mcp: true
  api: true
```




