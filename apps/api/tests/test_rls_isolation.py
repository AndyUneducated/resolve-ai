"""Postgres Row-Level Security 负向测试 (M9 · 多租户硬隔离).

验证数据库层（而非应用层）的隔离：
  1. 设了 tenant A 上下文 → 看不到 tenant B 的行；
  2. 在 tenant A 上下文里写 tenant B 的行 → 被 `WITH CHECK` 拒；
  3. 完全不设上下文 → 0 行 (fail-closed)。

需要 live Postgres、已应用 `infra/docker/migrations/0001_rls.sql`，**且应用以低权限
角色 `resolveai_app` 连库**（设 `APP_DATABASE_URL`）——超级用户 / BYPASSRLS 角色无条件
绕过 RLS，FORCE 也拦不住。任一条件不满足则自动 skip，保持默认 suite hermetic。
Embedding-free：只动 kb_documents 的明文列。
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

        # NOTE: 故意不带 WHERE tenant_id —— 让 RLS 兜底，模拟「应用漏写过滤」的 bug。
        async with tenant_session(engine, tenant_a) as conn:
            titles = [
                r[0]
                for r in (
                    await conn.execute(text("SELECT title FROM kb_documents"))
                ).fetchall()
            ]
        assert "A doc" in titles, "tenant A 应能看到自己的行"
        assert "B doc" not in titles, "RLS 未拦住跨租户读 —— tenant A 看到了 B 的行"
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
                # 在 A 的上下文里写一条 tenant_id = B 的行 → WITH CHECK 必须拒绝。
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

        # 不设 app.tenant_id：current_setting(..., true) → NULL → 0 行 (fail-closed)。
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text("SELECT count(*) FROM kb_documents WHERE tenant_id = :t"),
                    {"t": tenant_a},
                )
            ).scalar_one()
        assert count == 0, "未设租户上下文却看到了行 —— RLS 不是 fail-closed"
    finally:
        await _cleanup(engine, [tenant_a])
