from __future__ import annotations

import tomllib
from pathlib import Path

from fastapi import FastAPI

from memory.config import TracingConfig, parse_config
from memory.observability import tracing


def test_tracing_config_defaults_to_disabled() -> None:
    config = parse_config({})

    assert config.observability.tracing.enabled is False
    assert config.observability.tracing.endpoint == "http://otel-collector:4317"
    assert config.observability.tracing.protocol == "grpc"
    assert config.observability.tracing.sample_ratio == 1.0
    assert config.observability.tracing.service_name == "ai-memory-hub"
    assert config.observability.tracing.environment == "local"


def test_tracing_config_accepts_enabled_grpc_otlp() -> None:
    config = parse_config(
        {
            "observability": {
                "tracing": {
                    "enabled": True,
                    "endpoint": "http://localhost:4317/",
                    "protocol": "GRPC",
                    "sample_ratio": 0.25,
                    "service_name": "memory-dev",
                    "environment": "test",
                }
            }
        }
    )

    assert config.observability.tracing.enabled is True
    assert config.observability.tracing.endpoint == "http://localhost:4317"
    assert config.observability.tracing.protocol == "grpc"
    assert config.observability.tracing.sample_ratio == 0.25
    assert config.observability.tracing.service_name == "memory-dev"
    assert config.observability.tracing.environment == "test"


def test_tracing_setup_is_noop_when_disabled() -> None:
    app = FastAPI()

    status = tracing.configure_tracing(TracingConfig(enabled=False), app=app)

    assert status.enabled is False
    assert status.configured is False
    assert status.error_type is None


def test_tracing_setup_does_not_raise_when_optional_dependencies_are_missing(
    monkeypatch,
) -> None:
    def missing_dependencies(_config: TracingConfig, *, app):
        raise ImportError("opentelemetry")

    monkeypatch.setattr(tracing, "_configure_opentelemetry", missing_dependencies)

    status = tracing.configure_tracing(TracingConfig(enabled=True), app=FastAPI())

    assert status.enabled is False
    assert status.configured is False
    assert status.error_type == "ImportError"
    assert status.exporter == "http://otel-collector:4317"


def test_tracing_setup_does_not_raise_when_exporter_setup_fails(monkeypatch) -> None:
    def broken_exporter(_config: TracingConfig, *, app):
        raise RuntimeError("collector unavailable password=secret")

    monkeypatch.setattr(tracing, "_configure_opentelemetry", broken_exporter)

    status = tracing.configure_tracing(TracingConfig(enabled=True), app=FastAPI())

    assert status.enabled is False
    assert status.configured is False
    assert status.error_type == "RuntimeError"
    assert status.exporter == "http://otel-collector:4317"


def test_observability_extra_declares_otel_dependencies() -> None:
    with Path("pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    dependencies = pyproject["project"]["optional-dependencies"]["observability"]
    expected = {
        "opentelemetry-api",
        "opentelemetry-sdk",
        "opentelemetry-exporter-otlp-proto-grpc",
        "opentelemetry-instrumentation-fastapi",
        "opentelemetry-instrumentation-httpx",
        "opentelemetry-instrumentation-requests",
        "opentelemetry-instrumentation-psycopg",
    }

    assert expected <= {dependency.split(">=")[0] for dependency in dependencies}
