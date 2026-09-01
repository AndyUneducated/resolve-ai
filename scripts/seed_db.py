"""Seed KB demo data: FAQ / runbook documents + embeddings (M6 Hybrid Retrieval).

Prefer existing libraries:
- Write to the database through SQLAlchemy + psycopg (already repository dependencies)
  instead of implementing a connection pool.
- Generate vectors with `retrieval.embedder.make_embedder()` (default:
  OllamaEmbeddings bge-m3, 1024 dimensions).
- Use one document fixture (`apps/api/tests/fixtures/kb_documents.jsonl`) as the shared
  corpus for demos, evaluation, and load tests.

Idempotent: delete and reinsert by (tenant_id, title), so repeated runs are safe.

Usage:
    uv run python scripts/seed_db.py
    uv run python scripts/seed_db.py --tenant demo --truncate
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "apps" / "api" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

from resolveai_api.config import get_settings  # noqa: E402
from resolveai_api.retrieval.embedder import make_embedder  # noqa: E402
from resolveai_api.retrieval.store import to_pgvector_literal  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

DEFAULT_KB = ROOT / "apps" / "api" / "tests" / "fixtures" / "kb_documents.jsonl"


def _load_docs(path: Path) -> list[dict[str, object]]:
    docs: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


async def seed_kb(*, tenant_id: str, kb_path: Path, truncate: bool) -> int:
    settings = get_settings()
    if settings.embedding_dim != 1024:
        raise SystemExit(
            f"EMBEDDING_DIM={settings.embedding_dim} but kb_documents.embedding is vector(1024); "
            "align the model/schema before seeding."
        )

    docs = _load_docs(kb_path)
    print(f"[seed] loaded {len(docs)} KB documents from {kb_path}")

    embedder = make_embedder()
    contents = [str(doc["content"]) for doc in docs]
    print(f"[seed] embedding {len(contents)} docs via {settings.embedding_model} ...")
    vectors = await embedder.aembed_documents(contents)

    bad = [i for i, v in enumerate(vectors) if len(v) != settings.embedding_dim]
    if bad:
        raise SystemExit(
            f"Embedding dim mismatch on docs {bad[:5]}: expected {settings.embedding_dim}. "
            f"Is EMBEDDING_MODEL={settings.embedding_model!r} a {settings.embedding_dim}-dim model?"
        )

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    inserted = 0
    async with engine.begin() as conn:
        # FORCE ROW LEVEL SECURITY subjects table owners to policies (M9). Inject
        # app.tenant_id into the transaction first, or WITH CHECK on tenants /
        # kb_documents will reject inserts. set_config(..., true) is SET LOCAL and
        # expires automatically when the transaction ends (fail closed).
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"),
            {"t": tenant_id},
        )
        await conn.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, :name) ON CONFLICT DO NOTHING"),
            {"id": tenant_id, "name": f"{tenant_id} tenant"},
        )
        if truncate:
            await conn.execute(
                text("DELETE FROM kb_documents WHERE tenant_id = :t"), {"t": tenant_id}
            )
        for doc, vector in zip(docs, vectors, strict=True):
            # Idempotent upsert keyed by (tenant_id, title).
            await conn.execute(
                text("DELETE FROM kb_documents WHERE tenant_id = :t AND title = :title"),
                {"t": tenant_id, "title": doc["title"]},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO kb_documents (tenant_id, title, content, embedding, metadata)
                    VALUES (:tenant_id, :title, :content, CAST(:embedding AS vector), CAST(:metadata AS jsonb))
                    """
                ),
                {
                    "tenant_id": tenant_id,
                    "title": doc["title"],
                    "content": doc["content"],
                    "embedding": to_pgvector_literal(vector),
                    "metadata": json.dumps(doc.get("metadata") or {}),
                },
            )
            inserted += 1
    await engine.dispose()
    print(f"[seed] upserted {inserted} KB documents for tenant {tenant_id!r}.")
    return inserted


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed KB documents with embeddings.")
    parser.add_argument("--tenant", default=None, help="Tenant id (default: settings.default_tenant_id)")
    parser.add_argument("--kb", type=Path, default=DEFAULT_KB)
    parser.add_argument("--truncate", action="store_true", help="Delete tenant KB before seeding.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tenant_id = args.tenant or get_settings().default_tenant_id
    asyncio.run(seed_kb(tenant_id=tenant_id, kb_path=args.kb, truncate=args.truncate))
    return 0


if __name__ == "__main__":
    sys.exit(main())
