"""FastAPI entry point — wires lifespan resources (checkpointer + MCP tools)."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from resolveai_api.agents.supervisor import SupervisorGraph
from resolveai_api.api import chat, health, tickets
from resolveai_api.config import get_settings
from resolveai_api.core.checkpointer import lifespan_checkpointer
from resolveai_api.mcp.loader import build_client, load_tools
from resolveai_api.observability.tracing import setup_tracing

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_tracing(app, service_name=settings.otel_service_name, endpoint=settings.otel_endpoint)

    async with AsyncExitStack() as stack:
        checkpointer = await stack.enter_async_context(lifespan_checkpointer())

        # Discover MCP tools eagerly — failures here should not block the API.
        try:
            client = build_client()
            mcp_tools = await load_tools(client)
            logger.info("loaded %d MCP tools", len(mcp_tools))
        except Exception:  # pragma: no cover — defensive
            logger.exception("MCP tool loading failed; serving with 0 tools")
            mcp_tools = []

        app.state.checkpointer = checkpointer
        app.state.mcp_tools = mcp_tools
        app.state.supervisor = SupervisorGraph(checkpointer=checkpointer, mcp_tools=mcp_tools)
        yield


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
