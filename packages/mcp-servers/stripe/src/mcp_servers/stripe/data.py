"""Deterministic in-memory fake data backing the Stripe MCP server.

Designed to exercise every Billing branch:
- charges < $500     → safely auto-refundable
- charges >= $500    → Billing must propose escalation
- already-refunded   → refund() must error out (cross-check requirement)
- duplicate charges  → "I was charged twice" ticket demo
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Charge:
    id: str
    customer_id: str
    amount: int  # minor units (cents)
    currency: str = "usd"
    status: str = "succeeded"  # succeeded | refunded | failed
    description: str = ""
    refunded_amount: int = 0
    created_at: str = "2026-04-01T00:00:00Z"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Store:
    charges: dict[str, Charge] = field(default_factory=dict)

    def list_for_customer(self, customer_id: str, limit: int = 10) -> list[Charge]:
        out = [c for c in self.charges.values() if c.customer_id == customer_id]
        out.sort(key=lambda c: c.created_at, reverse=True)
        return out[:limit]


def _seed() -> _Store:
    store = _Store()
    seeds: list[Charge] = [
        Charge("ch_001", "cus_demo_001", amount=9900, description="Pro plan – April"),
        Charge(
            "ch_002",
            "cus_demo_001",
            amount=9900,
            description="Pro plan – April (duplicate)",
        ),
        Charge(
            "ch_003",
            "cus_demo_001",
            amount=9900,
            status="refunded",
            refunded_amount=9900,
            description="March refund (already issued)",
        ),
        Charge("ch_004", "cus_demo_002", amount=2900, description="Starter plan"),
        Charge(
            "ch_005",
            "cus_demo_003",
            amount=120000,  # $1200 — > $500 escalation trigger
            description="Enterprise add-on",
        ),
    ]
    for c in seeds:
        store.charges[c.id] = c
    return store


STORE = _seed()


def reset_store() -> None:
    """Restore deterministic seed state. Used by tests / between runs."""
    global STORE
    STORE = _seed()


def list_charges(customer_id: str, limit: int = 10) -> list[dict[str, Any]]:
    return [c.to_dict() for c in STORE.list_for_customer(customer_id, limit=limit)]


def get_charge(charge_id: str) -> dict[str, Any]:
    charge = STORE.charges.get(charge_id)
    if charge is None:
        raise KeyError(f"charge_not_found: {charge_id}")
    return charge.to_dict()


def refund(charge_id: str, amount: int | None = None) -> dict[str, Any]:
    """Refund (full or partial). Raises ValueError on policy violations.

    Policy:
    - amount must be <= remaining (charge.amount - charge.refunded_amount)
    - already-refunded charges cannot be refunded again
    """
    charge = STORE.charges.get(charge_id)
    if charge is None:
        raise KeyError(f"charge_not_found: {charge_id}")
    if charge.status == "refunded":
        raise ValueError(f"already_refunded: {charge_id}")

    remaining = charge.amount - charge.refunded_amount
    refund_amount = amount if amount is not None else remaining
    if refund_amount <= 0 or refund_amount > remaining:
        raise ValueError(
            f"invalid_refund_amount: {refund_amount} (remaining={remaining})"
        )

    charge.refunded_amount += refund_amount
    if charge.refunded_amount >= charge.amount:
        charge.status = "refunded"

    return {
        "id": f"re_{charge_id}",
        "charge_id": charge_id,
        "amount": refund_amount,
        "currency": charge.currency,
        "status": "succeeded",
    }
