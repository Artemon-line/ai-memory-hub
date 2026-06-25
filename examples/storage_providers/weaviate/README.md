# Weaviate

This stack runs Weaviate with anonymous local access and no built-in vectorizer.
ai-memory-hub supplies vectors directly.

```bash
cd examples/storage_providers/weaviate
docker compose up --build
```

Weaviate is bound to `127.0.0.1:8080`; ai-memory-hub is bound to
`127.0.0.1:8000`.

Run the common smoke commands from `examples/storage_providers/README.md`.
