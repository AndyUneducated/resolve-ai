"""Ticket management for the leader dashboard.

Persistence is not yet integrated: tickets currently exist only as transient
objects within a single session (inside the Supervisor), with no independent
ticket table or query layer. To avoid presenting placeholder data as real, these
read endpoints return an honest empty result or 404 instead of fabricating
records such as `customer_id="todo"`.
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
    """List tickets, returning an empty list until persistence is integrated."""
    return []


@router.get("/{ticket_id}")
async def get_ticket(ticket_id: str, tenant_id: str = "demo") -> TicketSummary:
    """Look up a ticket by ID, returning 404 while persistence is unavailable."""
    raise HTTPException(
        status_code=404,
        detail=f"ticket {ticket_id!r} not found: ticket persistence is not wired yet",
    )
