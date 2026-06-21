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
                },
                "metadata_providers": {
                    "mongodb": {
                        "uri": "mongodb://user:pass@127.0.0.1:27017/app",
                        "database": "ai_memory_hub",
                        "conversations_collection": "conversations",
                    }
                },
            }
        }
    )

    assert config.storage.vector_providers.qdrant.url == "https://qdrant.example.com"
    assert config.storage.vector_providers.chromadb.collection == "memory_vectors_dev"
    assert config.storage.metadata_providers.mongodb.database == "ai_memory_hub"


def test_provider_config_accepts_implemented_expansion_providers() -> None:
    qdrant_config = parse_config({"providers": {"vector_db": "qdrant"}})
    atlas_config = parse_config({"providers": {"vector_db": "mongodb_atlas"}})
    mongodb_config = parse_config({"providers": {"metadata_db": "mongodb"}})

    assert qdrant_config.providers.vector_db == "qdrant"
    assert atlas_config.providers.vector_db == "mongodb_atlas"
    assert mongodb_config.providers.metadata_db == "mongodb"


def test_storage_provider_configs_override_qdrant_and_atlas_defaults() -> None:
    config = parse_config(
        {
            "storage": {
                "vector_providers": {
                    "qdrant": {
                        "url": "https://qdrant.example.com",
                        "api_key": "qdrant-key",
                        "collection": "custom_qdrant_vectors",
                        "prefer_grpc": True,
                    },
                    "mongodb_atlas": {
                        "uri": "mongodb+srv://user:secret@example.mongodb.net/app",
                        "database": "custom_memory",
                        "collection": "custom_atlas_vectors",
                        "index": "custom_vector_index",
                    },
                }
            }
        }
    )

    assert config.storage.vector_providers.qdrant.url == "https://qdrant.example.com"
    assert config.storage.vector_providers.qdrant.api_key == "qdrant-key"
    assert config.storage.vector_providers.qdrant.collection == "custom_qdrant_vectors"
    assert config.storage.vector_providers.qdrant.prefer_grpc is True
    assert config.storage.vector_providers.mongodb_atlas.uri == (
        "mongodb+srv://user:secret@example.mongodb.net/app"
    )
    assert config.storage.vector_providers.mongodb_atlas.database == "custom_memory"
    assert config.storage.vector_providers.mongodb_atlas.collection == "custom_atlas_vectors"
    assert config.storage.vector_providers.mongodb_atlas.index == "custom_vector_index"


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
