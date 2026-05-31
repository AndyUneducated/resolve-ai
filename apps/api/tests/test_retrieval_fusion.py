"""RRF fusion — pure function, hermetic (no DB / no embeddings)."""

from __future__ import annotations

from resolveai_api.retrieval.fusion import fuse_ranked_ids, reciprocal_rank_fusion


def test_rrf_rewards_docs_ranked_high_in_both_lists() -> None:
    dense = [1, 2, 3]
    lexical = [2, 1, 4]
    scores = reciprocal_rank_fusion([dense, lexical], k=60)
    # Doc 2 is rank-2 + rank-1; doc 1 is rank-1 + rank-2 → near-tie above 3 and 4.
    assert scores[1] > scores[3]
    assert scores[2] > scores[4]


def test_rrf_doc_in_both_beats_doc_in_one() -> None:
    dense = [1, 5]
    lexical = [1, 9]
    scores = reciprocal_rank_fusion([dense, lexical], k=60)
    assert scores[1] > scores[5]
    assert scores[1] > scores[9]


def test_fuse_ranked_ids_is_sorted_and_deterministic() -> None:
    fused = fuse_ranked_ids([[1, 2, 3], [2, 1, 4]], k=60)
    assert fused[0] in (1, 2)
    assert set(fused) == {1, 2, 3, 4}
    # deterministic tie-break by id
    assert fuse_ranked_ids([[1], [2]], k=60) == [1, 2]


def test_rrf_empty_lists() -> None:
    assert reciprocal_rank_fusion([], k=60) == {}
    assert reciprocal_rank_fusion([[], []], k=60) == {}
