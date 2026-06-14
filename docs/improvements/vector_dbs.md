# Vector Database Options

- Qdrant — Rust-based, strong filtering
- Weaviate — hybrid search, GraphQL
- Pinecone — managed/serverless
- Vespa — Yahoo's, large-scale hybrid
- Turbopuffer — serverless, S3-backed
- Redis / RediSearch — vector module
- Elastic / OpenSearch — k-NN built in
- MongoDB Atlas — $vectorSearch
- Typesense / Meilisearch — lighter search engines w/ vectors
- DuckDB VSS, sqlite-vec — embedded/analytical
- Faiss, ScaNN, HNSWlib — libs, not DBs
Pick by axis: embedded (Chroma, Lance, sqlite-vec) vs managed (Pinecone, Turbopuffer) vs self-hosted scale (Milvus, Qdrant, Vespa) vs already-have-the-DB (PGVector, Mongo, Elastic, Redis).
