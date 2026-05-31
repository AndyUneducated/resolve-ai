"""聊天入口 — SSE 流式返回 Agent 响应 + tool trace。"""

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
    message: str = Field(..., description="客户输入")
    customer_id: str = Field(..., description="客户 ID — 决策 4 · Layer 4 记忆隔离 key")
    thread_id: str | None = Field(
        default=None,
        description="会话 ID；为空则新建。中断恢复 / 跨班次接力靠它。",
    )
    tenant_id: str | None = Field(default=None, description="租户 ID（多租户隔离）")


@router.post("/chat")
async def chat(
    req: ChatRequest, supervisor: SupervisorDep, tenant_id: TenantDep
) -> EventSourceResponse:
    """SSE 流：每一步 token / tool_call / handoff / final 都是一个 event。"""

    # 租户身份统一由 get_tenant_id 解析（无鉴权，demo 回退 DEFAULT_TENANT_ID）；
    # 请求体里若显式带了 tenant_id 则以它为准，喂给下游 SET LOCAL app.tenant_id。
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
