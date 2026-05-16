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
from resolveai_api.mcp.toolbelt import ToolBelt
from resolveai_api.observability.tracing import setup_tracing

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_tracing(app, service_name=settings.otel_service_name, endpoint=settings.otel_endpoint)

    async with AsyncExitStack() as stack:
        checkpointer = await stack.enter_async_context(lifespan_checkpointer())

        toolbelt = await ToolBelt.from_settings()
        logger.info("ToolBelt loaded %d MCP tools", len(toolbelt))

        app.state.checkpointer = checkpointer
        app.state.toolbelt = toolbelt
        app.state.mcp_tools = toolbelt.tools  # back-compat for any direct readers
        app.state.supervisor = SupervisorGraph(checkpointer=checkpointer, toolbelt=toolbelt)
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
