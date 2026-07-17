"""Retrieval metrics — pure functions, hermetic."""

from __future__ import annotations

import sys
from pathlib import Path

from resolveai_api.retrieval.metrics import (
    aggregate,
    dcg_at_k,
    mrr_at_k,
    ndcg_at_k,
    proportional_recall_at_k,
    recall_at_k,
)

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def test_recall_at_k_hit_and_miss() -> None:
    assert recall_at_k([3, 1, 2], [1], k=5) == 1.0
    assert recall_at_k([3, 4, 5], [1], k=5) == 0.0
    # outside the k window → miss
    assert recall_at_k([3, 4, 1], [1], k=2) == 0.0


def test_proportional_recall() -> None:
    assert proportional_recall_at_k([1, 2, 9], [1, 2], k=5) == 1.0
    assert proportional_recall_at_k([1, 9], [1, 2], k=5) == 0.5
    assert proportional_recall_at_k([9], [1, 2], k=5) == 0.0


def test_mrr_at_k() -> None:
    assert mrr_at_k([5, 1, 2], [1], k=5) == 0.5
    assert mrr_at_k([1, 2], [1], k=5) == 1.0
    assert mrr_at_k([7, 8], [1], k=5) == 0.0


def test_empty_expected_returns_zero() -> None:
    assert recall_at_k([1, 2], [], k=5) == 0.0
    assert mrr_at_k([1, 2], [], k=5) == 0.0


def test_aggregate_averages_rows() -> None:
    rows = [{"recall@5": 1.0, "mrr@5": 1.0}, {"recall@5": 0.0, "mrr@5": 0.0}]
    agg = aggregate(rows)
    assert agg["recall@5"] == 0.5
    assert agg["mrr@5"] == 0.5
    assert aggregate([]) == {}


# ------------------------------ nDCG (M13) ------------------------------


def test_ndcg_perfect_ranking_is_one() -> None:
    # both relevant docs at the very top → ideal
    assert ndcg_at_k([1, 2, 9, 8], [1, 2], k=4) == 1.0


def test_ndcg_rewards_higher_placement() -> None:
    # single relevant doc: rank 1 must score strictly higher than rank 3
    top = ndcg_at_k([1, 8, 9], [1], k=3)
    lower = ndcg_at_k([8, 9, 1], [1], k=3)
    assert top == 1.0
    assert 0.0 < lower < top


def test_ndcg_zero_when_nothing_relevant() -> None:
    assert ndcg_at_k([7, 8, 9], [1], k=3) == 0.0
    assert ndcg_at_k([1, 2], [], k=3) == 0.0  # no ideal ranking to normalize


def test_ndcg_supports_graded_relevance() -> None:
    # a highly-relevant doc (grade 3) ranked first beats putting a grade-1 first
    grades = {1: 3.0, 2: 1.0}
    good = ndcg_at_k([1, 2], [1, 2], k=2, grades=grades)
    worse = ndcg_at_k([2, 1], [1, 2], k=2, grades=grades)
    assert good == 1.0
    assert worse < good


def test_dcg_uses_log2_discount() -> None:
    # gain 1 at rank 1 = 1/log2(2) = 1.0; rank 2 adds 1/log2(3)
    import math

    assert dcg_at_k([1], [1], k=1) == 1.0
    assert dcg_at_k([9, 1], [1], k=2) == 1.0 / math.log2(3)


def test_render_quality_markdown_table() -> None:
    import eval_retrieval

    md = eval_retrieval.render_quality_markdown(
        [
            {"profile": "hybrid", "reranker_status": "active",
             "aggregate": {"recall@5": 0.9, "prop_recall@5": 0.8, "mrr@5": 0.7, "ndcg@5": 0.85}},
            {"profile": "dense_only", "reranker_status": "disabled",
             "aggregate": {"recall@5": 0.7, "prop_recall@5": 0.6, "mrr@5": 0.5, "ndcg@5": 0.6}},
        ],
        k=5,
    )
    assert "ndcg@5" in md
    assert "| hybrid | active |" in md
    assert "0.850" in md and "0.600" in md


def test_retrieval_regression_gate() -> None:
    import eval_retrieval

    baseline = {"ndcg@5": 0.80, "recall@5": 0.90, "prop_recall@5": 0.85}
    # within the -5% floor → no violation
    ok = eval_retrieval.check_retrieval_regression(
        {"ndcg@5": 0.79, "recall@5": 0.90, "prop_recall@5": 0.85}, baseline, k=5
    )
    assert ok == []
    # nDCG collapses → violation
    bad = eval_retrieval.check_retrieval_regression(
        {"ndcg@5": 0.60, "recall@5": 0.90, "prop_recall@5": 0.85}, baseline, k=5
    )
    assert len(bad) == 1 and "ndcg@5" in bad[0]
