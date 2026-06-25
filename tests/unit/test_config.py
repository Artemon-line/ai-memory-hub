import logging
from pathlib import Path

import pytest

from memory.config import HubConfig, ensure_token_hash_secret, load_config, parse_config


def test_load_config_default():
    # Now this should load config.yaml if it exists
    config = load_config()
    assert isinstance(config, HubConfig)
    
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if config_path.exists():
        # config.yaml says "openai"
        assert config.providers.embeddings == "openai"
        assert config.providers.embedding_dimension == 768
    else:
        # Default in code is also "openai" now (or whatever I set it to)
        assert config.providers.embeddings == "openai"

def test_load_config_from_file():
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        pytest.skip("config.yaml not found")
    
    config = load_config(config_path)
    assert isinstance(config, HubConfig)
    assert config.providers.embeddings == "openai"
    assert config.providers.embedding_dimension == 768

def test_load_config_non_existent():
    with pytest.raises(FileNotFoundError):
        load_config("non_existent_config.yaml")


def test_vector_provider_config_accepts_pgvector_and_memory_alias():
    pg_config = parse_config(
        {
            "providers": {"vector_db": "pgvector"},
            "storage": {"vector": {"distance": "inner_product"}},
        }
    )
    assert pg_config.providers.vector_db == "pgvector"
    assert pg_config.storage.vector.distance == "inner_product"

    memory_config = parse_config({"providers": {"vector_db": "in_memory"}})
    assert memory_config.providers.vector_db == "memory"

    chroma_config = parse_config({"providers": {"vector_db": "chromadb"}})
    assert chroma_config.providers.vector_db == "chromadb"


def test_vector_provider_config_rejects_unknown_distance():
    with pytest.raises(ValueError, match="storage.vector.distance"):
        parse_config({"storage": {"vector": {"distance": "manhattan"}}})


def test_storage_provider_expansion_config_models_validate() -> None:
    config = parse_config(
        {
            "storage": {
                "vector_providers": {
                    "pgvector": {
                        "url": "postgresql://user:pass@127.0.0.1:5432/app",
                        "table_name": "memory_vectors_dev",
                    },
                    "chromadb": {
                        "path": "./data/chroma",
                        "collection": "memory_vectors_dev",
                    },
                    "qdrant": {
                        "url": "https://qdrant.example.com",
                        "api_key": "qdrant-secret",
                        "collection": "memory_vectors",
                    },
                    "milvus": {
                        "uri": "http://127.0.0.1:19530",
                        "token": "milvus-secret",
                        "collection": "memory_vectors",
                    },
                    "weaviate": {
                        "url": "https://weaviate.example.com",
                        "api_key": "weaviate-secret",
                        "collection": "MemoryVector",
                    },
                    "mongodb_atlas": {
                        "uri": "mongodb+srv://user:pass@example.mongodb.net/app",
                        "database": "ai_memory_hub",
                        "collection": "memory_vectors",
                        "index": "memory_vector_index",
                    },
                    "elasticsearch": {
                        "url": "https://elastic.example.com",
                        "username": "elastic",
                        "password": "elastic-secret",
                        "index": "memory_vectors",
                    },
                    "opensearch": {
                        "url": "https://opensearch.example.com",
                        "username": "admin",
                        "password": "opensearch-secret",
                        "index": "memory_vectors",
                    },
                    "redis": {
                        "url": "redis://:redis-secret@127.0.0.1:6379/0",
                        "index": "memory_vectors",
                        "key_prefix": "memory_vectors:",
                    },
                    "pinecone": {
                        "api_key": "pinecone-secret",
                        "index": "memory-vectors",
                        "namespace": "project-a",
                        "cloud": "aws",
                        "region": "us-east-1",
                        "create_index": True,
                    },
                    "turbopuffer": {
                        "api_key": "turbopuffer-secret",
                        "namespace": "memory-vectors",
                        "region": "gcp-us-central1",
                    },
                    "vespa": {
                        "url": "https://vespa.example.com",
                        "token": "vespa-secret",
                        "namespace": "memory",
                        "schema": "memory_vector",
                        "rank_profile": "vector_similarity",
                    },
                    "typesense": {
                        "url": "https://typesense.example.com",
                        "api_key": "typesense-secret",
                        "collection": "memory_vectors",
                    },
                },
                "metadata_providers": {
                    "postgres": {
                        "url": "postgresql://user:pass@127.0.0.1:5432/app",
                    },
                    "mongodb": {
                        "uri": "mongodb://user:pass@127.0.0.1:27017/app",
                        "database": "ai_memory_hub",
                        "conversations_collection": "conversations",
                    }
                },
            }
        }
    )

    assert config.storage.vector_providers.pgvector.url == (
        "postgresql://user:pass@127.0.0.1:5432/app"
    )
    assert config.storage.vector_providers.pgvector.table_name == "memory_vectors_dev"
    assert config.storage.vector_providers.qdrant.url == "https://qdrant.example.com"
    assert config.storage.vector_providers.chromadb.collection == "memory_vectors_dev"
    assert config.storage.metadata_providers.postgres.url == (
        "postgresql://user:pass@127.0.0.1:5432/app"
    )
    assert config.storage.metadata_providers.mongodb.database == "ai_memory_hub"


def test_provider_config_accepts_implemented_expansion_providers() -> None:
    vector_providers = [
        "qdrant",
        "milvus",
        "weaviate",
        "mongodb_atlas",
        "elasticsearch",
        "opensearch",
        "redis",
        "pinecone",
        "turbopuffer",
        "vespa",
        "typesense",
    ]
    mongodb_config = parse_config({"providers": {"metadata_db": "mongodb"}})

    for provider in vector_providers:
        config = parse_config({"providers": {"vector_db": provider}})
        assert config.providers.vector_db == provider
    assert mongodb_config.providers.metadata_db == "mongodb"


def test_storage_provider_configs_override_expansion_defaults() -> None:
    config = parse_config(
        {
            "storage": {
                "vector_providers": {
                    "pgvector": {
                        "url": "postgresql://vector:secret@example.com/app",
                        "table_name": "custom_pgvector_vectors",
                    },
                    "qdrant": {
                        "url": "https://qdrant.example.com",
                        "api_key": "qdrant-key",
                        "collection": "custom_qdrant_vectors",
                        "prefer_grpc": True,
                    },
                    "milvus": {
                        "uri": "https://milvus.example.com",
                        "token": "milvus-token",
                        "collection": "custom_milvus_vectors",
                    },
                    "weaviate": {
                        "url": "https://weaviate.example.com",
                        "api_key": "weaviate-key",
                        "collection": "CustomMemoryVector",
                    },
                    "mongodb_atlas": {
                        "uri": "mongodb+srv://user:secret@example.mongodb.net/app",
                        "database": "custom_memory",
                        "collection": "custom_atlas_vectors",
                        "index": "custom_vector_index",
                    },
                    "elasticsearch": {
                        "url": "https://elastic.example.com",
                        "username": "elastic-user",
                        "password": "elastic-pass",
                        "index": "custom_elastic_vectors",
                    },
                    "opensearch": {
                        "url": "https://opensearch.example.com",
                        "username": "opensearch-user",
                        "password": "opensearch-pass",
                        "index": "custom_opensearch_vectors",
                    },
                    "redis": {
                        "url": "redis://:redis-pass@redis.example.com:6379/0",
                        "index": "custom_redis_vectors",
                        "key_prefix": "custom_vectors:",
                    },
                    "pinecone": {
                        "api_key": "pinecone-key",
                        "index": "custom-pinecone-vectors",
                        "namespace": "custom_namespace",
                        "cloud": "gcp",
                        "region": "us-central1",
                        "create_index": True,
                    },
                    "turbopuffer": {
                        "api_key": "turbopuffer-key",
                        "namespace": "custom-turbopuffer-vectors",
                        "region": "aws-us-east-1",
                    },
                    "vespa": {
                        "url": "https://vespa.example.com",
                        "token": "vespa-key",
                        "namespace": "custom_namespace",
                        "schema": "custom_schema",
                        "rank_profile": "custom_rank",
                    },
                    "typesense": {
                        "url": "https://typesense.example.com",
                        "api_key": "typesense-key",
                        "collection": "custom_typesense_vectors",
                    },
                },
                "metadata_providers": {
                    "postgres": {
                        "url": "postgresql://metadata:secret@example.com/app",
                    }
                },
            }
        }
    )

    assert config.storage.vector_providers.pgvector.url == (
        "postgresql://vector:secret@example.com/app"
    )
    assert config.storage.vector_providers.pgvector.table_name == (
        "custom_pgvector_vectors"
    )
    assert config.storage.vector_providers.qdrant.url == "https://qdrant.example.com"
    assert config.storage.vector_providers.qdrant.api_key == "qdrant-key"
    assert config.storage.vector_providers.qdrant.collection == "custom_qdrant_vectors"
    assert config.storage.vector_providers.qdrant.prefer_grpc is True
    assert config.storage.vector_providers.milvus.uri == "https://milvus.example.com"
    assert config.storage.vector_providers.milvus.token == "milvus-token"
    assert config.storage.vector_providers.milvus.collection == "custom_milvus_vectors"
    assert config.storage.vector_providers.weaviate.url == "https://weaviate.example.com"
    assert config.storage.vector_providers.weaviate.api_key == "weaviate-key"
    assert config.storage.vector_providers.weaviate.collection == "CustomMemoryVector"
    assert config.storage.vector_providers.mongodb_atlas.uri == (
        "mongodb+srv://user:secret@example.mongodb.net/app"
    )
    assert config.storage.vector_providers.mongodb_atlas.database == "custom_memory"
    assert config.storage.vector_providers.mongodb_atlas.collection == "custom_atlas_vectors"
    assert config.storage.vector_providers.mongodb_atlas.index == "custom_vector_index"
    assert config.storage.vector_providers.elasticsearch.url == "https://elastic.example.com"
    assert config.storage.vector_providers.elasticsearch.username == "elastic-user"
    assert config.storage.vector_providers.elasticsearch.password == "elastic-pass"
    assert config.storage.vector_providers.elasticsearch.index == "custom_elastic_vectors"
    assert config.storage.vector_providers.opensearch.url == "https://opensearch.example.com"
    assert config.storage.vector_providers.opensearch.username == "opensearch-user"
    assert config.storage.vector_providers.opensearch.password == "opensearch-pass"
    assert config.storage.vector_providers.opensearch.index == "custom_opensearch_vectors"
    assert config.storage.vector_providers.redis.url == (
        "redis://:redis-pass@redis.example.com:6379/0"
    )
    assert config.storage.vector_providers.redis.index == "custom_redis_vectors"
    assert config.storage.vector_providers.redis.key_prefix == "custom_vectors:"
    assert config.storage.vector_providers.pinecone.api_key == "pinecone-key"
    assert config.storage.vector_providers.pinecone.index == "custom-pinecone-vectors"
    assert config.storage.vector_providers.pinecone.namespace == "custom_namespace"
    assert config.storage.vector_providers.pinecone.cloud == "gcp"
    assert config.storage.vector_providers.pinecone.region == "us-central1"
    assert config.storage.vector_providers.pinecone.create_index is True
    assert config.storage.vector_providers.turbopuffer.api_key == "turbopuffer-key"
    assert (
        config.storage.vector_providers.turbopuffer.namespace
        == "custom-turbopuffer-vectors"
    )
    assert config.storage.vector_providers.turbopuffer.region == "aws-us-east-1"
    assert config.storage.vector_providers.vespa.url == "https://vespa.example.com"
    assert config.storage.vector_providers.vespa.token == "vespa-key"
    assert config.storage.vector_providers.vespa.namespace == "custom_namespace"
    assert config.storage.vector_providers.vespa.schema_name == "custom_schema"
    assert config.storage.vector_providers.vespa.rank_profile == "custom_rank"
    assert config.storage.vector_providers.typesense.url == "https://typesense.example.com"
    assert config.storage.vector_providers.typesense.api_key == "typesense-key"
    assert config.storage.vector_providers.typesense.collection == "custom_typesense_vectors"
    assert config.storage.metadata_providers.postgres.url == (
        "postgresql://metadata:secret@example.com/app"
    )


def test_storage_provider_config_rejects_invalid_urls_and_names() -> None:
    with pytest.raises(ValueError, match="storage.vector.qdrant.url"):
        parse_config({"storage": {"vector_providers": {"qdrant": {"url": "localhost"}}}})

    with pytest.raises(ValueError, match="storage.vector.chromadb.collection"):
        parse_config(
            {
                "storage": {
                    "vector_providers": {"chromadb": {"collection": "bad name"}}
                }
            }
        )

    with pytest.raises(ValueError, match="storage.vector.pgvector.table_name"):
        parse_config(
            {
                "storage": {
                    "vector_providers": {"pgvector": {"table_name": "bad-name"}}
                }
            }
        )

    with pytest.raises(ValueError, match="storage.metadata.postgres.url"):
        parse_config(
            {
                "storage": {
                    "metadata_providers": {"postgres": {"url": "localhost/db"}}
                }
            }
        )

    with pytest.raises(ValueError, match="metadata_dsn"):
        parse_config({"providers": {"metadata_dsn": "postgresql://example/db"}})


def test_tokenizer_and_ask_config_defaults():
    config = parse_config({})

    assert config.tokenizer.enabled is False
    assert config.tokenizer.encoding == "cl100k_base"
    assert config.ask.max_context_tokens == 2000


def test_ask_config_rejects_invalid_context_budget():
    with pytest.raises(ValueError, match="ask.max_context_tokens"):
        parse_config({"ask": {"max_context_tokens": 0}})


def test_retrieval_config_defaults_and_validation():
    config = parse_config({})

    assert config.retrieval.vector_score_threshold == 7.5
    assert config.retrieval.keyword_enabled is True
    assert config.retrieval.keyword_candidate_limit == 50
    assert config.retrieval.candidate_multiplier == 3

    tuned = parse_config(
        {
            "retrieval": {
                "vector_score_threshold": 1.2,
                "keyword_enabled": False,
                "keyword_candidate_limit": 25,
                "keyword_weight": 0.5,
                "metadata_weight": 0.25,
                "candidate_multiplier": 5,
            }
        }
    )
    assert tuned.retrieval.vector_score_threshold == 1.2
    assert tuned.retrieval.keyword_enabled is False
    assert tuned.retrieval.keyword_candidate_limit == 25
    assert tuned.retrieval.keyword_weight == 0.5
    assert tuned.retrieval.metadata_weight == 0.25
    assert tuned.retrieval.candidate_multiplier == 5

    with pytest.raises(ValueError, match="retrieval.vector_score_threshold"):
        parse_config({"retrieval": {"vector_score_threshold": -1}})


def test_chunking_config_defaults_and_validation():
    config = parse_config({})

    assert config.chunking.strategy == "message"
    assert config.chunking.max_tokens == 800
    assert config.chunking.overlap_tokens == 80

    token_config = parse_config(
        {"chunking": {"strategy": "token", "max_tokens": 128, "overlap_tokens": 16}}
    )
    assert token_config.chunking.strategy == "token"

    with pytest.raises(ValueError, match="chunking.strategy"):
        parse_config({"chunking": {"strategy": "paragraph"}})

    with pytest.raises(ValueError, match="chunking.overlap_tokens"):
        parse_config({"chunking": {"max_tokens": 8, "overlap_tokens": 8}})


def test_oauth_resource_server_config_requires_public_metadata() -> None:
    with pytest.raises(ValueError, match="api.public_base_url"):
        parse_config({"api": {"auth": "oauth_resource_server"}})

    with pytest.raises(ValueError, match="authorization_servers"):
        parse_config(
            {
                "api": {
                    "auth": "oauth_resource_server",
                    "public_base_url": "https://memory.example.com",
                    "oauth": {"jwt_secret": "secret"},
                }
            }
        )


def test_oauth_resource_server_config_derives_valid_defaults() -> None:
    config = parse_config(
        {
            "api": {
                "auth": "oauth_resource_server",
                "public_base_url": "https://memory.example.com/",
                "oauth": {
                    "authorization_servers": ["https://auth.example.com"],
                    "jwt_secret": "secret",
                },
            }
        }
    )

    assert config.api.public_base_url == "https://memory.example.com"
    assert config.api.oauth.scopes_supported == [
        "memory:read",
        "memory:write",
        "memory:admin",
    ]


def test_oauth_resource_uri_rejects_fragments() -> None:
    with pytest.raises(ValueError, match="api.oauth.resource"):
        parse_config(
            {
                "api": {
                    "auth": "oauth_resource_server",
                    "public_base_url": "https://memory.example.com",
                    "oauth": {
                        "authorization_servers": ["https://auth.example.com"],
                        "resource": "https://memory.example.com/mcp#frag",
                        "jwt_secret": "secret",
                    },
                }
            }
        )


def test_auth_none_warns_on_non_loopback_bind(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="memory.config"):
        config = parse_config({"api": {"auth": "none", "host": "0.0.0.0"}})

    assert config.api.auth == "none"
    assert "api.auth=none is configured with non-loopback host 0.0.0.0" in caplog.text


def test_bearer_token_auth_generates_token_hash_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AMH_TOKEN_HASH_SECRET", raising=False)
    config = parse_config(
        {
            "api": {"auth": "bearer_token"},
            "paths": {"data_dir": str(tmp_path)},
        }
    )

    secret = ensure_token_hash_secret(config)

    secret_path = tmp_path / ".token_hash_secret"
    assert secret
    assert secret_path.read_text(encoding="utf-8").strip() == secret
    assert secret == ensure_token_hash_secret(config)
