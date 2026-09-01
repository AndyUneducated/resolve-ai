"""Per-request tenant context for Postgres Row-Level Security (M9).

Prefer native Postgres RLS for isolation (`CREATE POLICY` plus
`current_setting`; see `infra/docker/migrations/0001_rls.sql`). The application
only injects tenant identity into the database session at transaction
boundaries. `tenant_session` opens a transaction, runs
`SET LOCAL app.tenant_id`, and yields the connection.

Why `SET LOCAL` (transaction-scoped) rather than `SET` (session-scoped):
`get_engine()` uses a process-level `lru_cache`, so pooled connections are
reused. `SET LOCAL` expires automatically with the transaction and cannot leak
the previous request's tenant context into the next request (fail-closed).
Never contaminate pooled connections with `SET`.
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
