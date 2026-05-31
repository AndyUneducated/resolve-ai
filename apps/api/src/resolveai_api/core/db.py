"""Per-request tenant context for Postgres Row-Level Security (M9).

调库优先：隔离用 Postgres 原生 RLS（`CREATE POLICY` + `current_setting`，见
`infra/docker/migrations/0001_rls.sql`），应用侧只负责在事务边界把租户身份注入
数据库会话。`tenant_session` 开一个事务、`SET LOCAL app.tenant_id`、yield 连接。

为什么是 `SET LOCAL`（事务级）而非 `SET`（会话级）：`get_engine()` 的连接池是
进程级 `lru_cache`，连接会被复用；`SET LOCAL` 随事务结束自动失效，不会把上个
请求的租户上下文泄漏给下一个请求（fail-closed）。严禁用 `SET` 污染池连接。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


@asynccontextmanager
async def tenant_session(
    engine: AsyncEngine, tenant_id: str
) -> AsyncIterator[AsyncConnection]:
    """Open a txn, pin `app.tenant_id` for RLS, then yield the connection.

    Use `set_config(name, value, is_local=true)` rather than `SET LOCAL ... = :t`
    because plain `SET` does not accept bind parameters; `set_config` does and is
    injection-safe. The `true` third arg scopes the GUC to the current transaction.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        yield conn
