# In-Memory Vectors

This setup keeps vectors in process memory. Metadata still uses SQLite, but
vector rows disappear when the server stops.

Run from the repository root:

```bash
uv sync --dev
uv run aim serve --config examples/storage_providers/memory/config.yaml --host 127.0.0.1 --port 8000
```

Use this for short-lived local tests, not persistent recall.
