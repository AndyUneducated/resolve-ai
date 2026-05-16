"""bge-reranker-v2-m3 — 客服 FAQ 检索的精排层。"""

from __future__ import annotations

from resolveai_api.retrieval.hybrid import RetrievedDoc


class Reranker:
    """TODO: 用 sentence-transformers 加载 BAAI/bge-reranker-v2-m3。"""

    async def rerank(
        self, *, query: str, docs: list[RetrievedDoc], top_k: int = 5
    ) -> list[RetrievedDoc]:
        return docs[:top_k]
