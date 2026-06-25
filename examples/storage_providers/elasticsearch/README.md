# Elasticsearch

This stack runs Elasticsearch single-node with security disabled for local
smoke tests. ai-memory-hub stores vectors in a `dense_vector` field and keeps
metadata in SQLite.

```bash
cd examples/storage_providers/elasticsearch
docker compose up --build
```

Elasticsearch is bound to `127.0.0.1:9200`; ai-memory-hub is bound to
`127.0.0.1:8000`.

Run the common smoke commands from `examples/storage_providers/README.md`.

For a secured cluster, set `storage.vector_providers.elasticsearch.username`
and `password` in `config.yaml`.

