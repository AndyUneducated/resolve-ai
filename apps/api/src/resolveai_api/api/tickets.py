"""Ticket 管理 — leader dashboard 用。"""

from __future__ import annotations

from fastapi import APIRouter
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
    """TODO: 接 DB 查询。"""
    return []


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: str, tenant_id: str = "demo") -> TicketSummary:
    """TODO: 接 DB 查询 + 返回 handoff_summary + trace。"""
    return TicketSummary(
        id=ticket_id,
        customer_id="todo",
        intent=None,
        status="open",
        auto_resolved=False,
    )
