# SQLite + LanceDB

This is the default local persistent setup: SQLite stores metadata and LanceDB
stores vectors.

Run from the repository root:

```bash
uv sync --dev
uv run aim serve --config examples/storage-providers/local-lancedb/config.yaml --host 127.0.0.1 --port 8000
```

In another terminal, use the common smoke commands from
`examples/storage-providers/README.md`.

