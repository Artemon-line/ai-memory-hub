# Storage Provider Examples

Checked-in examples live under `examples/storage-providers` and
`examples/postgres/pgvector`.

These examples use `providers.embeddings: local` so storage smoke tests are
credential-free and do not require OpenAI, Ollama, or another model service.
For production-quality semantic search, switch to a real embedding model and set
`providers.embedding_dimension` to match it.

## Example Matrix

| Provider setup | Path | Notes |
| --- | --- | --- |
| SQLite + LanceDB | `examples/storage-providers/local-lancedb` | Default local persistent setup |
| SQLite + in-memory vectors | `examples/storage-providers/memory` | Disposable vector storage for tests |
| Postgres + PGVector | `examples/postgres/pgvector` | Full runbook for Docker/Podman and MCP handoff |
| SQLite + ChromaDB | `examples/storage-providers/chromadb` | Local Chroma HTTP service |
| SQLite + Qdrant | `examples/storage-providers/qdrant` | Local Qdrant service |
| MongoDB metadata + LanceDB | `examples/storage-providers/mongodb` | MongoDB owns metadata only |
| MongoDB metadata + Atlas vectors | `examples/storage-providers/mongodb-atlas` | Hosted Atlas Vector Search |
| SQLite + Milvus | `examples/storage-providers/milvus` | Milvus standalone with etcd and MinIO |
| SQLite + Weaviate | `examples/storage-providers/weaviate` | Weaviate with no provider-side vectorizer |
| SQLite + Elasticsearch | `examples/storage-providers/elasticsearch` | Local single-node Elasticsearch |
| SQLite + OpenSearch | `examples/storage-providers/opensearch` | Local single-node OpenSearch |
| SQLite + Redis/RediSearch | `examples/storage-providers/redis` | Local Redis Stack service |
| SQLite + Typesense | `examples/storage-providers/typesense` | Local Typesense service |
| SQLite + Pinecone | `examples/storage-providers/pinecone` | Hosted managed vector search |
| SQLite + Turbopuffer | `examples/storage-providers/turbopuffer` | Hosted object-storage-backed vector search |

## CI Coverage

The normal CI suite runs shared fake-client contract tests for every vector
adapter and metadata provider. The dedicated provider workflow
`.github/workflows/storage-providers.yml` runs service-backed live tests for:

- ChromaDB
- Qdrant
- MongoDB
- Milvus
- Weaviate
- Elasticsearch
- OpenSearch
- Redis/RediSearch
- Typesense

Pinecone and Turbopuffer use the same live test entry point but require
externally supplied credentials. Pinecone also requires either an existing index
or `AMH_TEST_PINECONE_CREATE_INDEX=1`.

Postgres and PGVector live tests remain in the main CI workflow because they are
also used by Bruno API/MCP integration tests.

Hosted MongoDB Atlas Vector Search is covered by the same live test entry point,
but it requires externally supplied credentials and an existing Atlas vector
index.

## Common Smoke

After starting any local Compose example:

```bash
curl -fsS http://127.0.0.1:8000/ready
```

Then insert and search:

```bash
curl -fsS http://127.0.0.1:8000/memory/insert \
  -H "Content-Type: application/json" \
  -d '{
    "source": "storage-provider-example",
    "timestamp": "2026-01-01T00:00:00Z",
    "title": "Storage provider smoke",
    "messages": [
      {"role": "user", "text": "Remember that the storage provider smoke phrase is amber-vector."}
    ],
    "metadata": {"tags": ["storage", "smoke"]}
  }'

curl -fsS http://127.0.0.1:8000/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query":"amber-vector","top_k":3}'
```

Keep `api.auth: none` only for local smoke tests. Use bearer-token auth and a
trusted TLS/VPN/reverse-proxy boundary before exposing the hub outside loopback.
