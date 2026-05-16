"""Async LangGraph checkpointer factory.

行业对齐：用 `AsyncPostgresSaver`（与 FastAPI 异步栈一致）做 dev/prod state
持久化；测试场景下用 `MemorySaver`（LangGraph 官方测试 fixture）。

Checkpoint thread_id 由调用方按 `tenant::customer::thread` 命名（决策 4 · Layer 4）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from resolveai_api.config import get_settings


@asynccontextmanager
async def lifespan_checkpointer() -> AsyncIterator[BaseCheckpointSaver]:
    """Yield a checkpointer for the FastAPI lifespan; closes the pg conn on shutdown."""
    settings = get_settings()

    if settings.checkpoint_backend == "memory":
        yield MemorySaver()
        return

    if settings.checkpoint_backend != "postgres":
        raise ValueError(
            f"Unsupported CHECKPOINT_BACKEND={settings.checkpoint_backend!r}; "
            "expected 'postgres' or 'memory'."
        )

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(settings.psycopg_dsn) as saver:
        await saver.setup()  # idempotent — creates checkpoint tables on first run
        yield saver
