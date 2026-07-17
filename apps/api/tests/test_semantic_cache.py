"""M13 — semantic cache: cosine NN, TTL, tenant isolation, LRU + retriever wiring.

All hermetic: hand-built vectors + fake embedder/store, no Postgres, no models.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from langchain_core.embeddings import Embeddings
from resolveai_api.retrieval.hybrid import HybridRetriever
from resolveai_api.retrieval.reranker import Reranker
from resolveai_api.retrieval.semantic_cache import (
    SemanticCache,
    cosine_similarity,
)
from resolveai_api.retrieval.types import RetrievedDoc

# ------------------------------ cosine ------------------------------


def test_cosine_similarity_bounds() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0  # degenerate → 0
    assert cosine_similarity([1.0], [1.0, 0.0]) == 0.0  # length mismatch → 0


# ------------------------------ cache behavior ------------------------------


def test_hit_when_above_threshold_miss_when_below() -> None:
    cache = SemanticCache(threshold=0.95)
    cache.put(tenant_id="t1", embedding=[1.0, 0.0, 0.0], value="ANS", query="q")

    near = cache.get(tenant_id="t1", embedding=[0.999, 0.02, 0.0])
    assert near.hit is True and near.value == "ANS"

    far = cache.get(tenant_id="t1", embedding=[0.0, 1.0, 0.0])
    assert far.hit is False
    assert cache.hits == 1 and cache.misses == 1


def test_tenant_isolation_never_bleeds() -> None:
    cache = SemanticCache(threshold=0.9)
    cache.put(tenant_id="t1", embedding=[1.0, 0.0], value="t1-answer", query="q")
    # identical embedding, different tenant → must miss (no cross-tenant reuse)
    assert cache.get(tenant_id="t2", embedding=[1.0, 0.0]).hit is False
    assert cache.get(tenant_id="t1", embedding=[1.0, 0.0]).hit is True


def test_ttl_expiry() -> None:
    now = [1000.0]
    cache = SemanticCache(threshold=0.9, ttl_s=10.0, clock=lambda: now[0])
    cache.put(tenant_id="t1", embedding=[1.0, 0.0], value="v", query="q")
    now[0] = 1005.0
    assert cache.get(tenant_id="t1", embedding=[1.0, 0.0]).hit is True  # within TTL
    now[0] = 1020.0
    assert cache.get(tenant_id="t1", embedding=[1.0, 0.0]).hit is False  # expired


def test_lru_capacity_evicts_oldest() -> None:
    cache = SemanticCache(threshold=0.99, max_entries=2)
    cache.put(tenant_id="t", embedding=[1.0, 0.0, 0.0], value="a", query="qa")
    cache.put(tenant_id="t", embedding=[0.0, 1.0, 0.0], value="b", query="qb")
    cache.put(tenant_id="t", embedding=[0.0, 0.0, 1.0], value="c", query="qc")
    # "qa" (oldest) evicted; "qb"/"qc" remain
    assert cache.get(tenant_id="t", embedding=[1.0, 0.0, 0.0]).hit is False
    assert cache.get(tenant_id="t", embedding=[0.0, 1.0, 0.0]).hit is True


# ------------------------------ retriever wiring ------------------------------


class _FakeEmbedder(Embeddings):
    """Deterministic embeddings: query text → fixed vector via a small map."""

    _MAP: ClassVar[dict[str, list[float]]] = {
        "refund eta": [1.0, 0.0, 0.0],
        "when will my refund arrive": [0.999, 0.03, 0.0],  # ~synonym of the above
        "reset password": [0.0, 1.0, 0.0],  # unrelated
    }

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._MAP.get(text, [0.0, 0.0, 1.0])

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


class _FakeStore:
    def __init__(self) -> None:
        self.dense_calls = 0

    async def dense_search(
        self, *, query_embedding: list[float], tenant_id: str, k: int
    ) -> list[RetrievedDoc]:
        self.dense_calls += 1
        return [
            RetrievedDoc(id=1, title="Refunds", content="c", score=1.0, metadata={}),
        ]


def _retriever(cache: SemanticCache, store: _FakeStore) -> HybridRetriever:
    return HybridRetriever(
        embedder=_FakeEmbedder(),
        store=store,
        reranker=Reranker(model_name="x", enabled=False),  # passthrough
        profile="dense_only",
        cache=cache,
    )


@pytest.mark.asyncio
async def test_retriever_serves_synonym_from_cache() -> None:
    cache = SemanticCache(threshold=0.95)
    store = _FakeStore()
    retriever = _retriever(cache, store)

    docs1, trace1 = await retriever.search_with_trace(query="refund eta", tenant_id="demo", k=5)
    assert [d.id for d in docs1] == [1]
    assert trace1.cache_hit is False
    assert store.dense_calls == 1

    # a semantically-equivalent question reuses the cached result (no new DB hit)
    docs2, trace2 = await retriever.search_with_trace(
        query="when will my refund arrive", tenant_id="demo", k=5
    )
    assert [d.id for d in docs2] == [1]
    assert trace2.cache_hit is True
    assert store.dense_calls == 1  # unchanged → cache saved the round-trip


@pytest.mark.asyncio
async def test_retriever_cache_is_tenant_scoped_and_misses_unrelated() -> None:
    cache = SemanticCache(threshold=0.95)
    store = _FakeStore()
    retriever = _retriever(cache, store)

    await retriever.search_with_trace(query="refund eta", tenant_id="demo", k=5)
    assert store.dense_calls == 1

    # different tenant → cache must not serve demo's entry
    _, trace_other = await retriever.search_with_trace(
        query="refund eta", tenant_id="other", k=5
    )
    assert trace_other.cache_hit is False
    assert store.dense_calls == 2

    # unrelated query for demo → miss
    _, trace_unrelated = await retriever.search_with_trace(
        query="reset password", tenant_id="demo", k=5
    )
    assert trace_unrelated.cache_hit is False
    assert store.dense_calls == 3
