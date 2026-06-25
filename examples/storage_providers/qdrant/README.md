# Qdrant

This stack runs Qdrant for vectors and SQLite for metadata.

```bash
cd examples/storage_providers/qdrant
docker compose up --build
```

Qdrant is bound to `127.0.0.1:6333`; ai-memory-hub is bound to
`127.0.0.1:8000`.

Run the common smoke commands from `examples/storage_providers/README.md`.
