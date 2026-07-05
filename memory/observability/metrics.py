from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib import import_module
from threading import Lock
from typing import Any

from memory.backend.log_safety import redact_secrets
from memory.config import MetricsConfig

logger = logging.getLogger(__name__)

MetricLabels = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MetricsStatus:
    enabled: bool
    configured: bool
    exporter: str | None = None
    protocol: str | None = None
    error_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "exporter": self.exporter,
            "protocol": self.protocol,
            "error_type": self.error_type,
        }


@dataclass
class _MetricState:
    counters: dict[tuple[str, MetricLabels], int] = field(default_factory=dict)
    histograms: dict[tuple[str, MetricLabels], list[float]] = field(default_factory=dict)
    gauges: dict[tuple[str, MetricLabels], float] = field(default_factory=dict)


class MetricsRecorder:
    def __init__(self) -> None:
        self._lock = Lock()
        self._state = _MetricState()
        self._otel_counters: dict[str, Any] = {}
        self._otel_histograms: dict[str, Any] = {}
        self._otel_gauges: dict[str, Any] = {}

    def configure_otel(self, meter: Any | None) -> None:
        if meter is None:
            return
        self._otel_counters = {
            "memory_mcp_tool_calls_total": meter.create_counter(
                "memory_mcp_tool_calls_total"
            ),
            "memory_api_requests_total": meter.create_counter(
                "memory_api_requests_total"
            ),
            "memory_insert_total": meter.create_counter("memory_insert_total"),
            "memory_embedding_failures_total": meter.create_counter(
                "memory_embedding_failures_total"
            ),
            "memory_provider_fallback_total": meter.create_counter(
                "memory_provider_fallback_total"
            ),
        }
        self._otel_histograms = {
            "memory_mcp_tool_duration_ms": meter.create_histogram(
                "memory_mcp_tool_duration_ms"
            ),
            "memory_api_request_duration_ms": meter.create_histogram(
                "memory_api_request_duration_ms"
            ),
            "memory_ingestion_stage_duration_ms": meter.create_histogram(
                "memory_ingestion_stage_duration_ms"
            ),
            "memory_embedding_duration_ms": meter.create_histogram(
                "memory_embedding_duration_ms"
            ),
            "memory_vector_search_duration_ms": meter.create_histogram(
                "memory_vector_search_duration_ms"
            ),
        }

    def increment(self, name: str, **labels: Any) -> None:
        safe_labels = _labels(labels)
        with self._lock:
            key = (name, safe_labels)
            self._state.counters[key] = self._state.counters.get(key, 0) + 1
        instrument = self._otel_counters.get(name)
        if instrument is not None:
            instrument.add(1, dict(safe_labels))

    def observe(self, name: str, value: float, **labels: Any) -> None:
        safe_labels = _labels(labels)
        with self._lock:
            key = (name, safe_labels)
            self._state.histograms.setdefault(key, []).append(float(value))
        instrument = self._otel_histograms.get(name)
        if instrument is not None:
            instrument.record(float(value), dict(safe_labels))

    def gauge(self, name: str, value: float, **labels: Any) -> None:
        safe_labels = _labels(labels)
        with self._lock:
            self._state.gauges[(name, safe_labels)] = float(value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": {
                    _snapshot_key(name, labels): value
                    for (name, labels), value in self._state.counters.items()
                },
                "histograms": {
                    _snapshot_key(name, labels): list(values)
                    for (name, labels), values in self._state.histograms.items()
                },
                "gauges": {
                    _snapshot_key(name, labels): value
                    for (name, labels), value in self._state.gauges.items()
                },
            }

    def reset(self) -> None:
        with self._lock:
            self._state = _MetricState()


metrics = MetricsRecorder()


def configure_metrics(config: MetricsConfig) -> MetricsStatus:
    if not config.enabled:
        return MetricsStatus(enabled=False, configured=False)
    try:
        return _configure_opentelemetry_metrics(config)
    except ImportError as exc:
        logger.warning(
            "OpenTelemetry metrics requested but optional dependencies are unavailable",
            extra={
                "event": "otel_metrics_unavailable",
                "error_type": type(exc).__name__,
                "provider": "opentelemetry",
            },
        )
        return MetricsStatus(
            enabled=False,
            configured=False,
            exporter=config.endpoint,
            protocol=config.protocol,
            error_type=type(exc).__name__,
        )
    except Exception as exc:
        logger.warning(
            "OpenTelemetry metrics setup failed: %s",
            redact_secrets(f"{type(exc).__name__}: {exc}"),
            extra={
                "event": "otel_metrics_setup_failed",
                "error_type": type(exc).__name__,
                "provider": "opentelemetry",
            },
        )
        return MetricsStatus(
            enabled=False,
            configured=False,
            exporter=config.endpoint,
            protocol=config.protocol,
            error_type=type(exc).__name__,
        )


def record_health_metrics(health_state: dict[str, Any]) -> None:
    mode = str(health_state.get("mode") or "unknown")
    metrics.gauge("memory_health_mode", 1.0, mode=mode)
    metrics.gauge(
        "memory_tokenizer_enabled",
        1.0 if health_state.get("tokenizer_enabled") is True else 0.0,
    )
    embedding_health = health_state.get("embedding_health")
    if isinstance(embedding_health, dict) and embedding_health.get("status") == "degraded":
        metrics.increment(
            "memory_embedding_failures_total",
            provider=embedding_health.get("provider") or "unknown",
            error_type=embedding_health.get("error_type") or "unknown",
        )
    vector_health = health_state.get("vector_health")
    if isinstance(vector_health, dict):
        rows = vector_health.get("rows")
        if isinstance(rows, int | float):
            metrics.gauge(
                "memory_vector_rows",
                float(rows),
                provider=vector_health.get("provider") or "unknown",
            )
    if health_state.get("vector_fallback_active") is True:
        metrics.increment(
            "memory_provider_fallback_total",
            requested_provider=health_state.get("requested_vector_provider") or "unknown",
            effective_provider=health_state.get("vector_provider") or "unknown",
        )


def _configure_opentelemetry_metrics(config: MetricsConfig) -> MetricsStatus:
    metrics_exporter = import_module(
        "opentelemetry.exporter.otlp.proto.grpc.metric_exporter"
    )
    metrics_sdk = import_module("opentelemetry.sdk.metrics")
    metrics_export = import_module("opentelemetry.sdk.metrics.export")
    metrics_api = import_module("opentelemetry.metrics")

    exporter = metrics_exporter.OTLPMetricExporter(
        endpoint=config.endpoint,
        insecure=True,
    )
    reader = metrics_export.PeriodicExportingMetricReader(exporter)
    provider = metrics_sdk.MeterProvider(metric_readers=[reader])
    metrics_api.set_meter_provider(provider)
    meter = metrics_api.get_meter("ai-memory-hub")
    metrics.configure_otel(meter)
    logger.info(
        "OpenTelemetry metrics configured",
        extra={
            "event": "otel_metrics_configured",
            "provider": "opentelemetry",
            "otel_exporter": config.endpoint,
            "otel_protocol": config.protocol,
        },
    )
    return MetricsStatus(
        enabled=True,
        configured=True,
        exporter=config.endpoint,
        protocol=config.protocol,
    )


def _labels(labels: dict[str, Any]) -> MetricLabels:
    return tuple(sorted((key, _label_value(value)) for key, value in labels.items()))


def _label_value(value: Any) -> str:
    if value is None:
        return "unknown"
    text = str(value).strip()
    if not text:
        return "unknown"
    return redact_secrets(text[:80])


def _snapshot_key(name: str, labels: MetricLabels) -> str:
    if not labels:
        return name
    label_text = ",".join(f"{key}={value}" for key, value in labels)
    return f"{name}{{{label_text}}}"
