"""Semantic cache (M13) — embedding-nearest-neighbour reuse for KB retrieval.

Semantically-equivalent questions ("how long do refunds take?" ≈ "when will my
refund arrive?") should not re-run embed → dense+lexical → rerank every time. This
caches a query's *result* keyed by its embedding: a new query whose cosine
similarity to a cached entry clears `threshold` reuses that result.

Design (honest about the trade-off):
- **In-process, cosine-NN over recent entries** — small, dependency-free, and
  deterministically testable. The interface is storage-agnostic, so the durable
  production variant (pgvector-backed, shared across replicas — same idea as
  GPTCache) is a local swap; called out in the plan doc, not built here.
- **Tenant-isolated by construction**: entries are bucketed by `tenant_id` and a
  lookup only ever scans its own tenant — no cross-tenant answer bleed (aligns
  with M9 RLS).
- **TTL + LRU-ish cap**: stale entries expire; the bucket evicts oldest first.
- Safety on hit is preserved by the *caller*: cached retrieval results still flow
  through the normal answer-generation + Layer-3 output guardrail re-scan.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity ∈ [-1, 1]; 0.0 for a zero/degenerate vector."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


@dataclass
class _Entry:
    embedding: tuple[float, ...]
    value: Any
    created_at: float
    query: str


@dataclass
class CacheLookup:
    hit: bool
    value: Any = None
    similarity: float = 0.0


class SemanticCache:
    """Per-process, tenant-isolated semantic cache with TTL + capacity cap."""

    def __init__(
        self,
        *,
        threshold: float = 0.95,
        ttl_s: float = 3600.0,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._ttl_s = ttl_s
        self._max_entries = max(1, max_entries)
        self._clock = clock
        # tenant_id -> insertion-ordered {query -> _Entry}
        self._buckets: dict[str, OrderedDict[str, _Entry]] = {}
        self.hits = 0
        self.misses = 0

    def _expired(self, entry: _Entry, now: float) -> bool:
        return self._ttl_s > 0 and (now - entry.created_at) > self._ttl_s

    def get(self, *, tenant_id: str, embedding: Sequence[float]) -> CacheLookup:
        """Nearest cached entry for `tenant_id`; hit iff cosine ≥ threshold.

        Records hit/miss to the process metrics as a side effect.
        """
        from resolveai_api.observability import metrics as prom

        now = self._clock()
        bucket = self._buckets.get(tenant_id)
        best: _Entry | None = None
        best_sim = -1.0
        if bucket:
            for entry in list(bucket.values()):
                if self._expired(entry, now):
                    bucket.pop(entry.query, None)
                    continue
                sim = cosine_similarity(embedding, entry.embedding)
                if sim > best_sim:
                    best_sim = sim
                    best = entry
        if best is not None and best_sim >= self._threshold:
            self.hits += 1
            prom.record_cache_hit()
            # refresh recency
            bucket = self._buckets[tenant_id]
            bucket.move_to_end(best.query)
            return CacheLookup(hit=True, value=best.value, similarity=best_sim)
        self.misses += 1
        prom.record_cache_miss()
        return CacheLookup(hit=False, similarity=max(best_sim, 0.0))

    def put(
        self, *, tenant_id: str, embedding: Sequence[float], value: Any, query: str = ""
    ) -> None:
        bucket = self._buckets.setdefault(tenant_id, OrderedDict())
        entry = _Entry(
            embedding=tuple(float(x) for x in embedding),
            value=value,
            created_at=self._clock(),
            query=query,
        )
        bucket[query] = entry
        bucket.move_to_end(query)
        while len(bucket) > self._max_entries:
            bucket.popitem(last=False)  # evict oldest

    def clear(self) -> None:
        self._buckets.clear()
        self.hits = 0
        self.misses = 0


_CACHE: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache:
    """Process-wide cache built from settings (created on first use)."""
    global _CACHE
    if _CACHE is None:
        from resolveai_api.config import get_settings

        settings = get_settings()
        _CACHE = SemanticCache(
            threshold=settings.semantic_cache_threshold,
            ttl_s=settings.semantic_cache_ttl_s,
            max_entries=settings.semantic_cache_max_entries,
        )
    return _CACHE


def reset_semantic_cache() -> None:
    """Test hook: drop the process-wide cache singleton."""
    global _CACHE
    _CACHE = None
