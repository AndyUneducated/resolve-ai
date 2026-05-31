"""Retrieval metrics — pure functions, hermetic."""

from __future__ import annotations

from resolveai_api.retrieval.metrics import (
    aggregate,
    mrr_at_k,
    proportional_recall_at_k,
    recall_at_k,
)


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
