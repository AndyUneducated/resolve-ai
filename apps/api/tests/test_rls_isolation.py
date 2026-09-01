"""Negative Postgres Row-Level Security tests (M9 · strict multi-tenant isolation).

Verify isolation at the database layer rather than the application layer:
  1. Tenant A context cannot see tenant B rows.
  2. Writing a tenant B row from tenant A context is rejected by `WITH CHECK`.
  3. No context returns zero rows (fail closed).

Requires live Postgres with `infra/docker/migrations/0001_rls.sql` applied and an
application connection using the low-privilege `resolveai_app` role (set
`APP_DATABASE_URL`). Superuser / BYPASSRLS roles always bypass RLS, even with
FORCE. Skip automatically if any requirement is unmet to keep the default suite
hermetic. Embedding-free: touches only plaintext kb_documents columns.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _db_or_skip():
    """Return engine if Postgres is reachable AND RLS is forced on kb_documents."""
    from resolveai_api.retrieval.store import get_engine

    engine = get_engine()
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity "
                        "FROM pg_class WHERE relname = 'kb_documents'"
                    )
                )
            ).first()
            privileged = (
                await conn.execute(
                    text(
                        "SELECT rolsuper OR rolbypassrls FROM pg_roles "
                        "WHERE rolname = current_user"
                    )
                )
            ).scalar()
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"Postgres not reachable: {exc}")
    if row is None or not (row[0] and row[1]):
        pytest.skip(
            "RLS not enabled/forced on kb_documents; "
            "apply infra/docker/migrations/0001_rls.sql first."
        )
    if privileged:
        pytest.skip(
            "connected as a superuser/BYPASSRLS role which bypasses RLS; "
            "set APP_DATABASE_URL to the non-superuser resolveai_app role."
        )
    return engine


async def _seed_tenant(engine, tenant_id: str, *, title: str, content: str) -> None:
    """Insert a tenant + one kb doc under that tenant's RLS context."""
    from resolveai_api.core.db import tenant_session

    async with tenant_session(engine, tenant_id) as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, :id) ON CONFLICT DO NOTHING"),
            {"id": tenant_id},
        )
        await conn.execute(
            text(
                "INSERT INTO kb_documents (tenant_id, title, content) "
                "VALUES (:t, :title, :content)"
            ),
            {"t": tenant_id, "title": title, "content": content},
        )


async def _cleanup(engine, tenant_ids: list[str]) -> None:
    from resolveai_api.core.db import tenant_session

    for tid in tenant_ids:
        try:
            async with tenant_session(engine, tid) as conn:
                await conn.execute(
                    text("DELETE FROM kb_documents WHERE tenant_id = :t"), {"t": tid}
                )
                await conn.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tid})
        except Exception:  # pragma: no cover - best-effort teardown
            pass


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant_read() -> None:
    engine = await _db_or_skip()
    from resolveai_api.core.db import tenant_session

    tenant_a = f"rls-{uuid.uuid4().hex[:8]}"
    tenant_b = f"rls-{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(engine, tenant_a, title="A doc", content="alpha content a")
        await _seed_tenant(engine, tenant_b, title="B doc", content="bravo content b")

        # NOTE: Intentionally omit WHERE tenant_id so RLS provides the fallback,
        # simulating an application bug that forgot the filter.
        async with tenant_session(engine, tenant_a) as conn:
            titles = [
                r[0]
                for r in (
                    await conn.execute(text("SELECT title FROM kb_documents"))
                ).fetchall()
            ]
        assert "A doc" in titles, "tenant A should be able to see its own row"
        assert "B doc" not in titles, "RLS failed to block tenant A from reading tenant B's row"
    finally:
        await _cleanup(engine, [tenant_a, tenant_b])


@pytest.mark.asyncio
async def test_rls_with_check_blocks_cross_tenant_write() -> None:
    engine = await _db_or_skip()
    from resolveai_api.core.db import tenant_session

    tenant_a = f"rls-{uuid.uuid4().hex[:8]}"
    tenant_b = f"rls-{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(engine, tenant_a, title="A doc", content="alpha content a")
        await _seed_tenant(engine, tenant_b, title="B doc", content="bravo content b")

        with pytest.raises(Exception) as exc_info:
            async with tenant_session(engine, tenant_a) as conn:
                # Write a tenant_id = B row from A's context; WITH CHECK must reject it.
                await conn.execute(
                    text(
                        "INSERT INTO kb_documents (tenant_id, title, content) "
                        "VALUES (:t, :title, :content)"
                    ),
                    {"t": tenant_b, "title": "smuggled", "content": "should be rejected"},
                )
        assert "row-level security" in str(exc_info.value).lower()
    finally:
        await _cleanup(engine, [tenant_a, tenant_b])


@pytest.mark.asyncio
async def test_rls_fail_closed_without_context() -> None:
    engine = await _db_or_skip()

    tenant_a = f"rls-{uuid.uuid4().hex[:8]}"
    try:
        await _seed_tenant(engine, tenant_a, title="A doc", content="alpha content a")

        # Without app.tenant_id, current_setting(..., true) → NULL → zero rows (fail closed).
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text("SELECT count(*) FROM kb_documents WHERE tenant_id = :t"),
                    {"t": tenant_a},
                )
            ).scalar_one()
        assert count == 0, "rows were visible without tenant context; RLS did not fail closed"
    finally:
        await _cleanup(engine, [tenant_a])
