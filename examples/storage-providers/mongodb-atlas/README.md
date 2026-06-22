# MongoDB Atlas Vector Search

This hosted example uses MongoDB for metadata and MongoDB Atlas Vector Search
for vectors. It is not a local Compose stack because Atlas Vector Search is a
hosted Atlas feature.

1. Create an Atlas cluster and database.
2. Create a vector search index named `memory_vector_index` on collection
   `memory_vectors`.
3. Configure the index path as `vector` and dimensions as `32` for this example.
4. Replace both MongoDB URIs in `config.yaml`.
5. Run from the repository root:

```bash
uv sync --dev --extra mongodb
uv run aim serve --config examples/storage-providers/mongodb-atlas/config.yaml --host 127.0.0.1 --port 8000
```

For CI or local live tests, set:

```bash
export AMH_TEST_MONGODB_ATLAS_URI="mongodb+srv://user:password@example.mongodb.net/app"
export AMH_TEST_MONGODB_ATLAS_DATABASE="ai_memory_hub"
export AMH_TEST_MONGODB_ATLAS_COLLECTION="memory_vectors"
export AMH_TEST_MONGODB_ATLAS_INDEX="memory_vector_index"
uv run pytest -q tests/integration/test_storage_features.py -k live_mongodb_atlas_vector_contract
```
