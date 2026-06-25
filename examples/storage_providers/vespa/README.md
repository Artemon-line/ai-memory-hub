# Vespa Vector Provider Example

This example points ai-memory-hub at an already deployed Vespa application.
Vespa application package deployment is intentionally managed outside
ai-memory-hub because schema, rank profile, and cluster sizing choices are
operational decisions.

The Vespa schema must expose the fields ai-memory-hub feeds:

- `id`
- `memory_id`
- `project_id`
- `owner_id`
- `chunk_id`
- `chunk_index`
- `message_hash`
- `role`
- `text`
- `vector`

The `vector` field must use the same dimensionality as
`providers.embedding_dimension`, and the configured rank profile must accept
`input.query(q)` for nearest-neighbor search.

Run ai-memory-hub with:

```bash
uv sync --extra vespa
uv run python -m memory.cli serve --config examples/storage_providers/vespa/config.yaml
```

Set `storage.vector_providers.vespa.token` when using Vespa Cloud data-plane
token authentication. For local unauthenticated Vespa, leave it empty.
