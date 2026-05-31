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


@pytest.mark.asyncio
async def test_lexical_search_and_tenant_isolation() -> None:
    engine = await _db_or_skip()
    from resolveai_api.retrieval.store import KbStore

    tenant_a = f"itest-{uuid.uuid4().hex[:8]}"
    tenant_b = f"itest-{uuid.uuid4().hex[:8]}"
    store = KbStore()
    try:
        async with engine.begin() as conn:
            for tid in (tenant_a, tenant_b):
                await conn.execute(
                    text("INSERT INTO tenants (id, name) VALUES (:id, :id) ON CONFLICT DO NOTHING"),
                    {"id": tid},
                )
            await conn.execute(
                text(
                    "INSERT INTO kb_documents (tenant_id, title, content) "
                    "VALUES (:t, :title, :content)"
                ),
                {
                    "t": tenant_a,
                    "title": "Gateway 502 runbook",
                    "content": "Intermittent 502 bad gateway errors from the upstream load balancer.",
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO kb_documents (tenant_id, title, content) "
                    "VALUES (:t, :title, :content)"
                ),
                {
                    "t": tenant_b,
                    "title": "Other tenant doc",
                    "content": "Intermittent 502 bad gateway errors for another tenant entirely.",
                },
            )

        hits = await store.lexical_search(query="502 gateway errors", tenant_id=tenant_a, k=10)
        assert hits, "expected a lexical match for tenant A"
        assert all(doc.title != "Other tenant doc" for doc in hits), "tenant isolation breached"
        assert any("502" in doc.content for doc in hits)
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM kb_documents WHERE tenant_id = ANY(:ts)"),
                {"ts": [tenant_a, tenant_b]},
            )
            await conn.execute(
                text("DELETE FROM tenants WHERE id = ANY(:ts)"),
                {"ts": [tenant_a, tenant_b]},
            )
