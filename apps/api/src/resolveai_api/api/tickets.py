"""Ticket 管理 — leader dashboard 用。

持久化尚未接入：ticket 目前只作为一次会话的瞬态对象存在（Supervisor 内），
没有独立的 ticket 表 / 查询层。为避免把占位数据当成真实数据，这里的读接口
返回诚实的空结果 / 404，而不是编造 `customer_id="todo"` 之类的假记录。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/tickets", tags=["tickets"])


class TicketSummary(BaseModel):
    id: str
    customer_id: str
    intent: str | None
    status: str
    auto_resolved: bool


@router.get("")
async def list_tickets(tenant_id: str = "demo") -> list[TicketSummary]:
    """列出 ticket。持久化层未接入前诚实返回空列表（而非占位数据）。"""
    return []


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: str, tenant_id: str = "demo") -> TicketSummary:
    """按 ID 查 ticket。无持久化层，返回 404 而不是编造记录。"""
    raise HTTPException(
        status_code=404,
        detail=f"ticket {ticket_id!r} not found: ticket persistence is not wired yet",
    )
