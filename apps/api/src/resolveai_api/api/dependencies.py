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

    本项目不做也不计划做鉴权（见 docs/milestone-9-plan.md §4），所以这里是个 thin
    dependency：从请求上下文取 tenant_id，缺省回退 `DEFAULT_TENANT_ID`。它把租户身份
    喂给下游 `SET LOCAL app.tenant_id`，让 RLS 成为「防应用 bug」的 defense-in-depth，
    而非假装拦住恶意客户端（无 auth 时客户端仍可自报 tenant）。

    取值优先级：`X-Tenant-Id` header > `tenant` query 参数 > `DEFAULT_TENANT_ID`。
    请求体里的 `ChatRequest.tenant_id`（已有全链路）仍由 endpoint 自行透传，二者一致。
    """
    header_tenant = request.headers.get("x-tenant-id")
    query_tenant = request.query_params.get("tenant")
    return header_tenant or query_tenant or get_settings().default_tenant_id
