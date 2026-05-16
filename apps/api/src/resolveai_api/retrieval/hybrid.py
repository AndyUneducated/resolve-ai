"""Hybrid 检索 — BM25 (Postgres tsvector) + dense (pgvector) + RRF 融合。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievedDoc:
    id: int
    title: str
    content: str
    score: float
    metadata: dict[str, object]


class HybridRetriever:
    """TODO: 接 Postgres + pgvector，BM25 走 ts_rank_cd，dense 走 vector cosine。"""

    async def search(
        self,
        *,
        query: str,
        tenant_id: str,
        k: int = 10,
        rrf_k: int = 60,
    ) -> list[RetrievedDoc]:
        """每路检索取 top-50，RRF 融合后用 reranker 精排回 top-k。"""
        # TODO: 实现
        return []
