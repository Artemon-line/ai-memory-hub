# Storage Provider Examples

Checked-in examples live under `examples/storage_providers` and
`examples/storage_providers/postgres-pgvector`.

These examples use `providers.embeddings: local` so storage smoke tests are
credential-free and do not require OpenAI, Ollama, or another model service.
For production-quality semantic search, switch to a real embedding model and set
`providers.embedding_dimension` to match it.

Multilingual retrieval is supported when the selected embedding model supports
the languages you store and query. Use the same embedding provider/model/options
for both ingestion and search. If you switch models on a persistent vector
store, reindex the data or use a separate vector namespace/index; same-dimension
model swaps can still corrupt ranking.

Each running hub uses one metadata provider and one vector provider. The active
providers are selected by `providers.metadata_db` and `providers.vector_db`.
Provider settings under `storage.metadata_providers` and
`storage.vector_providers` are configuration candidates; listing a provider
block there does not activate it.

Installing dependencies is separate from configuration. Optional SDKs are
downloaded only when you install their extras, such as `uv sync --extra qdrant`
or `uv sync --extra postgres`, or when using a provider-local container image
that deliberately installs that provider's extra.

The API-key-free local Compose examples for PostgreSQL/PGVector, MongoDB,
Redis, ChromaDB, and SQLite/LanceDB use provider-local Containerfiles. Those
images install only the optional dependency extras needed by that example.
For Redis and MongoDB specifically, the checked-in directories include all
local-run assets:

- `examples/storage_providers/redis/compose.yaml`
- `examples/storage_providers/redis/Containerfile`
- `examples/storage_providers/redis/config.yaml`
- `examples/storage_providers/mongodb/compose.yaml`
- `examples/storage_providers/mongodb/Containerfile`
- `examples/storage_providers/mongodb/config.yaml`

## Example Matrix

| Provider setup | Path | Notes |
| --- | --- | --- |
| SQLite + LanceDB | `examples/storage_providers/sqlite-lancedb` | Default local persistent setup |
| SQLite + in-memory vectors | `examples/storage_providers/memory` | Disposable vector storage for tests |
| Postgres + PGVector | `examples/storage_providers/postgres-pgvector` | Full runbook for Docker/Podman and MCP handoff |
| SQLite + ChromaDB | `examples/storage_providers/chromadb` | Local Chroma HTTP service |
| SQLite + Qdrant | `examples/storage_providers/qdrant` | Local Qdrant service |
| MongoDB metadata + LanceDB | `examples/storage_providers/mongodb` | MongoDB owns metadata only |
| MongoDB metadata + Atlas vectors | `examples/storage_providers/mongodb-atlas` | Hosted Atlas Vector Search |
| SQLite + Milvus | `examples/storage_providers/milvus` | Milvus standalone with etcd and MinIO |
| SQLite + Weaviate | `examples/storage_providers/weaviate` | Weaviate with no provider-side vectorizer |
| SQLite + Elasticsearch | `examples/storage_providers/elasticsearch` | Local single-node Elasticsearch |
| SQLite + OpenSearch | `examples/storage_providers/opensearch` | Local single-node OpenSearch |
| SQLite + Redis/RediSearch | `examples/storage_providers/redis` | Local Redis Stack service |
| SQLite + Vespa | `examples/storage_providers/vespa` | Existing deployed Vespa application |
| SQLite + Typesense | `examples/storage_providers/typesense` | Local Typesense service |
| SQLite + Pinecone | `examples/storage_providers/pinecone` | Hosted managed vector search |
| SQLite + Turbopuffer | `examples/storage_providers/turbopuffer` | Hosted object-storage-backed vector search |

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

Pinecone, Turbopuffer, and Vespa use the same live test entry point but require
externally supplied credentials or services. Pinecone also requires either an
existing index or `AMH_TEST_PINECONE_CREATE_INDEX=1`. Vespa requires a deployed
application package matching `examples/storage_providers/vespa/config.yaml`.

Postgres and PGVector live tests remain in the main CI workflow because they are
also used by Bruno API/MCP integration tests.

Hosted MongoDB Atlas Vector Search is covered by the same live test entry point,
but it requires externally supplied credentials and an existing Atlas vector
index.

## Hosted Provider Notes

Zilliz uses the Milvus adapter. Set `providers.vector_db: milvus`, point
`storage.vector_providers.milvus.uri` at the Zilliz endpoint, and set
`storage.vector_providers.milvus.token` from the Zilliz API key/token. The hub
validates collection dimensionality and metric when the provider SDK exposes
that metadata, creates or refreshes the vector index, and loads the collection
before search.

Weaviate Cloud uses the Weaviate adapter. Set `providers.vector_db: weaviate`,
use the cluster URL in `storage.vector_providers.weaviate.url`, and set
`storage.vector_providers.weaviate.api_key`. The adapter stores hub-generated
vectors directly and creates collections with provider-side vectorization
disabled when the SDK config classes are available.

Elastic Cloud uses the Elasticsearch adapter. Set
`providers.vector_db: elasticsearch`, use the deployment HTTPS endpoint in
`storage.vector_providers.elasticsearch.url`, and configure username/password
or equivalent basic auth credentials. OpenSearch-hosted services use the
OpenSearch adapter and the matching `opensearch` config block.

## Provider Limitations

The hub keeps API and MCP response shapes stable across storage providers, but
provider internals still differ:

- **Consistency/read-after-write:** Elasticsearch and OpenSearch inserts and
  deletes request `refresh=true`; other hosted providers may still expose
  provider-specific propagation latency.
- **Index readiness:** Atlas Vector Search must report the configured search
  index as ready before startup succeeds. Milvus/Zilliz collections are loaded
  before search. Hosted providers can still require operator-side index or
  schema creation before the hub starts.
- **Score interpretation:** Search results keep the existing `score` field, but
  backends may use cosine similarity, distance, certainty, or provider-specific
  score ranges internally. Treat scores as provider-local ranking signals, not
  globally comparable values.
- **Distance metrics:** The shared config supports `cosine`, `l2`, and
  `inner_product` where the provider supports them. Some providers expose
  different names or restrict metric changes after an index/collection exists.
- **Local vs hosted behavior:** Local Compose examples favor fast smoke tests
  and API-key-free setup. Hosted deployments add auth, quotas, network latency,
  managed index lifecycle, and provider-specific operational limits.

## Common Smoke

API-key-free local Compose examples:

```bash
cd examples/storage_providers/sqlite-lancedb
docker compose up --build

cd examples/storage_providers/chromadb
docker compose up --build

cd examples/storage_providers/mongodb
docker compose up --build

cd examples/storage_providers/redis
docker compose up --build

cd examples/storage_providers/postgres-pgvector
docker compose up --build
```

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

Keep `api.auth: none` only for local smoke tests. Use
`api.auth: oauth_resource_server` with TLS, a reverse proxy, VPN, or another
trusted private network before exposing MCP outside loopback.
