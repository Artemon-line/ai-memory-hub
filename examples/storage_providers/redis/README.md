# Redis/RediSearch

This stack runs Redis Stack for vectors and SQLite for metadata.

Files in this example:

- `compose.yaml` starts Redis Stack and ai-memory-hub.
- `Containerfile` builds a slim hub image with only the `redis` optional extra.
- `config.yaml` selects `providers.metadata_db: sqlite` and
  `providers.vector_db: redis`.

```bash
cd examples/storage_providers/redis
docker compose up --build
```

Redis is bound to `127.0.0.1:6379`; ai-memory-hub is bound to
`127.0.0.1:8000`.

The hub image is built from this directory's Containerfile and installs only
the `redis` optional dependency extra.

Run the common smoke commands from `examples/storage_providers/README.md`.

Clean up the containers while keeping volumes:

```bash
docker compose down
```

Delete Redis, SQLite, and hub data volumes:

```bash
docker compose down -v
```
