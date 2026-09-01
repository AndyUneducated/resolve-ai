"""bge-reranker-v2-m3 reranking layer using sentence-transformers CrossEncoder.

Design principles:
- Lazily import heavy dependencies (torch and sentence-transformers) on the
  first reranking request.
- On any failure (missing package, model download, or inference), fall back to
  input order (the RRF order) without changing the interface contract. This
  keeps dense and hybrid retrieval usable without the reranker extra.
- Run the synchronous, CPU-intensive CrossEncoder.predict in a thread pool to
  avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging

from resolveai_api.retrieval.types import RetrievedDoc

logger = logging.getLogger(__name__)


class Reranker:
    """Cross-encoder reranker with lazy model load and RRF-order fallback."""

    def __init__(self, *, model_name: str, enabled: bool = True) -> None:
        self._model_name = model_name
        self._enabled = enabled
        self._model: object | None = None
        self._unavailable = False

    def _load(self) -> object | None:
        if self._model is not None or self._unavailable:
            return self._model
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name)
        except Exception:
            # Missing extra / no network / no torch → degrade to RRF order.
            logger.warning(
                "reranker_unavailable model=%s; falling back to fusion order",
                self._model_name,
            )
            self._unavailable = True
        return self._model

    def availability(self) -> str:
        """Report effective status: 'disabled' | 'active' | 'fallback(rrf)'.

        Triggers a (cached) load attempt so callers can surface the *real* state
        instead of assuming the configured cross-encoder is in effect. Lets eval /
        startup make silent degradation (missing `--extra rerank`) visible.
        """
        if not self._enabled:
            return "disabled"
        return "active" if self._load() is not None else "fallback(rrf)"

    def _score(self, query: str, docs: list[RetrievedDoc]) -> list[float]:
        model = self._model
        assert model is not None  # guarded by caller
        pairs = [(query, doc.content) for doc in docs]
        scores = model.predict(pairs)  # type: ignore[attr-defined]
        return [float(s) for s in scores]

    async def rerank(
        self, *, query: str, docs: list[RetrievedDoc], top_k: int = 5
    ) -> list[RetrievedDoc]:
        if not self._enabled or not docs:
            return docs[:top_k]

        if self._load() is None:
            return docs[:top_k]

        try:
            scores = await asyncio.to_thread(self._score, query, docs)
        except Exception:
            logger.exception("reranker_inference_failed; falling back to fusion order")
            return docs[:top_k]

        ranked = sorted(
            (
                RetrievedDoc(
                    id=doc.id,
                    title=doc.title,
                    content=doc.content,
                    score=score,
                    metadata=doc.metadata,
                    source="rerank",
                )
                for doc, score in zip(docs, scores, strict=True)
            ),
            key=lambda d: d.score,
            reverse=True,
        )
        return ranked[:top_k]
