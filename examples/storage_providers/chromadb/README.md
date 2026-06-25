# ChromaDB

This stack runs ChromaDB over HTTP and uses SQLite for metadata.

```bash
cd examples/storage_providers/chromadb
docker compose up --build
```

ChromaDB is bound to `127.0.0.1:8001`; ai-memory-hub is bound to
`127.0.0.1:8000`.

The hub image is built from this directory's Containerfile and installs only
the `chromadb` optional dependency extra.

Run the common smoke commands from `examples/storage_providers/README.md`.
