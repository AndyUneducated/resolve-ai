"""KB store — 薄封装：直接调 Postgres 全文检索 (ts_rank_cd) + pgvector (cosine)。

不自研索引/打分：
- BM25 lexical → `ts_rank_cd(content_tsv, plainto_tsquery('english', :q))`
  （索引 `kb_tsv_idx` GIN）
- dense → `embedding <=> :qvec`（cosine 距离，索引 `kb_embedding_idx` HNSW）

所有查询强制带 `tenant_id`（对齐 M9 多租户 / 未来 RLS），不提供无租户全库检索。
向量参数以 pgvector 文本字面量 `'[...]'::vector` 形式绑定，省去逐连接 register_vector。
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from resolveai_api.config import get_settings
from resolveai_api.retrieval.types import RetrievedDoc


@lru_cache
def get_engine() -> AsyncEngine:
    """Process-wide async engine (psycopg3). DSN already uses postgresql+psycopg://."""
    settings = get_settings()
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def to_pgvector_literal(vec: list[float]) -> str:
    """pgvector accepts the textual form '[0.1,0.2,...]' cast to ::vector."""
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


_DENSE_SQL = text(
    """
    SELECT id, title, content, metadata,
           1 - (embedding <=> CAST(:qvec AS vector)) AS score
    FROM kb_documents
    WHERE tenant_id = :tenant_id AND embedding IS NOT NULL
    ORDER BY embedding <=> CAST(:qvec AS vector)
    LIMIT :k
    """
)

_LEXICAL_SQL = text(
    """
    SELECT id, title, content, metadata,
           ts_rank_cd(content_tsv, plainto_tsquery('english', :q)) AS score
    FROM kb_documents
    WHERE tenant_id = :tenant_id
      AND content_tsv @@ plainto_tsquery('english', :q)
    ORDER BY score DESC
    LIMIT :k
    """
)


def _row_to_doc(row: object, *, source: str) -> RetrievedDoc:
    mapping = row._mapping  # type: ignore[attr-defined]
    metadata = mapping["metadata"] or {}
    return RetrievedDoc(
        id=int(mapping["id"]),
        title=str(mapping["title"]),
        content=str(mapping["content"]),
        score=float(mapping["score"] or 0.0),
        metadata=dict(metadata),
        source=source,
    )


class KbStore:
    """Async access to `kb_documents`. One engine per process, injectable for tests."""

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self._engine = engine

    @property
    def engine(self) -> AsyncEngine:
        return self._engine or get_engine()

    async def dense_search(
        self, *, query_embedding: list[float], tenant_id: str, k: int
    ) -> list[RetrievedDoc]:
        async with self.engine.connect() as conn:
            result = await conn.execute(
                _DENSE_SQL,
                {
                    "qvec": to_pgvector_literal(query_embedding),
                    "tenant_id": tenant_id,
                    "k": k,
                },
            )
            return [_row_to_doc(row, source="dense") for row in result.fetchall()]

    async def lexical_search(
        self, *, query: str, tenant_id: str, k: int
    ) -> list[RetrievedDoc]:
        async with self.engine.connect() as conn:
            result = await conn.execute(
                _LEXICAL_SQL,
                {"q": query, "tenant_id": tenant_id, "k": k},
            )
            return [_row_to_doc(row, source="lexical") for row in result.fetchall()]
