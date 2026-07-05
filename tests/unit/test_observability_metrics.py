from __future__ import annotations

import pytest

from memory.config import MetricsConfig, parse_config
from memory.ingestion.base_agent import BaseIngestionAgent
from memory.interfaces.mcp_server import build_tool_handlers
from memory.observability import metrics as metrics_module
from memory.observability.metrics import metrics


class StubAgent(BaseIngestionAgent):
    async def ingest_messages(
        self,
        conversation_json: dict[str, object],
        *,
        owner_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, object]:
        return {"status": "ok", "id": str(conversation_json.get("id") or "generated")}


def test_metrics_config_defaults_to_disabled() -> None:
    config = parse_config({})

    assert config.observability.metrics.enabled is False
    assert config.observability.metrics.endpoint == "http://otel-collector:4317"
    assert config.observability.metrics.protocol == "grpc"


def test_metrics_config_accepts_enabled_grpc_otlp() -> None:
    config = parse_config(
        {
            "observability": {
                "metrics": {
                    "enabled": True,
                    "endpoint": "http://localhost:4317/",
                    "protocol": "GRPC",
                }
            }
        }
    )

    assert config.observability.metrics.enabled is True
    assert config.observability.metrics.endpoint == "http://localhost:4317"
    assert config.observability.metrics.protocol == "grpc"


def test_metrics_setup_is_noop_when_disabled() -> None:
    status = metrics_module.configure_metrics(MetricsConfig(enabled=False))

    assert status.enabled is False
    assert status.configured is False
    assert status.error_type is None


def test_metrics_setup_does_not_raise_when_optional_dependencies_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_dependencies(_config: MetricsConfig):
        raise ImportError("opentelemetry")

    monkeypatch.setattr(
        metrics_module, "_configure_opentelemetry_metrics", missing_dependencies
    )

    status = metrics_module.configure_metrics(MetricsConfig(enabled=True))

    assert status.enabled is False
    assert status.configured is False
    assert status.error_type == "ImportError"
    assert status.exporter == "http://otel-collector:4317"


@pytest.mark.asyncio
async def test_mcp_tool_metrics_increment_for_success_and_error() -> None:
    metrics.reset()
    agent = StubAgent(config={"providers": {"agent": "mvp"}, "interfaces": {"api": "true"}})
    handlers = build_tool_handlers(agent)

    success = await handlers["memory_validate"](
        {
            "source": "pytest",
            "timestamp": "2026-01-01T00:00:00Z",
            "messages": [{"role": "user", "text": "hello"}],
            "metadata": {"imported_at": "2026-01-01T00:00:00Z"},
        }
    )
    error = await handlers["memory_validate"]({"messages": "not-a-list"})

    snapshot = metrics.snapshot()

    assert success["status"] == "ok"
    assert error["status"] == "error"
    assert (
        snapshot["counters"][
            "memory_mcp_tool_calls_total{error_code=none,status=ok,tool=memory_validate}"
        ]
        == 1
    )
    assert (
        snapshot["counters"][
            "memory_mcp_tool_calls_total{error_code=invalid_input,status=error,tool=memory_validate}"
        ]
        == 1
    )
    assert (
        len(
            snapshot["histograms"][
                "memory_mcp_tool_duration_ms{status=ok,tool=memory_validate}"
            ]
        )
        == 1
    )
