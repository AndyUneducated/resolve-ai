"""Reciprocal Rank Fusion (RRF), implemented as an easily tested pure function.

The standard approach (Cormack et al., 2009) sums 1/(k+rank) across retrieval
paths, with ranks starting at 1. Larger k values reduce rank differences; the
default is 60.

This is a thin implementation of the algorithm itself; no reusable lightweight
library warrants an added dependency for approximately ten lines of RRF.
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
