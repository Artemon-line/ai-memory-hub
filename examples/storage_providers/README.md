# Storage Provider Fixtures

These directories are maintainer fixtures for supported metadata and vector
providers. The human-facing examples are:

- Quickstart: repository root config and root `Containerfile`.
User-facing runnable examples live one level up:

- Quickstart: repository root config and root `Containerfile`.
- Local stack: `examples/local-stack`, combining Postgres, PGVector, Ollama
  embeddings, Google OAuth, observability, and a public HTTPS URL for remote
  agents.

Use fixture directories from the repository root unless a provider README says
otherwise. Most fixtures use deterministic local embeddings so provider smoke
tests do not need OpenAI, Ollama, or any external model service.

For real semantic or multilingual retrieval, switch to an embedding model that
supports the languages you store and query, and set the matching dimension. If
you change embedding model/provider/options on persistent vector data, reindex
or use a separate vector namespace/index; same-dimension model swaps can still
break ranking.

## Supported Fixtures

| Provider | Example | CI coverage |
| --- | --- | --- |
| SQLite metadata + LanceDB vectors | `sqlite-lancedb` | default unit/integration suite; local Compose smoke |
| In-memory vectors | `memory` | default unit/integration suite |
| Postgres metadata + PGVector vectors | `../local-stack` | compose smoke fixture |
| Qdrant vectors | `qdrant` | provider workflow |
| MongoDB metadata | `mongodb` | provider workflow; local Compose smoke |
| MongoDB Atlas Vector Search | `mongodb-atlas` | optional hosted workflow inputs |
| Milvus/Zilliz vectors | `milvus` | manual/scheduled provider workflow |
| Weaviate vectors | `weaviate` | provider workflow |
| Elasticsearch vectors | `elasticsearch` | provider workflow |
| OpenSearch vectors | `opensearch` | provider workflow |
| Redis/RediSearch vectors | `redis` | provider workflow; local Compose smoke |
| Vespa vectors | `vespa` | optional deployed-application live test |
| Typesense vectors | `typesense` | provider workflow |
| Pinecone vectors | `pinecone` | hosted optional live test |
| Turbopuffer vectors | `turbopuffer` | hosted optional live test |

## Common Smoke Check

ChromaDB is temporarily unavailable in `v0.1.0` because the upstream
`chromadb` package has an unresolved critical advisory with no patched release.
The adapter remains in the repository for future re-enable.

Main local Compose smoke:

```bash
cd examples/local-stack
docker compose up --build
```

After starting an example stack, verify readiness:

```bash
curl -fsS http://127.0.0.1:8000/ready
```

Insert and search a conversation:

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

## Cleanup

For Compose examples:

```bash
docker compose down
```

Delete provider data volumes:

```bash
docker compose down -v
```

Do not expose these local examples on untrusted networks with `api.auth: none`.
Switch to `api.auth: bearer_token` and put TLS, VPN, or a trusted reverse proxy
in front of the hub before internet-facing use.
