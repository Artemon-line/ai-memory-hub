# SQLite + LanceDB

This is the default local persistent setup: SQLite stores metadata and LanceDB
stores vectors.

Run with Compose:

```bash
cd examples/storage_providers/sqlite-lancedb
docker compose up --build
```

The hub image is built from this directory's Containerfile and does not install
any optional provider extras.

Run the common smoke commands from `examples/storage_providers/README.md`.
