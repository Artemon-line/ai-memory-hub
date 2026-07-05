from __future__ import annotations

from memory.config import parse_config
from memory.observability.metrics import metrics
from memory.observability.privacy import debug_payload_snippet


def test_debug_payload_snippet_is_disabled_by_default() -> None:
    config = parse_config({})

    assert debug_payload_snippet(config, {"message": "raw user text"}) is None


def test_debug_payload_snippet_requires_flag_and_redacts() -> None:
    config = parse_config({"observability": {"debug_payloads": True}})

    snippet = debug_payload_snippet(
        config,
        {"message": "raw user text", "api_key": "sk-proj-secretvalue123456"},
    )

    assert snippet is not None
    assert "raw user text" in snippet
    assert "sk-proj-secretvalue123456" not in snippet
    assert "'api_key': ***" in snippet


def test_metric_labels_redact_secret_like_values() -> None:
    metrics.reset()

    metrics.increment(
        "memory_embedding_failures_total",
        provider="openai",
        error_type="api_key=sk-proj-secretvalue123456",
    )
    snapshot = metrics.snapshot()

    assert "sk-proj-secretvalue123456" not in str(snapshot)
    assert "api_key=***" in str(snapshot)
