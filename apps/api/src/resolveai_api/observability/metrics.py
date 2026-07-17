"""Prometheus metrics (M11) — process-global counters/histograms + render.

Mirrors the graceful-degradation pattern in `observability/tracing.py`: if
`prometheus_client` is not importable, every recorder becomes a no-op and
`/metrics` returns a short notice, so the app never hard-depends on the metrics
stack (tests / chaos-load run fine without it).

Names follow Prometheus conventions (`_total` suffix for counters, base units in
the name). Label cardinality is kept low on purpose (no tenant/customer labels —
those belong in traces, not metrics):

- ``resolveai_tickets_total{outcome}``              — done | blocked
- ``resolveai_guardrail_blocks_total{layer,kind}``  — input|output × true_positive|degraded|...
- ``resolveai_tool_calls_total``                    — MCP tool invocations
- ``resolveai_tool_errors_total``                   — tool invocations that errored
- ``resolveai_cost_budget_exceeded_total``          — runs over the per-ticket budget
- ``resolveai_ticket_cost_usd`` (histogram)         — modeled $/ticket
- ``resolveai_ticket_tokens`` (histogram)           — total tokens/ticket
- ``resolveai_guardrail_latency_ms{layer}`` (hist)  — per-layer scan latency
"""

from __future__ import annotations

from collections.abc import Mapping

try:  # pragma: no cover - import guard mirrors tracing.py
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

    _AVAILABLE = True
except Exception:  # pragma: no cover - metrics stack optional
    _AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

_COST_BUCKETS = (0.0005, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0)
_TOKEN_BUCKETS = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000)
_LATENCY_BUCKETS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500)

if _AVAILABLE:
    TICKETS = Counter(
        "resolveai_tickets_total", "Tickets processed, by terminal outcome.", ["outcome"]
    )
    GUARDRAIL_BLOCKS = Counter(
        "resolveai_guardrail_blocks_total",
        "Guardrail blocks, by layer and block kind.",
        ["layer", "kind"],
    )
    TOOL_CALLS = Counter("resolveai_tool_calls_total", "MCP tool invocations.")
    TOOL_ERRORS = Counter(
        "resolveai_tool_errors_total", "MCP tool invocations that returned an error."
    )
    BUDGET_EXCEEDED = Counter(
        "resolveai_cost_budget_exceeded_total",
        "Runs whose modeled cost exceeded the per-ticket budget.",
    )
    APPROVALS_PENDING = Counter(
        "resolveai_approvals_pending_total",
        "Destructive tool calls parked for human approval (M12 HITL gate).",
    )
    CACHE_HITS = Counter(
        "resolveai_cache_hits_total", "Semantic-cache hits on KB retrieval (M13)."
    )
    CACHE_MISSES = Counter(
        "resolveai_cache_misses_total", "Semantic-cache misses on KB retrieval (M13)."
    )
    COST_USD = Histogram(
        "resolveai_ticket_cost_usd", "Modeled per-ticket cost (USD).", buckets=_COST_BUCKETS
    )
    TOKENS = Histogram(
        "resolveai_ticket_tokens", "Total tokens per ticket.", buckets=_TOKEN_BUCKETS
    )
    GUARDRAIL_LATENCY_MS = Histogram(
        "resolveai_guardrail_latency_ms",
        "Guardrail scan latency (ms), by layer.",
        ["layer"],
        buckets=_LATENCY_BUCKETS,
    )


def available() -> bool:
    return _AVAILABLE


def record_block(layer: str, kind: str) -> None:
    """Count one blocked ticket + the (layer, kind) attribution."""
    if not _AVAILABLE:
        return
    TICKETS.labels(outcome="blocked").inc()
    GUARDRAIL_BLOCKS.labels(layer=layer, kind=kind).inc()


def record_awaiting(pending: int = 1) -> None:
    """Count one ticket parked for human approval (+ the # of parked actions)."""
    if not _AVAILABLE:
        return
    TICKETS.labels(outcome="awaiting_approval").inc()
    if pending > 0:
        APPROVALS_PENDING.inc(pending)


def record_cache_hit() -> None:
    if _AVAILABLE:
        CACHE_HITS.inc()


def record_cache_miss() -> None:
    if _AVAILABLE:
        CACHE_MISSES.inc()


def record_done(
    *,
    cost_usd: float,
    tokens: int,
    tool_calls: int = 0,
    tool_errors: int = 0,
    guardrail_latency_ms: Mapping[str, float] | None = None,
    over_budget: bool = False,
) -> None:
    """Record a successfully completed ticket's cost/usage/latency."""
    if not _AVAILABLE:
        return
    TICKETS.labels(outcome="done").inc()
    COST_USD.observe(max(0.0, float(cost_usd)))
    TOKENS.observe(max(0, int(tokens)))
    if tool_calls:
        TOOL_CALLS.inc(tool_calls)
    if tool_errors:
        TOOL_ERRORS.inc(tool_errors)
    for layer, ms in (guardrail_latency_ms or {}).items():
        GUARDRAIL_LATENCY_MS.labels(layer=layer).observe(max(0.0, float(ms)))
    if over_budget:
        BUDGET_EXCEEDED.inc()


def render_latest() -> tuple[bytes, str]:
    """Return ``(body, content_type)`` for the ``/metrics`` endpoint."""
    if not _AVAILABLE:
        return (b"# prometheus_client not installed\n", CONTENT_TYPE_LATEST)
    return (generate_latest(), CONTENT_TYPE_LATEST)
