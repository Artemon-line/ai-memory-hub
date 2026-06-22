# OpenSearch

This stack runs OpenSearch single-node with the security plugin disabled for
local smoke tests. ai-memory-hub stores vectors in a `knn_vector` field and
keeps metadata in SQLite.

```bash
cd examples/storage-providers/opensearch
docker compose up --build
```

OpenSearch is bound to `127.0.0.1:9200`; ai-memory-hub is bound to
`127.0.0.1:8000`.

Run the common smoke commands from `examples/storage-providers/README.md`.

For a secured cluster, set `storage.vector_providers.opensearch.username` and
`password` in `config.yaml`.
