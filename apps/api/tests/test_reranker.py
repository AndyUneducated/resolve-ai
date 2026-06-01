"""Reranker availability reporting — pure, hermetic (no torch / no model load).

Covers the degradation-visibility fix: `availability()` must report the *effective*
state so eval / startup can surface a silent fallback (missing `--extra rerank`)
instead of pretending the cross-encoder is in effect.
"""

from __future__ import annotations

from resolveai_api.retrieval.hybrid import HybridRetriever
from resolveai_api.retrieval.reranker import Reranker


def test_availability_disabled_skips_load() -> None:
    r = Reranker(model_name="BAAI/bge-reranker-v2-m3", enabled=False)
    assert r.availability() == "disabled"
    # Disabled must not even attempt a model import.
    assert r._model is None
    assert r._unavailable is False


def test_availability_fallback_when_load_failed() -> None:
    r = Reranker(model_name="does-not-exist/model", enabled=True)
    # Simulate a prior failed load (missing extra / no torch) without importing.
    r._unavailable = True
    assert r.availability() == "fallback(rrf)"


def test_availability_active_when_model_present() -> None:
    r = Reranker(model_name="BAAI/bge-reranker-v2-m3", enabled=True)
    # Inject a sentinel so _load() short-circuits to the cached model.
    r._model = object()
    assert r.availability() == "active"


def test_hybrid_reranker_status_delegates_to_injected_reranker() -> None:
    disabled = HybridRetriever(reranker=Reranker(model_name="x", enabled=False))
    assert disabled.reranker_status == "disabled"
