"""Layer 4 · Memory side — per-tenant and per-customer state namespace isolation.

This is an agent-runtime layer rather than database RBAC. It prevents one
user's PII from leaking into another user's LLM context, a real risk in
multi-tenant SaaS systems.
"""

from __future__ import annotations


class MemoryIsolator:
    """Route all state checkpoint keys here to enforce the tenant/customer namespace."""

    @staticmethod
    def namespace(tenant_id: str, customer_id: str, thread_id: str) -> str:
        if not tenant_id or not customer_id:
            raise ValueError("tenant_id and customer_id must be provided explicitly")
        return f"{tenant_id}::{customer_id}::{thread_id or 'default'}"

    @staticmethod
    def assert_match(*, ns: str, tenant_id: str, customer_id: str) -> None:
        """Validate the namespace whenever state is loaded from a checkpoint."""
        prefix = f"{tenant_id}::{customer_id}::"
        if not ns.startswith(prefix):
            raise PermissionError(
                f"Cross-tenant/customer state access blocked: {ns} vs {prefix}*"
            )
