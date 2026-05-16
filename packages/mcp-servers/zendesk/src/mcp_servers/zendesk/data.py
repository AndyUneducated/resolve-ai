"""Deterministic in-memory fake data backing the Zendesk MCP server.

Designed to exercise every support branch:
- open tickets per customer for Technical / Billing context recall
- a pre-escalated ticket so `escalate` exercises the idempotency error path
- mixed statuses (open / pending / solved) for `update_ticket` round-trips
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Ticket:
    id: str
    customer_id: str
    subject: str
    status: str = "open"  # open | pending | solved | escalated
    notes: list[str] = field(default_factory=list)
    created_at: str = "2026-04-01T00:00:00Z"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Store:
    tickets: dict[str, Ticket] = field(default_factory=dict)

    def for_customer(self, customer_id: str) -> list[Ticket]:
        out = [t for t in self.tickets.values() if t.customer_id == customer_id]
        out.sort(key=lambda t: t.created_at, reverse=True)
        return out


def _seed() -> _Store:
    store = _Store()
    seeds = [
        Ticket("zd_001", "cus_demo_001", "Duplicate charge on Pro plan", status="open"),
        Ticket(
            "zd_002",
            "cus_demo_001",
            "Refund follow-up",
            status="pending",
            notes=["Billing acknowledged the duplicate."],
        ),
        Ticket(
            "zd_003",
            "cus_demo_002",
            "Onboarding question",
            status="solved",
            notes=["Walked customer through setup."],
        ),
        Ticket(
            "zd_004",
            "cus_demo_003",
            "Enterprise add-on misbilled",
            status="escalated",
            notes=["Pre-escalated; do not re-escalate."],
        ),
    ]
    for t in seeds:
        store.tickets[t.id] = t
    return store


STORE = _seed()


def reset_store() -> None:
    """Restore deterministic seed state. Used by tests / between runs."""
    global STORE
    STORE = _seed()


def get_ticket_history(customer_id: str) -> list[dict[str, Any]]:
    return [t.to_dict() for t in STORE.for_customer(customer_id)]


def update_ticket(
    ticket_id: str,
    status: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Update status and/or append an internal note."""
    ticket = STORE.tickets.get(ticket_id)
    if ticket is None:
        raise KeyError(f"ticket_not_found: {ticket_id}")
    if status is not None:
        if status not in {"open", "pending", "solved", "escalated"}:
            raise ValueError(f"invalid_status: {status!r}")
        ticket.status = status
    if note:
        ticket.notes.append(note)
    return ticket.to_dict()


def escalate(ticket_id: str, reason: str) -> dict[str, Any]:
    """Mark a ticket as escalated. Idempotency error if already escalated."""
    ticket = STORE.tickets.get(ticket_id)
    if ticket is None:
        raise KeyError(f"ticket_not_found: {ticket_id}")
    if ticket.status == "escalated":
        raise ValueError(f"already_escalated: {ticket_id}")
    if not reason or not reason.strip():
        raise ValueError("escalation_requires_reason")
    ticket.status = "escalated"
    ticket.notes.append(f"[escalation] {reason}")
    return ticket.to_dict()
