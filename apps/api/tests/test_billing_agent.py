"""BillingAgent finalize logic — the sub-graph-result → (text, escalate) mapping.

Guards the fix for the "says Escalating but escalate=False" bug: when the
plan-execute loop runs out of iteration/cost budget (no response, no steps), the
customer-facing text promises a handoff, so `escalate` MUST be True or the
Supervisor never routes to the escalation node and the ticket dead-ends.
"""

from __future__ import annotations

from resolveai_api.agents.billing import _finalize
from resolveai_api.agents.billing_graph import Response


def test_finalize_uses_response_answer_and_flag() -> None:
    text, escalate = _finalize(Response(final_answer="Refund issued.", escalate=False), [])
    assert text == "Refund issued."
    assert escalate is False


def test_finalize_respects_response_escalate_true() -> None:
    text, escalate = _finalize(Response(final_answer="Handing off.", escalate=True), [])
    assert text == "Handing off."
    assert escalate is True


def test_finalize_summarizes_partial_progress_without_escalating() -> None:
    steps = [("look up the charge", "found ch_1 $99"), ("refund", "refunded ch_1")]
    text, escalate = _finalize(None, steps)
    assert escalate is False
    assert "refund" in text.lower()


def test_finalize_escalates_on_budget_exhaustion() -> None:
    text, escalate = _finalize(None, [])
    assert escalate is True
    assert "escalat" in text.lower()
