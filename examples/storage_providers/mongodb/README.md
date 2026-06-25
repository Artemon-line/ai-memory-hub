# MongoDB Metadata

This stack runs MongoDB for conversation metadata and LanceDB for vectors. Use
this when MongoDB already owns application persistence but vectors should remain
local.

```bash
cd examples/storage_providers/mongodb
docker compose up --build
```

MongoDB is bound to `127.0.0.1:27017`; ai-memory-hub is bound to
`127.0.0.1:8000`.

The hub image is built from this directory's Containerfile and installs only
the `mongodb` optional dependency extra.

Run the common smoke commands from `examples/storage_providers/README.md`.
