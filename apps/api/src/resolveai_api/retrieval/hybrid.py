"""Hybrid 检索编排层 — BM25 (Postgres ts_rank_cd) + dense (pgvector) + RRF + rerank。

本模块只做"编排"：把 embedder / store / fusion / reranker 这些薄封装拼起来，
底层检索与打分全部交给 Postgres、pgvector、sentence-transformers。

可配置 profile：
- "hybrid"     双路召回 → RRF 融合 → reranker 精排（M6 目标）
- "dense_only" 仅向量召回 → reranker 精排（roadmap 的可砍降级路径 / M7 ablation）

统一接口供 Technical KB 与未来长期 memory 复用；查询强制带 tenant_id。
"""

from __future__ import annotations

import logging
import time

from langchain_core.embeddings import Embeddings

from resolveai_api.config import get_settings
from resolveai_api.guardrails.attribution import flag_enabled
from resolveai_api.retrieval.fusion import reciprocal_rank_fusion
from resolveai_api.retrieval.reranker import Reranker
from resolveai_api.retrieval.store import KbStore
from resolveai_api.retrieval.types import RetrievalTrace, RetrievedDoc

logger = logging.getLogger(__name__)

# Re-export so existing imports (`from ...retrieval.hybrid import RetrievedDoc`) keep working.
__all__ = ["HybridRetriever", "RetrievalTrace", "RetrievedDoc"]


def _tracer():
    """Return an OTel tracer if the SDK is importable; no-op otherwise.

    `start_as_current_span` is a cheap no-op when no provider is configured
    (tracing.py only installs one when OTEL endpoint is set), so this is safe
    to call unconditionally.
    """
    try:
        from opentelemetry import trace

        return trace.get_tracer("resolveai.retrieval")
    except Exception:  # pragma: no cover - OTel always present in deps
        return None


def _annotate_span(span: object, trace: RetrievalTrace) -> None:
    """Attach retrieval attributes to the active span (no-op if span is None)."""
    set_attr = getattr(span, "set_attribute", None)
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
        set_attr("retrieval.latency_ms", trace.latency_ms)
    except Exception:  # pragma: no cover - defensive
        pass


class HybridRetriever:
    """编排 BM25 + dense + RRF + reranker。组件可注入，便于测试与 memory 复用。"""

    def __init__(
        self,
        *,
        embedder: Embeddings | None = None,
        store: KbStore | None = None,
        reranker: Reranker | None = None,
        profile: str | None = None,
        candidate_k: int | None = None,
        rrf_k: int | None = None,
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
        """检索 top-k：每路取候选 → RRF 融合 → reranker 精排回 top-k。"""
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
        """同 `search`，但额外返回 `RetrievalTrace`（doc ids / 各路 / latency）。"""
        rrf_k = rrf_k or self._rrf_k
        trace = RetrievalTrace(query=query, tenant_id=tenant_id, profile=self._profile)
        tracer = _tracer()
        start = time.perf_counter()

        from contextlib import nullcontext

        span_cm = (
            tracer.start_as_current_span("retrieval.search")
            if tracer is not None
            else nullcontext()
        )
        with span_cm as span:
            try:
                docs = await self._run(
                    query=query, tenant_id=tenant_id, k=k, rrf_k=rrf_k, trace=trace
                )
            finally:
                trace.latency_ms = (time.perf_counter() - start) * 1000.0
                _annotate_span(span, trace)
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
    ) -> list[RetrievedDoc]:
        # ---- dense path (always on) ----
        embedding = await self._get_embedder().aembed_query(query)
        dense = await self._store.dense_search(
            query_embedding=embedding, tenant_id=tenant_id, k=self._candidate_k
        )
        trace.dense_ids = [d.id for d in dense]

        if self._profile == "dense_only":
            candidates = dense
            trace.fused_ids = trace.dense_ids
        else:
            # ---- lexical path ----
            lexical = await self._store.lexical_search(
                query=query, tenant_id=tenant_id, k=self._candidate_k
            )
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
