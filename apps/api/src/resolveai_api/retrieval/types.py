"""Shared retrieval data types, separated to avoid store/reranker/hybrid cycles."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievedDoc:
    """Unified retrieval result contract; `id` is the KB document ID used for citations and grounding."""

    id: int
    title: str
    content: str
    score: float
    metadata: dict[str, object]
    source: str = "hybrid"
    """Retrieval source: dense | lexical | fused | rerank, used for observability."""


@dataclass
class RetrievalTrace:
    """Observable snapshot of one retrieval sent to OTel, EvalGate, and agent tool calls."""

    query: str
    tenant_id: str
    profile: str
    dense_ids: list[int] = field(default_factory=list)
    lexical_ids: list[int] = field(default_factory=list)
    fused_ids: list[int] = field(default_factory=list)
    result_ids: list[int] = field(default_factory=list)
    latency_ms: float = 0.0
    reranked: bool = False
    cache_hit: bool = False
    """M13 semantic cache — True when the result was served from the cache."""

    def as_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "tenant_id": self.tenant_id,
            "profile": self.profile,
            "dense_ids": self.dense_ids,
            "lexical_ids": self.lexical_ids,
            "fused_ids": self.fused_ids,
            "result_ids": self.result_ids,
            "latency_ms": round(self.latency_ms, 2),
            "reranked": self.reranked,
            "cache_hit": self.cache_hit,
        }
