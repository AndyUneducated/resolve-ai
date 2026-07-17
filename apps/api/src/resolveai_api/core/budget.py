"""Per-ticket cost budget + circuit breaker (M11).

Reads the accrued *modeled* cost from the active `RunTrace` (installed by
`core.usage.capture_run`) and compares it to `settings.cost_budget_usd`.

Used by the vertical Plan-Execute / ReAct loops to **stop spending** once a
ticket blows its budget — a protective degrade (finalize with what we have),
never a hard failure — and by the Supervisor to flag/emit the event
(`cost:budget_exceeded`, `resolveai_cost_budget_exceeded_total`).

A budget of `<= 0` disables the breaker (the demo default is generous), so the
common path is unchanged.
"""

from __future__ import annotations

from resolveai_api.core.usage import RunTrace, current_trace


def is_over_budget(trace: RunTrace | None, budget_usd: float) -> bool:
    """True iff `trace`'s modeled cost exceeds a positive `budget_usd`.

    Pure + explicit (no globals) so it is trivially unit-testable; safe on a
    `None` trace and on a non-positive budget (breaker disabled).
    """
    if trace is None or budget_usd <= 0:
        return False
    from resolveai_api.eval.pricing import trace_cost_usd

    return trace_cost_usd(trace) > budget_usd


def over_cost_budget() -> bool:
    """Check the *active* run trace against the configured per-ticket budget."""
    from resolveai_api.config import get_settings

    return is_over_budget(current_trace(), get_settings().cost_budget_usd)
