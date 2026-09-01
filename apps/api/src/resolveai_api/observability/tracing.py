"""OTel setup with FastAPI auto-instrumentation and custom agent/tool spans.

EvalGate retrieves traces through the OTel collector for online regression.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def get_tracer(name: str) -> Any | None:
    """Return an OTel tracer if the SDK is importable + configured; else None.

    Mirrors the no-op pattern in `retrieval/hybrid.py` so span emission is free
    when OTel is not installed/wired (tests, chaos load with tracing disabled).
    """
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except Exception:  # pragma: no cover - import/runtime guard
        return None


@contextmanager
def span(
    tracer: Any | None,
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
) -> Iterator[Any | None]:
    """Start an OTel span, or a no-op context when `tracer` is None."""
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as otel_span:
        if attributes:
            for key, value in attributes.items():
                if value is not None:
                    try:
                        otel_span.set_attribute(key, value)
                    except Exception:  # pragma: no cover - defensive
                        otel_span.set_attribute(key, str(value))
        yield otel_span


def setup_tracing(app: FastAPI, *, service_name: str, endpoint: str) -> None:
    if not endpoint:
        logger.info("OTel endpoint is not configured; skipping tracing setup.")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTel SDK is not installed; skipping tracing.")
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    logger.info("OTel tracing → %s", endpoint)
