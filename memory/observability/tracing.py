from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Iterator

from memory.backend.log_safety import redact_secrets
from memory.config import TracingConfig

logger = logging.getLogger(__name__)

_SAFE_ATTRIBUTE_TYPES = (str, bool, int, float)


@dataclass(frozen=True)
class TracingStatus:
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


def configure_tracing(config: TracingConfig, *, app: Any | None = None) -> TracingStatus:
    if not config.enabled:
        return TracingStatus(enabled=False, configured=False)
    try:
        return _configure_opentelemetry(config, app=app)
    except ImportError as exc:
        logger.warning(
            "OpenTelemetry tracing requested but optional dependencies are unavailable",
            extra={
                "event": "otel_tracing_unavailable",
                "error_type": type(exc).__name__,
                "provider": "opentelemetry",
            },
        )
        return TracingStatus(
            enabled=False,
            configured=False,
            exporter=config.endpoint,
            protocol=config.protocol,
            error_type=type(exc).__name__,
        )
    except Exception as exc:
        logger.warning(
            "OpenTelemetry tracing setup failed: %s",
            redact_secrets(f"{type(exc).__name__}: {exc}"),
            extra={
                "event": "otel_tracing_setup_failed",
                "error_type": type(exc).__name__,
                "provider": "opentelemetry",
            },
        )
        return TracingStatus(
            enabled=False,
            configured=False,
            exporter=config.endpoint,
            protocol=config.protocol,
            error_type=type(exc).__name__,
        )


def _configure_opentelemetry(config: TracingConfig, *, app: Any | None) -> TracingStatus:
    trace = import_module("opentelemetry.trace")
    OTLPSpanExporter = import_module(
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
    ).OTLPSpanExporter
    FastAPIInstrumentor = import_module(
        "opentelemetry.instrumentation.fastapi"
    ).FastAPIInstrumentor
    HTTPXClientInstrumentor = import_module(
        "opentelemetry.instrumentation.httpx"
    ).HTTPXClientInstrumentor
    PsycopgInstrumentor = import_module(
        "opentelemetry.instrumentation.psycopg"
    ).PsycopgInstrumentor
    RequestsInstrumentor = import_module(
        "opentelemetry.instrumentation.requests"
    ).RequestsInstrumentor
    Resource = import_module("opentelemetry.sdk.resources").Resource
    TracerProvider = import_module("opentelemetry.sdk.trace").TracerProvider
    BatchSpanProcessor = import_module(
        "opentelemetry.sdk.trace.export"
    ).BatchSpanProcessor
    TraceIdRatioBased = import_module(
        "opentelemetry.sdk.trace.sampling"
    ).TraceIdRatioBased

    resource = Resource.create(
        {
            "service.name": config.service_name,
            "deployment.environment": config.environment,
        }
    )
    provider = TracerProvider(
        resource=resource,
        sampler=TraceIdRatioBased(config.sample_ratio),
    )
    exporter = OTLPSpanExporter(endpoint=config.endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    if app is not None:
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    RequestsInstrumentor().instrument(tracer_provider=provider)
    PsycopgInstrumentor().instrument(tracer_provider=provider)

    logger.info(
        "OpenTelemetry tracing configured",
        extra={
            "event": "otel_tracing_configured",
            "provider": "opentelemetry",
            "otel_exporter": config.endpoint,
            "otel_protocol": config.protocol,
        },
    )
    return TracingStatus(
        enabled=True,
        configured=True,
        exporter=config.endpoint,
        protocol=config.protocol,
    )


@contextmanager
def start_observability_span(
    name: str, *, attributes: dict[str, Any] | None = None
) -> Iterator[Any | None]:
    """Start a best-effort OpenTelemetry span without making OTel a hard dependency."""
    try:
        trace = import_module("opentelemetry.trace")
        tracer = trace.get_tracer("ai-memory-hub")
    except ImportError:
        yield None
        return

    with tracer.start_as_current_span(name) as span:
        _set_safe_span_attributes(span, attributes or {})
        yield span


def _set_safe_span_attributes(span: Any, attributes: dict[str, Any]) -> None:
    for key, value in attributes.items():
        if isinstance(value, _SAFE_ATTRIBUTE_TYPES):
            span.set_attribute(key, value)
        elif value is None:
            continue
        else:
            span.set_attribute(key, str(value))
