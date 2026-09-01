"""Shared LangGraph state for stateful handoffs.

The key to Decision 1 is passing only a structured ticket summary during
handoff rather than the full conversation. Combined with LangGraph state
checkpointing, this provides:
  1. Approximately 60% token reduction
  2. Interruption recovery for returning users, agent crashes, and shift handoffs
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

AgentName = Literal["triage", "billing", "technical", "escalation"]


class TicketSummary(TypedDict, total=False):
    """Structured payload for cross-agent handoff; excludes the full conversation."""

    intent: str
    customer_id: str
    tenant_id: str
    entities: dict[str, object]  # eg. {"charge_id": "...", "amount": 99}
    sla_tier: str
    confidence: float


class GraphState(TypedDict, total=False):
    # LangGraph's built-in messages reducer.
    messages: Annotated[list, add_messages]

    # Multi-tenant and multi-customer isolation keys (Decision 4 · Layer 4).
    tenant_id: str
    customer_id: str
    thread_id: str

    # Agent currently handling the request.
    current_agent: AgentName

    # Structured cross-agent handoff payload.
    ticket_summary: TicketSummary

    # Plan-and-Execute plan (Decision 1).
    plan: list[str]

    # Tool-call trace streamed to the frontend and EvalGate.
    tool_calls: list[dict[str, object]]

    # Guardrail flags.
    guardrail_flags: list[str]

    # A business agent requests human intervention; the Supervisor routes to
    # the escalation node for a real handoff rather than a textual suggestion.
    escalate: bool
