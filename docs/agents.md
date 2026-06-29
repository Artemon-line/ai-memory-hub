# Agent Integration

ai-memory-hub gives agents a local-first memory backend over MCP and HTTP. Agents send
conversation payloads to the hub, and the hub owns normalization, validation, ID generation,
hashing, deduplication, embedding, storage, retrieval, and ask-over-memory behavior.

Related docs:

- Architecture: `architecture.md`
- MCP contract details: `mcp_plan.md`
- MCP utility compliance: `mcp_utility_compliance_plan.md`
- MCP client smoke testing: `mcp_client_smoke_plan.md`
- Deterministic ingestion details: `deterministic_ingestion_plan.md`
- Storage/provider details: `storage_agnostic_byoa_plan.md`
- CLI plan: `cli_implementation_plan.md`
- Project overview: `../README.md`

## Current Status

Implemented:

- MCP tools: `memory_validate`, `memory_insert`, `memory_search`, `memory_retrieve`, `memory_ask`.
- MCP resources: `memory://conversation/example`, `memory://conversation/{id}`,
  `memory://search/{query}`, `memory://timeline/{day}`, `memory://health`.
- MCP prompts: `save_conversation`, `search_memory`, `ask_memory`, `summarize_conversation`.
- HTTP endpoints: `POST /memory/insert`, `/memory/search`, `/memory/retrieve`, `/memory/ask`.
- Omitted-ID insertion: agents should omit `id` by default and use the returned canonical ID.
- Deterministic normalization, schema validation, message hashes, conversation hashes, duplicate
  detection, and trusted same-thread append support.
- Message chunking by default, with optional token-window chunking.
- Token-budgeted `memory_ask` through config or request `max_context_tokens`.
- Storage providers: SQLite/Postgres/MongoDB metadata,
  LanceDB/ChromaDB/Qdrant/Milvus/Weaviate/PGVector/MongoDB Atlas/
  Elasticsearch/OpenSearch/Redis/Vespa/Typesense/Pinecone/Turbopuffer/in-memory vectors.
- Storage safety: provider capabilities, schema-version checks, vector dimensionality checks,
  policy-gated fallback, degraded health, dry-run wrappers, and secret-safe fallback logging.
- MCP smoke profiles for Codex, Gemini, VS Code Copilot, and opencode.
- CLI agent workflows: `tokenizer-check`, `ingest`, `search`, `retrieve`,
  `ask`, and `serve`.

Planned or partial:

- Claude MCP smoke profile and negative client payload cases are planned.
- Platform-specific importers, summaries, timeline intelligence, graph memory, shared memory, plugins, and cloud sync are planned.

## Agent Rules

- Prefer MCP tools when running inside an MCP-capable client.
- Use HTTP endpoints only when MCP is unavailable.
- Do not write directly to SQLite, Postgres, LanceDB, ChromaDB, PGVector,
  Redis, Vespa, or data files.
- Omit `id` unless the user or upstream system requires a specific UUID.
- Treat the returned `id` as canonical for retrieve/search citations.
- Call `memory_validate` before `memory_insert` when using MCP.
- Include full user and assistant turns that should be remembered. You may add a short factual `metadata.summary` retrieval hint, but do not use it instead of `messages`.
- Do not call `memory_insert` for ordinary user statements unless the user asked to save them, confirmed a save, or explicitly enabled client auto-save. When saving intentionally, include `metadata.save_intent` with `explicit_user_request`, `user_confirmed`, or `client_auto_save`. Servers configured with `memory.insert_policy: require_save_intent` reject inserts without this marker.
- Do not include tool output, debug logs, or operational planning text as conversation messages unless the user explicitly wants that stored.
- Do not supply client-side embeddings, message hashes, or conversation hashes. The hub computes them.
- Do not assume memory is English-only. Multilingual retrieval is available when
  the hub's configured embedding model supports the relevant languages.
- Do not change the hub embedding model for an existing persistent vector index
  without reindexing or using a separate vector namespace/index.
- Never expose raw stored secrets in messages or metadata.

After implementing any new agent-facing feature, update `README.md` and this document in the same change.

## MCP Tool Surface

MCP tool responses use a stable envelope:

- `status`
- `id`
- `results`
- `cursor`
- `error_code`
- `error_message`

Tool-specific fields can also appear, such as `answer`, `citations`, `memory`, `valid`,
`deduplicated`, `appended_messages`, `embedded_chunks`, and token-budget diagnostics.

### `memory_validate`

Use this before insert to check payload shape.

```json
{
  "conversation_json": {
    "source": "codex",
    "timestamp": "2026-06-08T12:00:00Z",
    "messages": [
      {"role": "user", "text": "Remember that I prefer local-first tools."},
      {"role": "assistant", "text": "Stored."}
    ],
    "metadata": {
      "imported_at": "2026-06-08T12:00:00Z",
      "summary": "User said they prefer local-first tools.",
      "tags": ["preferences"]
    }
  }
}
```

Expected success:

```json
{
  "status": "ok",
  "valid": true,
  "results": []
}
```

### `memory_insert`

Insert the same payload after validation. Omit `id` by default; the hub assigns a UUID.

```json
{
  "conversation_json": {
    "source": "codex",
    "timestamp": "2026-06-08T12:00:00Z",
    "messages": [
      {"role": "user", "text": "Remember that I prefer local-first tools."},
      {"role": "assistant", "text": "Stored."}
    ],
    "metadata": {
      "imported_at": "2026-06-08T12:00:00Z",
      "summary": "User said they prefer local-first tools.",
      "tags": ["preferences"]
    }
  }
}
```

Expected success includes:

- `status: "ok"`
- `id`: canonical stored memory ID
- `deduplicated`
- `appended_messages`
- `embedded_chunks`
- `chunks`

### `memory_search`

Search stored memory by semantic query.

```json
{
  "query": "local-first tools",
  "top_k": 5,
  "limit": 5,
  "cursor": "0",
  "source": "codex",
  "date_from": "2026-01-01T00:00:00Z",
  "date_to": "2026-12-31T23:59:59Z",
  "tags": ["preferences"],
  "thread_id": "codex:session-42"
}
```

Notes:

- `top_k` controls retrieval breadth.
- `limit` and `cursor` support paginated MCP output.
- `source`, `date_from`, `date_to`, `tags`, and `thread_id` are optional filters.
- Do not pass `null` for optional fields; omit them instead.

### `memory_retrieve`

Retrieve one stored conversation by canonical ID.

```json
{
  "id": "11111111-2222-4333-8444-555555555555"
}
```

The returned `memory` object redacts internal content hashes from external responses.

### `memory_ask`

Ask a question over retrieved memory.

```json
{
  "question": "What tool preference did the user mention?",
  "top_k": 5,
  "max_context_tokens": 1200
}
```

Expected success includes:

- `answer`
- `results`
- `citations`
- optional `context_tokens_used`
- optional `chunks_selected`
- optional `chunks_dropped`
- optional `tokenizer_used`

## MCP Resources And Prompts

Resources are useful for clients that can browse MCP state:

- `memory://conversation/example`
- `memory://conversation/{id}`
- `memory://search/{query}`
- `memory://timeline/{day}`
- `memory://health`

Prompts provide client guidance:

- `save_conversation`: validate, insert, retrieve, and confirm a conversation.
- `search_memory`: call `memory_search` with stable defaults.
- `ask_memory`: call `memory_ask` with a valid integer `top_k`.
- `summarize_conversation`: retrieve then summarize a stored conversation.

## HTTP Agent Surface

Use HTTP when MCP is not available:

- `POST /memory/insert`
- `POST /memory/search`
- `POST /memory/retrieve`
- `POST /memory/ask`

The HTTP API exposes the same core workflows as MCP, but MCP has richer prompt/resource
discoverability and tool envelopes.

## Recommended Agent Workflows

Save current conversation:

```text
collect relevant user/assistant turns
-> build conversation_json
-> memory_validate
-> fix payload if needed
-> memory_insert
-> memory_retrieve with returned id
-> confirm saved id to user
```

Search memory:

```text
user asks for remembered context
-> memory_search(query, top_k=5, limit=5)
-> inspect results
-> answer with cited memory ids when useful
```

Ask over memory:

```text
user asks a question over prior memory
-> memory_ask(question, top_k=5)
-> return answer and citations
```

## Client Payload Notes

The MCP layer tolerates common client-shaped payloads:

- `conversation` arrays can be normalized into `messages`.
- message `content` aliases are normalized to `text`.
- omitted `id` is filled with a generated UUID.
- supported roles normalize to `user` and `assistant`.

Invalid explicit IDs still fail fast. If an agent supplies `id`, it must be a valid UUID.
`metadata.summary` must be a string of 2000 characters or fewer. It improves
search recall and metadata reranking, but answers still need support from raw
messages or normalized facts. The hub also generates deterministic
conversation, topic, and project summaries from stored message text. Search and
retrieve responses expose the conversation summary as
`metadata.generated_summary`; treat it as metadata, not as citation evidence by
itself. The hub may also return server-owned `metadata.auto_tags` and
`metadata.tag_sources`; clients should keep user/manual tags in `metadata.tags`
and let the server refresh auto-tags during insert or trusted append.
`metadata.save_intent` is optional under the default `permissive` policy and
required under `memory.insert_policy: require_save_intent`.
For thread continuity, clients may send `metadata.upstream_thread_id` or
`metadata.thread_id`. The hub preserves upstream IDs, derives `thread_id` from
`source` plus `upstream_thread_id` when needed, and supports `thread_id` filters
on `memory_search` and `memory_ask`. Use `result_mode="threads"` when a client
wants grouped thread-level search results.

Use `memory_profile_get` when you need a compact profile view. It returns
filtered normalized facts plus a `summary` object generated from active facts,
freshness, source-quality counts, and compact fact provenance; the generated
summary is stored separately from raw messages, chunks, and normalized facts.

## Storage Awareness For Agents

Agents should not branch behavior based on the active storage backend. The configured providers
are selected at startup:

- Metadata: `sqlite`, `postgres`, or `mongodb`
- Vectors: `lancedb`, `chromadb`, `qdrant`, `milvus`, `weaviate`,
  `pgvector`, `mongodb_atlas`, `elasticsearch`, `opensearch`, `redis`,
  `typesense`, `pinecone`, `turbopuffer`, or `memory`
- Embeddings: `openai` or `local`

For multilingual conversations, retrieval quality is a property of the
configured embedding model. The agent should send normal Unicode text through
MCP/API and let the hub embed it. If the operator changes the embedding model,
provider, dimension, or model options, existing persistent vectors must be
reindexed or isolated in a separate namespace/index before mixed retrieval is
trusted.

Agents can inspect `memory://health` or API health behavior when available, but storage provider
details should only inform diagnostics, not payload shape.

Dry-run mode may skip writes while preserving response shape. Degraded mode can indicate vector
fallback to in-memory storage when explicitly allowed by config.

## Future Agent Features

- Real-client smoke tests for more agent CLIs.
- Platform-specific importers.
- Memory consolidation and long-term distillation.
- Topic clustering and timeline extraction.
- Graph memory.
- Multi-agent shared memory.
- Plugin-defined ingestion and storage providers.
