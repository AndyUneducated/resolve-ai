"""Retrieval quality metrics: pure functions shared by tests and evaluation.

M6 grounding and retrieval metrics (distinct from M5's security miss rate):
- Recall@k: whether any expected document appears in the top k
- MRR@k: reciprocal rank of the first expected document
- nDCG@k (M13): position-weighted ranking quality, a standard IR measure that
  distinguishes a hit at rank 1 from one at rank 5 better than recall or MRR
These metrics provide the retrieval dimension for M7 architecture ablation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(retrieved: Sequence[int], expected: Sequence[int], *, k: int) -> float:
    """Return 1.0 when top-k results intersect expected, otherwise 0.0.

    Return 0.0 when expected is empty because the case cannot be evaluated.
    """
    expected_set = set(expected)
    if not expected_set:
        return 0.0
    return 1.0 if expected_set & set(retrieved[:k]) else 0.0


def proportional_recall_at_k(
    retrieved: Sequence[int], expected: Sequence[int], *, k: int
) -> float:
    """Return expected documents found in top-k divided by all expected documents."""
    expected_set = set(expected)
    if not expected_set:
        return 0.0
    hit = expected_set & set(retrieved[:k])
    return len(hit) / len(expected_set)


def mrr_at_k(retrieved: Sequence[int], expected: Sequence[int], *, k: int) -> float:
    """Return reciprocal rank of the first hit for one case, or 0 when none match."""
    expected_set = set(expected)
    for rank, doc_id in enumerate(retrieved[:k], start=1):
        if doc_id in expected_set:
            return 1.0 / rank
    return 0.0


def dcg_at_k(
    retrieved: Sequence[int],
    expected: Sequence[int],
    *,
    k: int,
    grades: dict[int, float] | None = None,
) -> float:
    """Discounted Cumulative Gain@k.

    Binary relevance by default (1 if a retrieved doc is expected, else 0). Pass
    `grades` (doc_id → graded relevance) for graded gains. Standard log2 discount:
    ``sum(gain_i / log2(i + 1))`` for i = 1..k.
    """
    expected_set = set(expected)
    total = 0.0
    for rank, doc_id in enumerate(retrieved[:k], start=1):
        if grades is not None:
            gain = grades.get(doc_id, 0.0)
        else:
            gain = 1.0 if doc_id in expected_set else 0.0
        if gain:
            total += gain / math.log2(rank + 1)
    return total


def ndcg_at_k(
    retrieved: Sequence[int],
    expected: Sequence[int],
    *,
    k: int,
    grades: dict[int, float] | None = None,
) -> float:
    """Normalized DCG@k ∈ [0, 1] — DCG divided by the ideal ranking's DCG.

    Rewards putting relevant docs *higher*. Returns 0.0 when nothing is relevant
    (no ideal ranking to normalize against).
    """
    if grades is not None:
        ideal_gains = sorted(grades.values(), reverse=True)
    else:
        ideal_gains = [1.0] * len(set(expected))
    idcg = sum(
        gain / math.log2(rank + 1)
        for rank, gain in enumerate(ideal_gains[:k], start=1)
        if gain
    )
    if idcg == 0.0:
        return 0.0
    return dcg_at_k(retrieved, expected, k=k, grades=grades) / idcg


def aggregate(rows: Sequence[dict[str, float]]) -> dict[str, float]:
    """Average metric dictionaries across cases; return an empty dict for no rows."""
    if not rows:
        return {}
    keys = rows[0].keys()
    return {key: sum(row[key] for row in rows) / len(rows) for key in keys}
