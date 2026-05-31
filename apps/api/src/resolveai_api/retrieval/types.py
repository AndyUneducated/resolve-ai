"""共享检索数据类型 — 单独成模块，避免 store/reranker/hybrid 循环依赖。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RetrievedDoc:
    """统一检索返回契约。`id` 即 KB doc id，Agent 引用与 grounding 校验都基于它。"""

    id: int
    title: str
    content: str
    score: float
    metadata: dict[str, object]
    source: str = "hybrid"
    """召回来源：dense | lexical | fused | rerank — 用于可观测性。"""


@dataclass
class RetrievalTrace:
    """单次检索的可观测快照（送 OTel span / EvalGate / Agent tool_calls）。"""

    query: str
    tenant_id: str
    profile: str
    dense_ids: list[int] = field(default_factory=list)
    lexical_ids: list[int] = field(default_factory=list)
    fused_ids: list[int] = field(default_factory=list)
    result_ids: list[int] = field(default_factory=list)
    latency_ms: float = 0.0
    reranked: bool = False

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
        }
