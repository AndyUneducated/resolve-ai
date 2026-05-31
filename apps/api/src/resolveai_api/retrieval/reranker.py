"""bge-reranker-v2-m3 精排层 — 调库优先，复用 sentence-transformers CrossEncoder。

设计要点（与 plan 的"调库优先 + 可降级"一致）：
- 重依赖（torch / sentence-transformers）lazy import，仅首次 rerank 时加载。
- 任意失败（未安装、模型拉取失败、推理异常）→ 回退到入参顺序（即 RRF 排序），
  保持接口契约不变。这样 `pip install` 不带 reranker extra 也能跑 dense/hybrid。
- CrossEncoder.predict 是 CPU 密集的同步调用，丢到线程池避免阻塞事件循环。
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
