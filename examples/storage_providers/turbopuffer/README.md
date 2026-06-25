# Turbopuffer

This example uses Turbopuffer for vectors and SQLite for metadata.

Turbopuffer is hosted, so this example does not include a local Compose service.
Set `storage.vector_providers.turbopuffer.api_key` in `config.yaml`, or mount a
secret-managed config file with that value populated.

```bash
uv sync --extra turbopuffer
uv run aim serve --config examples/storage_providers/turbopuffer/config.yaml --host 127.0.0.1 --port 8000
```

The example writes to the configured namespace in the configured region. Use a
separate namespace for tests so cleanup and cost tracking are straightforward.

Run the common smoke commands from `examples/storage_providers/README.md`.
