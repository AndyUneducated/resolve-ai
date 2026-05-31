"""Modeled $/ticket pricing for the architecture ablation (M7).

Token *counts* are real (captured from the local Ollama runs via
`core.usage`); the *dollar* figures are modeled by mapping each cost tier to a
representative published Anthropic list price. This keeps the benchmark free and
reproducible while still showing the real-world economics of cost routing
(cheap triage tier + expensive vertical tier).

Prices are USD per 1M tokens (input, output), Anthropic public list prices as of
2026-05; update `PRICE_PER_MTOK` if the rate card changes.
"""

from __future__ import annotations

from resolveai_api.core.usage import RunTrace, TierUsage

# Representative cloud model standing in for each local cost tier.
TIER_REPRESENTATIVE: dict[str, str] = {
    "triage": "claude-3-5-haiku",
    "vertical": "claude-sonnet-4",
}

# (input_usd_per_mtok, output_usd_per_mtok)
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-sonnet-4": (3.00, 15.00),
}

_DEFAULT_TIER = "vertical"


def tier_price(tier: str) -> tuple[float, float]:
    model = TIER_REPRESENTATIVE.get(tier, TIER_REPRESENTATIVE[_DEFAULT_TIER])
    return PRICE_PER_MTOK[model]


def usage_cost_usd(tier: str, usage: TierUsage) -> float:
    price_in, price_out = tier_price(tier)
    return (
        usage.input_tokens / 1_000_000.0 * price_in
        + usage.output_tokens / 1_000_000.0 * price_out
    )


def cost_usd(usage_by_tier: dict[str, TierUsage]) -> float:
    """Total modeled cost for one run, priced per cost tier."""
    return sum(usage_cost_usd(tier, usage) for tier, usage in usage_by_tier.items())


def trace_cost_usd(trace: RunTrace) -> float:
    return cost_usd(trace.usage_by_tier)
