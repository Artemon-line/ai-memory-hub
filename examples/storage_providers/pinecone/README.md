# Pinecone

This example uses Pinecone for vectors and SQLite for metadata.

Pinecone is hosted, so this example does not include a local Compose service.
Set `storage.vector_providers.pinecone.api_key` in `config.yaml`, or mount a
secret-managed config file with that value populated.

```bash
uv sync --extra pinecone
uv run aim serve --config examples/storage_providers/pinecone/config.yaml --host 127.0.0.1 --port 8000
```

The example assumes the Pinecone index already exists. Set `create_index: true`
only when the configured API key is allowed to create indexes in the configured
cloud and region.

Run the common smoke commands from `examples/storage_providers/README.md`.
