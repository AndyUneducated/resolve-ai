"""Deterministic in-memory fake data backing the Salesforce MCP server.

Provides account + opportunity surface used by Billing for SLA context and by
internal flows for opportunity-stage updates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

VALID_STAGES = {"prospecting", "negotiation", "closed_won", "closed_lost"}


@dataclass
class Account:
    customer_id: str
    name: str
    sla_tier: str = "standard"  # standard | priority | enterprise
    annual_revenue: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Opportunity:
    id: str
    account_customer_id: str
    stage: str = "prospecting"
    amount: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Store:
    accounts: dict[str, Account] = field(default_factory=dict)
    opportunities: dict[str, Opportunity] = field(default_factory=dict)


def _seed() -> _Store:
    store = _Store()
    seeds_a = [
        Account("cus_demo_001", "Acme Co.", sla_tier="priority", annual_revenue=240000),
        Account("cus_demo_002", "Solo Studio", sla_tier="standard", annual_revenue=12000),
        Account(
            "cus_demo_003",
            "MegaCorp Inc.",
            sla_tier="enterprise",
            annual_revenue=4_800_000,
        ),
    ]
    for a in seeds_a:
        store.accounts[a.customer_id] = a
    seeds_o = [
        Opportunity("op_001", "cus_demo_001", stage="negotiation", amount=24_000),
        Opportunity("op_002", "cus_demo_003", stage="closed_won", amount=480_000),
    ]
    for o in seeds_o:
        store.opportunities[o.id] = o
    return store


STORE = _seed()


def reset_store() -> None:
    """Restore deterministic state. Used by tests / between runs."""
    global STORE
    STORE = _seed()


def get_account(customer_id: str) -> dict[str, Any]:
    account = STORE.accounts.get(customer_id)
    if account is None:
        raise KeyError(f"account_not_found: {customer_id}")
    return account.to_dict()


def update_opportunity(
    opportunity_id: str,
    stage: str | None = None,
    amount: float | None = None,
) -> dict[str, Any]:
    opp = STORE.opportunities.get(opportunity_id)
    if opp is None:
        raise KeyError(f"opportunity_not_found: {opportunity_id}")
    if stage is None and amount is None:
        raise ValueError("update_requires_stage_or_amount")
    if stage is not None:
        if stage not in VALID_STAGES:
            raise ValueError(f"invalid_stage: {stage!r}")
        opp.stage = stage
    if amount is not None:
        if amount < 0:
            raise ValueError(f"invalid_amount: {amount}")
        opp.amount = float(amount)
    return opp.to_dict()
