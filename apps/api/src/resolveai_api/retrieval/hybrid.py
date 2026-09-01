"""Hybrid retrieval orchestration: BM25, dense retrieval, RRF, and reranking.

This module only orchestrates thin wrappers for the embedder, store, fusion,
and reranker. Postgres, pgvector, and sentence-transformers perform the
underlying retrieval and scoring.

Configurable profiles:
- "hybrid": dual-path retrieval → RRF fusion → reranking (M6 target)
- "dense_only": vector retrieval → reranking (roadmap fallback / M7 ablation)

The unified interface is shared by the Technical KB and future long-term
memory; every query requires a tenant_id.
"""

from __future__ import annotations

import asyncio
import logging
import time

from langchain_core.embeddings import Embeddings

from resolveai_api.config import get_settings
from resolveai_api.guardrails.attribution import flag_enabled
from resolveai_api.observability.tracing import get_tracer, span
from resolveai_api.retrieval.fusion import reciprocal_rank_fusion
from resolveai_api.retrieval.reranker import Reranker
from resolveai_api.retrieval.semantic_cache import SemanticCache
from resolveai_api.retrieval.store import KbStore
from resolveai_api.retrieval.types import RetrievalTrace, RetrievedDoc

logger = logging.getLogger(__name__)

# Re-export so existing imports (`from ...retrieval.hybrid import RetrievedDoc`) keep working.
__all__ = ["HybridRetriever", "RetrievalTrace", "RetrievedDoc"]

# Shared tracer helper (no-op unless an OTel provider is installed) — same one the
# executor / supervisor use, instead of a bespoke local bootstrap.
_TRACER = get_tracer("resolveai.retrieval")


def _annotate_span(otel_span: object, trace: RetrievalTrace) -> None:
    """Attach retrieval attributes to the active span (no-op if span is None)."""
    set_attr = getattr(otel_span, "set_attribute", None)
    if not callable(set_attr):
        return
    try:
        set_attr("retrieval.profile", trace.profile)
        set_attr("retrieval.tenant_id", trace.tenant_id)
        set_attr("retrieval.query_len", len(trace.query))
        set_attr("retrieval.dense_count", len(trace.dense_ids))
        set_attr("retrieval.lexical_count", len(trace.lexical_ids))
        set_attr("retrieval.result_ids", str(trace.result_ids))
        set_attr("retrieval.reranked", trace.reranked)
        set_attr("retrieval.cache_hit", trace.cache_hit)
        set_attr("retrieval.latency_ms", trace.latency_ms)
    except Exception:  # pragma: no cover - defensive
        pass


class HybridRetriever:
    """Orchestrate BM25, dense retrieval, RRF, and reranking with injectable components."""

    def __init__(
        self,
        *,
        embedder: Embeddings | None = None,
        store: KbStore | None = None,
        reranker: Reranker | None = None,
        profile: str | None = None,
        candidate_k: int | None = None,
        rrf_k: int | None = None,
        cache: SemanticCache | None = None,
    ) -> None:
        settings = get_settings()
        self._embedder = embedder
        self._store = store or KbStore()
        self._reranker = reranker or Reranker(
            model_name=settings.reranker_model,
            enabled=flag_enabled(settings.reranker_enabled),
        )
        self._profile = profile or settings.retrieval_profile
        self._candidate_k = candidate_k or settings.retrieval_candidate_k
        self._rrf_k = rrf_k or settings.retrieval_rrf_k
        # Semantic cache (M13): injected for tests, else the process-wide singleton
        # when SEMANTIC_CACHE_ENABLED=on. None → caching disabled (default path).
        if cache is not None:
            self._cache: SemanticCache | None = cache
        elif flag_enabled(getattr(settings, "semantic_cache_enabled", "off")):
            from resolveai_api.retrieval.semantic_cache import get_semantic_cache

            self._cache = get_semantic_cache()
        else:
            self._cache = None

    @property
    def reranker_status(self) -> str:
        """Effective reranker state: 'disabled' | 'active' | 'fallback(rrf)'."""
        return self._reranker.availability()

    def _get_embedder(self) -> Embeddings:
        if self._embedder is None:
            from resolveai_api.retrieval.embedder import get_embedder

            self._embedder = get_embedder()
        return self._embedder

    async def search(
        self,
        *,
        query: str,
        tenant_id: str,
        k: int = 10,
        rrf_k: int | None = None,
    ) -> list[RetrievedDoc]:
        """Retrieve top-k via per-path candidates, RRF fusion, and reranking."""
        docs, _ = await self.search_with_trace(
            query=query, tenant_id=tenant_id, k=k, rrf_k=rrf_k
        )
        return docs

    async def search_with_trace(
        self,
        *,
        query: str,
        tenant_id: str,
        k: int = 10,
        rrf_k: int | None = None,
    ) -> tuple[list[RetrievedDoc], RetrievalTrace]:
        """Like `search`, but also return a `RetrievalTrace` with IDs, paths, and latency."""
        rrf_k = rrf_k or self._rrf_k
        trace = RetrievalTrace(query=query, tenant_id=tenant_id, profile=self._profile)
        start = time.perf_counter()

        with span(_TRACER, "retrieval.search") as retrieval_span:
            try:
                # Semantic cache (M13): embed once up-front, reuse the vector for
                # both the cache lookup and (on miss) the dense round-trip.
                query_embedding: list[float] | None = None
                if self._cache is not None:
                    query_embedding = await self._get_embedder().aembed_query(query)
                    lookup = self._cache.get(
                        tenant_id=tenant_id, embedding=query_embedding
                    )
                    if lookup.hit:
                        docs = list(lookup.value)[:k]
                        trace.cache_hit = True
                        trace.result_ids = [d.id for d in docs]
                        return docs, trace

                docs = await self._run(
                    query=query,
                    tenant_id=tenant_id,
                    k=k,
                    rrf_k=rrf_k,
                    trace=trace,
                    query_embedding=query_embedding,
                )
                if self._cache is not None and query_embedding is not None:
                    self._cache.put(
                        tenant_id=tenant_id,
                        embedding=query_embedding,
                        value=docs,
                        query=query,
                    )
            finally:
                trace.latency_ms = (time.perf_counter() - start) * 1000.0
                _annotate_span(retrieval_span, trace)
        logger.info("retrieval_done %s", trace.as_dict())
        return docs, trace

    async def _run(
        self,
        *,
        query: str,
        tenant_id: str,
        k: int,
        rrf_k: int,
        trace: RetrievalTrace,
        query_embedding: list[float] | None = None,
    ) -> list[RetrievedDoc]:
        async def _dense() -> list[RetrievedDoc]:
            embedding = query_embedding
            if embedding is None:
                embedding = await self._get_embedder().aembed_query(query)
            return await self._store.dense_search(
                query_embedding=embedding, tenant_id=tenant_id, k=self._candidate_k
            )

        if self._profile == "dense_only":
            dense = await _dense()
            trace.dense_ids = [d.id for d in dense]
            candidates = dense
            trace.fused_ids = trace.dense_ids
        else:
            # Dense (embed + pgvector) and lexical (ts_rank_cd) are independent DB
            # round-trips; run them concurrently to cut end-to-end latency.
            dense, lexical = await asyncio.gather(
                _dense(),
                self._store.lexical_search(
                    query=query, tenant_id=tenant_id, k=self._candidate_k
                ),
            )
            trace.dense_ids = [d.id for d in dense]
            trace.lexical_ids = [d.id for d in lexical]
            candidates = self._fuse(dense, lexical, rrf_k=rrf_k)
            trace.fused_ids = [d.id for d in candidates]

        # ---- rerank (reranker degrades to fusion order if unavailable) ----
        result = await self._reranker.rerank(query=query, docs=candidates, top_k=k)
        trace.reranked = any(d.source == "rerank" for d in result)
        trace.result_ids = [d.id for d in result]
        return result

    def _fuse(
        self, dense: list[RetrievedDoc], lexical: list[RetrievedDoc], *, rrf_k: int
    ) -> list[RetrievedDoc]:
        by_id: dict[int, RetrievedDoc] = {}
        for doc in (*dense, *lexical):
            by_id.setdefault(doc.id, doc)
        fused_scores = reciprocal_rank_fusion(
            [[d.id for d in dense], [d.id for d in lexical]], k=rrf_k
        )
        ordered = sorted(
            fused_scores.items(), key=lambda kv: (-kv[1], kv[0])
        )
        out: list[RetrievedDoc] = []
        for doc_id, score in ordered[: self._candidate_k]:
            base = by_id[doc_id]
            out.append(
                RetrievedDoc(
                    id=base.id,
                    title=base.title,
                    content=base.content,
                    score=score,
                    metadata=base.metadata,
                    source="fused",
                )
            )
        return out
