from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.config import ensure_token_hash_secret, parse_config
from memory.ingestion import mvp_ingestion


def _base_config(tmp_path: Path, *, auth: str) -> dict[str, Any]:
    config: dict[str, Any] = {
        "api": {"auth": auth},
        "interfaces": {"api": True, "mcp": True},
        "paths": {"data_dir": str(tmp_path / "data")},
        "providers": {
            "embeddings": "local",
            "metadata_db": "sqlite",
            "vector_db": "in_memory",
        },
    }
    if auth == "oauth_resource_server":
        config["api"] = {
            "auth": "oauth_resource_server",
            "public_base_url": "https://memory.example.com",
            "oauth": {
                "authorization_servers": ["https://auth.example.com"],
                "audience": "https://memory.example.com/mcp",
                "issuer": "https://auth.example.com",
                "jwks": {
                    "keys": [
                        {
                            "kty": "oct",
                            "kid": "test-key",
                            "k": "oauth-secret-value",
                            "alg": "HS256",
                        }
                    ]
                },
                "scopes_supported": ["memory:read", "memory:write"],
            },
        }
    return config


def _client(tmp_path: Path, *, auth: str) -> TestClient:
    config = _base_config(tmp_path, auth=auth)
    if auth == "bearer_token":
        os.environ["AMH_TOKEN_HASH_SECRET"] = "operational-observability-secret"
        parsed = parse_config(config)
        ensure_token_hash_secret(parsed)
        SQLiteMetadataStore(Path(parsed.paths.data_dir) / "metadata.sqlite3").create_auth_token(
            owner_id="owner-a", token="token-a"
        )
        return TestClient(create_app(config=parsed))
    return TestClient(create_app(config=config))


def test_health_and_ready_are_public_and_secret_free_under_every_auth_mode(
    tmp_path: Path,
) -> None:
    for auth in ("none", "bearer_token", "oauth_resource_server"):
        with _client(tmp_path / auth, auth=auth) as client:
            health = client.get("/health")
            ready = client.get("/ready")

        assert health.status_code == 200, health.text
        assert ready.status_code == 200, ready.text
        payload = json.dumps({"health": health.json(), "ready": ready.json()})
        assert "operational-observability-secret" not in payload
        assert "token-a" not in payload
        assert "oauth-secret-value" not in payload


def test_oauth_protected_resource_metadata_is_public_and_secret_free(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, auth="oauth_resource_server") as client:
        root = client.get("/.well-known/oauth-protected-resource")
        mcp = client.get("/.well-known/oauth-protected-resource/mcp")

    assert root.status_code == 200, root.text
    assert mcp.status_code == 200, mcp.text
    body = json.dumps({"root": root.json(), "mcp": mcp.json()})
    assert "https://memory.example.com/mcp" in body
    assert "https://auth.example.com" in body
    assert "oauth-secret-value" not in body
    assert "token" not in body.lower()


def test_request_failure_logs_are_structured_and_do_not_include_payload_or_query_secrets(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_query = "query-token-secret"
    secret_payload = "payload-secret-phrase"
    with _client(tmp_path, auth="bearer_token") as client:
        with caplog.at_level(logging.INFO, logger="memory.api.server"):
            response = client.post(
                f"/memory/search?access_token={secret_query}",
                json={"query": secret_payload},
            )

    assert response.status_code == 401
    log_text = caplog.text
    assert secret_query not in log_text
    assert secret_payload not in log_text
    record = next(
        record for record in caplog.records if getattr(record, "event", "") == "http_request_error"
    )
    assert record.method == "POST"
    assert record.path == "/memory/search"
    assert record.status_code == 401
    assert record.auth_mode == "bearer_token"
    assert UUID(record.request_id)


def test_http_request_id_is_propagated_to_response_and_error_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_id = "pytest-request-id-123"
    with _client(tmp_path, auth="bearer_token") as client:
        with caplog.at_level(logging.INFO, logger="memory.api.server"):
            response = client.post(
                "/memory/search",
                json={"query": "request id propagation"},
                headers={"x-request-id": request_id},
            )

    assert response.status_code == 401
    assert response.headers["x-request-id"] == request_id
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", "") == "http_request_error"
    )
    assert record.request_id == request_id


def test_http_request_id_is_generated_when_missing(tmp_path: Path) -> None:
    with _client(tmp_path, auth="none") as client:
        response = client.get("/health")

    assert response.status_code == 200, response.text
    assert UUID(response.headers["x-request-id"])


def test_startup_logs_fallback_policy_and_dry_run_state(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="memory.ingestion.mvp_ingestion"):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "in_memory"},
                "paths": {"data_dir": str(tmp_path)},
                "storage": {"dry_run": True},
            }
        )

    policy_record = next(
        record
        for record in caplog.records
        if getattr(record, "event", "") == "runtime_startup_policy"
    )
    assert policy_record.vector_provider == "memory"
    assert policy_record.storage_profile == "local"
    assert policy_record.vector_fallback_allowed is True
    assert policy_record.dry_run is True
    assert "DRY-RUN enabled" in caplog.text


def test_allow_fallback_true_for_persistent_vectors_warns_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenLanceDB:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("lancedb unavailable password=vector-secret")

    monkeypatch.setattr(mvp_ingestion, "LanceDBVectorStore", BrokenLanceDB)
    mvp_ingestion._FALLBACK_POLICY_WARNED.clear()
    config = {
        "providers": {"embeddings": "local", "vector_db": "lancedb"},
        "paths": {"data_dir": str(tmp_path)},
        "storage": {"vector": {"allow_fallback": True}},
    }

    with caplog.at_level(logging.WARNING, logger="memory.ingestion.mvp_ingestion"):
        mvp_ingestion.build_runtime(config)
        mvp_ingestion.build_runtime(config)

    warning_records = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "vector_fallback_policy_warning"
    ]
    assert len(warning_records) == 1
    assert warning_records[0].non_durable_fallback_possible is True
    assert "non-durable" in warning_records[0].getMessage()
    assert "vector-secret" not in caplog.text
    fallback_records = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "vector_fallback_activated"
    ]
    assert len(fallback_records) == 2
    assert fallback_records[0].requested_vector_provider == "lancedb"
    assert fallback_records[0].effective_vector_provider == "memory"
    assert fallback_records[0].error_type == "RuntimeError"


def test_production_profile_with_vector_fallback_warns_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenLanceDB:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("lancedb unavailable password=vector-secret")

    monkeypatch.setattr(mvp_ingestion, "LanceDBVectorStore", BrokenLanceDB)
    mvp_ingestion._FALLBACK_POLICY_WARNED.clear()
    mvp_ingestion._PRODUCTION_FALLBACK_POLICY_WARNED.clear()
    config = {
        "providers": {"embeddings": "local", "vector_db": "lancedb"},
        "paths": {"data_dir": str(tmp_path)},
        "storage": {"profile": "production", "vector": {"allow_fallback": True}},
    }

    with caplog.at_level(logging.WARNING, logger="memory.ingestion.mvp_ingestion"):
        mvp_ingestion.build_runtime(config)
        mvp_ingestion.build_runtime(config)

    warning_records = [
        record
        for record in caplog.records
        if getattr(record, "event", "") == "production_vector_fallback_policy_warning"
    ]
    assert len(warning_records) == 1
    assert warning_records[0].storage_profile == "production"
    assert warning_records[0].vector_provider == "lancedb"
    assert warning_records[0].vector_fallback_allowed is True
    assert warning_records[0].recommended_vector_fallback_allowed is False
    assert "vector-secret" not in caplog.text
