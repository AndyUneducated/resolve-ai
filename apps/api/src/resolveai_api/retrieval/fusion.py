"""Reciprocal Rank Fusion (RRF) — 纯函数，无 DB / 无外部依赖，便于单测。

行业标准做法（Cormack et al. 2009）：对每一路检索结果按名次累加 1/(k+rank)，
名次从 1 起。k 越大，越淡化排名差异；默认 60。

只做"算法本体"的薄实现——没有可直接复用的轻量库值得为 ~10 行 RRF 引入。
"""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[int]], *, k: int = 60
) -> dict[int, float]:
    """Fuse multiple ranked doc-id lists into a single {doc_id: fused_score} map.

    Args:
        ranked_lists: each inner sequence is doc ids ordered best→worst.
        k: RRF smoothing constant.

    Returns:
        Mapping from doc id to fused score (higher = better).
    """
    scores: dict[int, float] = {}
    for ranking in ranked_lists:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def fuse_ranked_ids(
    ranked_lists: Sequence[Sequence[int]], *, k: int = 60
) -> list[int]:
    """Convenience: return doc ids sorted by fused RRF score (descending).

    Ties break by smallest doc id for deterministic output.
    """
    scores = reciprocal_rank_fusion(ranked_lists, k=k)
    return [doc_id for doc_id, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
