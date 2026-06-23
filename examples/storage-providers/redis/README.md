# Redis/RediSearch

This stack runs Redis Stack for vectors and SQLite for metadata.

```bash
cd examples/storage-providers/redis
docker compose up --build
```

Redis is bound to `127.0.0.1:6379`; ai-memory-hub is bound to
`127.0.0.1:8000`.

Run the common smoke commands from `examples/storage-providers/README.md`.
