"""Hybrid retrieval — live Postgres integration (skipped if DB unreachable).

Embedding-free: exercises the lexical (ts_rank_cd) path + tenant isolation against
a real `kb_documents` table, so it runs without Ollama. Marked `integration` and
auto-skips when Postgres is not available, keeping the default suite hermetic.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _db_or_skip():
    from resolveai_api.retrieval.store import get_engine

    engine = get_engine()
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - env dependent
        pytest.skip(f"Postgres not reachable: {exc}")
    return engine


async def _seed(engine, tenant_id: str, *, title: str, content: str) -> None:
    """Insert a tenant + kb doc under that tenant's context.

    Goes through `tenant_session` (SET LOCAL app.tenant_id) so the inserts pass
    RLS WITH CHECK when the app connects as the low-priv `resolveai_app` role; a
    superuser connection simply ignores the (harmless) context.
    """
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


@pytest.mark.asyncio
async def test_lexical_search_and_tenant_isolation() -> None:
    engine = await _db_or_skip()
    from resolveai_api.core.db import tenant_session
    from resolveai_api.retrieval.store import KbStore

    tenant_a = f"itest-{uuid.uuid4().hex[:8]}"
    tenant_b = f"itest-{uuid.uuid4().hex[:8]}"
    store = KbStore()
    try:
        await _seed(
            engine,
            tenant_a,
            title="Gateway 502 runbook",
            content="Intermittent 502 bad gateway errors from the upstream load balancer.",
        )
        await _seed(
            engine,
            tenant_b,
            title="Other tenant doc",
            content="Intermittent 502 bad gateway errors for another tenant entirely.",
        )

        hits = await store.lexical_search(query="502 gateway errors", tenant_id=tenant_a, k=10)
        assert hits, "expected a lexical match for tenant A"
        assert all(doc.title != "Other tenant doc" for doc in hits), "tenant isolation breached"
        assert any("502" in doc.content for doc in hits)
    finally:
        for tid in (tenant_a, tenant_b):
            try:
                async with tenant_session(engine, tid) as conn:
                    await conn.execute(
                        text("DELETE FROM kb_documents WHERE tenant_id = :t"), {"t": tid}
                    )
                    await conn.execute(
                        text("DELETE FROM tenants WHERE id = :t"), {"t": tid}
                    )
            except Exception:  # pragma: no cover - best-effort teardown
                pass
