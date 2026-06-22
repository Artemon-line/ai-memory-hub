# ChromaDB

This stack runs ChromaDB over HTTP and uses SQLite for metadata.

```bash
cd examples/storage-providers/chromadb
docker compose up --build
```

ChromaDB is bound to `127.0.0.1:8001`; ai-memory-hub is bound to
`127.0.0.1:8000`.

Run the common smoke commands from `examples/storage-providers/README.md`.

