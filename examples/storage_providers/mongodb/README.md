# MongoDB Metadata

This stack runs MongoDB for conversation metadata and LanceDB for vectors. Use
this when MongoDB already owns application persistence but vectors should remain
local.

Files in this example:

- `compose.yaml` starts MongoDB and ai-memory-hub.
- `Containerfile` builds a slim hub image with only the `mongodb` optional extra.
- `config.yaml` selects `providers.metadata_db: mongodb` and
  `providers.vector_db: lancedb`.

```bash
cd examples/storage_providers/mongodb
docker compose up --build
```

MongoDB is bound to `127.0.0.1:27017`; ai-memory-hub is bound to
`127.0.0.1:8000`.

The hub image is built from this directory's Containerfile and installs only
the `mongodb` optional dependency extra.

Run the common smoke commands from `examples/storage_providers/README.md`.

Clean up the containers while keeping volumes:

```bash
docker compose down
```

Delete MongoDB and hub data volumes:

```bash
docker compose down -v
```
