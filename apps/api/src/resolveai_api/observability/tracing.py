"""OTel 装配 — FastAPI 自动 instrument + 自定义 Agent / Tool span。

EvalGate 通过 OTel collector 拉到 trace 后做 online regression。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def setup_tracing(app: FastAPI, *, service_name: str, endpoint: str) -> None:
    if not endpoint:
        logger.info("OTel endpoint 未配置，跳过 tracing 装配。")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTel SDK 未安装，跳过 tracing。")
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    logger.info("OTel tracing → %s", endpoint)
