"""Chat endpoint that streams agent responses and tool traces over SSE."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from resolveai_api.agents.supervisor import SupervisorGraph
from resolveai_api.api.dependencies import get_supervisor, get_tenant_id

router = APIRouter(tags=["chat"])

SupervisorDep = Annotated[SupervisorGraph, Depends(get_supervisor)]
TenantDep = Annotated[str, Depends(get_tenant_id)]


class ChatRequest(BaseModel):
    message: str = Field(..., description="Customer input")
    customer_id: str = Field(..., description="Customer ID — Decision 4 · Layer 4 memory isolation key")
    thread_id: str | None = Field(
        default=None,
        description="Session ID; a new session is created when omitted. Used for interruption recovery and cross-shift handoffs.",
    )
    tenant_id: str | None = Field(default=None, description="Tenant ID (multi-tenant isolation)")


@router.post("/chat")
async def chat(
    req: ChatRequest, supervisor: SupervisorDep, tenant_id: TenantDep
) -> EventSourceResponse:
    """SSE stream with one event per agent step.

    Event types (aligned with `SupervisorGraph.stream`):
    - `agent_step`: an agent produced a response; data = {agent, content, flags, tool_calls}
    - `blocked`: blocked by a guardrail (L1 input / L3 output / L4 cross-tenant);
      data = {reason, layer, kind}
    - `awaiting_approval`: a destructive action was paused by the HITL gateway
      for human approval (M12);
      data = {thread_ref, pending:[{id, tool, args, ...}]}
    - `human_owned`: the thread was taken over by a human agent (M12);
      data = {owner, thread_ref}
    - `done`: the current turn ended;
      data = {tokens, cost_usd, over_budget, guardrail_latency_ms, ...}
    """

    # Tenant identity is resolved by get_tenant_id (without authentication, the
    # demo falls back to DEFAULT_TENANT_ID). An explicit tenant_id in the request
    # body takes precedence and is passed downstream to SET LOCAL app.tenant_id.
    resolved_tenant = req.tenant_id or tenant_id

    async def event_stream() -> AsyncIterator[dict[str, str]]:
        async for event in supervisor.stream(
            message=req.message,
            tenant_id=resolved_tenant,
            customer_id=req.customer_id,
            thread_id=req.thread_id,
        ):
            yield {"event": event["type"], "data": event["data"]}

    return EventSourceResponse(event_stream())
