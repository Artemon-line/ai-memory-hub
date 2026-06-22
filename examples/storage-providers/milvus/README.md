# Milvus

This stack runs Milvus standalone with etcd and MinIO, then points
ai-memory-hub at Milvus for vectors and SQLite for metadata.

```bash
cd examples/storage-providers/milvus
docker compose up --build
```

Milvus can take longer than smaller vector stores to become ready. If the hub
starts before Milvus is accepting requests, Compose will restart the hub.

Milvus is bound to `127.0.0.1:19530`; ai-memory-hub is bound to
`127.0.0.1:8000`.

Run the common smoke commands from `examples/storage-providers/README.md`.

