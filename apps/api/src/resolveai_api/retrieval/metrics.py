"""检索质量指标 — 纯函数，供 `scripts/eval_retrieval.py` 与单测复用。

M6 定义的 grounding / 召回指标（区别于 M5 的安全漏过率）：
- Recall@k：top-k 中命中任一 expected doc 的比例
- MRR@k：第一个命中 expected doc 的倒数名次
- nDCG@k（M13）：位置加权的排序质量（IR 标准度量），比 recall/MRR 更能区分
  "命中在第 1 位 vs 第 5 位"，是量化 rerank 收益的关键指标
这些是 M7 architecture ablation 的检索维度输入。
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(retrieved: Sequence[int], expected: Sequence[int], *, k: int) -> float:
    """命中率：top-k 检索结果与 expected 是否有交集（1.0 / 0.0）。

    expected 为空时返回 0.0（无法评估）。
    """
    expected_set = set(expected)
    if not expected_set:
        return 0.0
    return 1.0 if expected_set & set(retrieved[:k]) else 0.0


def proportional_recall_at_k(
    retrieved: Sequence[int], expected: Sequence[int], *, k: int
) -> float:
    """比例召回：top-k 命中的 expected 文档数 / expected 总数。"""
    expected_set = set(expected)
    if not expected_set:
        return 0.0
    hit = expected_set & set(retrieved[:k])
    return len(hit) / len(expected_set)


def mrr_at_k(retrieved: Sequence[int], expected: Sequence[int], *, k: int) -> float:
    """Mean Reciprocal Rank 单条：第一个命中的倒数名次，未命中为 0。"""
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
    """对每条 case 的指标 dict 求平均。空输入返回空 dict。"""
    if not rows:
        return {}
    keys = rows[0].keys()
    return {key: sum(row[key] for row in rows) / len(rows) for key in keys}
