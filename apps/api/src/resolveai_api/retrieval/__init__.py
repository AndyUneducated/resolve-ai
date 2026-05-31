"""Hybrid retrieval — BM25 + dense (pgvector) + RRF + bge-reranker-v2-m3。"""

from __future__ import annotations

from functools import lru_cache

from resolveai_api.retrieval.hybrid import HybridRetriever
from resolveai_api.retrieval.types import RetrievalTrace, RetrievedDoc

__all__ = ["HybridRetriever", "RetrievalTrace", "RetrievedDoc", "get_retriever"]


@lru_cache
def get_retriever() -> HybridRetriever:
    """Process-wide retriever wired from settings (lazy: no DB/embedder until search)."""
    return HybridRetriever()
