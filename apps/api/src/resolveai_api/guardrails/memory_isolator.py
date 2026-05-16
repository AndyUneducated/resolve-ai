"""Layer 4 · 记忆侧 — per-tenant + per-customer state 命名空间隔离。

不是 DB 层 RBAC，而是 Agent runtime 层 — 防"上一个用户的 PII 漏进下一个用户的 LLM context"。
多租户 SaaS 真实事故。
"""

from __future__ import annotations


class MemoryIsolator:
    """所有 state checkpointing key 走这里 — 强制 (tenant_id, customer_id) 命名空间。"""

    @staticmethod
    def namespace(tenant_id: str, customer_id: str, thread_id: str) -> str:
        if not tenant_id or not customer_id:
            raise ValueError("tenant_id / customer_id 必须显式提供")
        return f"{tenant_id}::{customer_id}::{thread_id or 'default'}"

    @staticmethod
    def assert_match(*, ns: str, tenant_id: str, customer_id: str) -> None:
        """从 checkpoint 取出 state 时，强制校验命名空间。"""
        prefix = f"{tenant_id}::{customer_id}::"
        if not ns.startswith(prefix):
            raise PermissionError(
                f"Cross-tenant/customer state access blocked: {ns} vs {prefix}*"
            )
