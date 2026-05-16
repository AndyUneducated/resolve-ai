"""Memory — 双层（短期 conversation buffer + 长期 customer history RAG）。

决策 4 · Layer 4 的实现宿主：
- per-tenant + per-customer state 命名空间隔离
- 进程复用时强制 reset short-term buffer
- long-term history RAG 严格按 (tenant_id, customer_id) 过滤
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConversationBuffer:
    """短期 — 仅本轮对话。"""

    messages: list[dict[str, str]] = field(default_factory=list)


class Memory:
    """TODO:
    - short-term: in-memory dict keyed by (tenant_id, customer_id, thread_id)
    - long-term: pgvector 存历史 ticket 摘要 + 客户偏好；查询永远带 tenant + customer 过滤
    """

    def __init__(self) -> None:
        self._short_term: dict[tuple[str, str, str], ConversationBuffer] = {}

    def short_term_key(self, tenant_id: str, customer_id: str, thread_id: str) -> tuple[str, str, str]:
        return (tenant_id, customer_id, thread_id)

    def get_short_term(self, tenant_id: str, customer_id: str, thread_id: str) -> ConversationBuffer:
        key = self.short_term_key(tenant_id, customer_id, thread_id)
        return self._short_term.setdefault(key, ConversationBuffer())

    def reset_short_term(self, tenant_id: str, customer_id: str, thread_id: str) -> None:
        """决策 4 · Layer 4 — Agent 进程复用时强制 reset。"""
        self._short_term.pop(self.short_term_key(tenant_id, customer_id, thread_id), None)

    async def search_long_term(
        self, *, tenant_id: str, customer_id: str, query: str, k: int = 5
    ) -> list[dict[str, object]]:
        """长期 customer history RAG — 严格按 (tenant_id, customer_id) 过滤。"""
        # TODO: 接 retrieval.hybrid
        return []
