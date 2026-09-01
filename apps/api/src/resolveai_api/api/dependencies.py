"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from resolveai_api.agents.supervisor import SupervisorGraph
from resolveai_api.config import get_settings


def get_supervisor(request: Request) -> SupervisorGraph:
    """Return the SupervisorGraph wired in `main.lifespan`."""
    supervisor: SupervisorGraph | None = getattr(request.app.state, "supervisor", None)
    if supervisor is None:  # pragma: no cover — guards misconfigured tests
        raise RuntimeError("SupervisorGraph not initialised; lifespan did not run.")
    return supervisor


def get_tenant_id(request: Request) -> str:
    """Resolve the tenant id for the current request.

    This project does not implement or plan to implement authentication (see
    docs/milestone-9-plan.md section 4), so this is a thin dependency: it reads
    tenant_id from the request context and falls back to `DEFAULT_TENANT_ID`.
    It passes tenant identity downstream to `SET LOCAL app.tenant_id`, making
    RLS defense in depth against application bugs rather than pretending to
    block malicious clients (without authentication, clients can still supply
    their own tenant).

    Resolution order: `X-Tenant-Id` header > `tenant` query parameter >
    `DEFAULT_TENANT_ID`. The endpoint still forwards `ChatRequest.tenant_id`
    from the request body through the existing end-to-end path.
    """
    header_tenant = request.headers.get("x-tenant-id")
    query_tenant = request.query_params.get("tenant")
    return header_tenant or query_tenant or get_settings().default_tenant_id
