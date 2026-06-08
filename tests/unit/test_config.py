from pathlib import Path

import pytest

from memory.config import HubConfig, parse_config, load_config

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


def test_vector_provider_config_rejects_unknown_distance():
    with pytest.raises(ValueError, match="storage.vector.distance"):
        parse_config({"storage": {"vector": {"distance": "manhattan"}}})


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
