"""FastAPI 入口 — 装配中间件、路由、observability。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from resolveai_api.api import chat, health, tickets
from resolveai_api.config import get_settings
from resolveai_api.observability.tracing import setup_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    settings = get_settings()
    setup_tracing(app, service_name=settings.otel_service_name, endpoint=settings.otel_endpoint)
    yield
    # shutdown — 留给后续：关 MCP client / DB pool


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ResolveAI API",
        version="0.0.1",
        description="Adversarially-Hardened Multi-Agent Customer Support",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(tickets.router, prefix="/api/v1")

    return app


app = create_app()
