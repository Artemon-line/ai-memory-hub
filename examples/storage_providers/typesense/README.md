# Typesense

This example uses Typesense for vectors and SQLite for metadata.

Start Typesense:

```bash
cd examples/storage_providers/typesense
docker compose up -d
cd ../../..
```

Run ai-memory-hub from the repository root:

```bash
uv sync --extra typesense
uv run aim serve --config examples/storage_providers/typesense/config.yaml --host 127.0.0.1 --port 8000
```

Typesense stores hub-owned vectors in a `float[]` field and searches them with
`vector_query`. Run the common smoke commands from
`examples/storage_providers/README.md`.
