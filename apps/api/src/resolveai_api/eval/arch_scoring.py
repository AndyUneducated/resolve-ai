"""Aggregation + markdown rendering for the M7 architecture ablation.

Consumes the per-(variant x ticket) JSONL rows emitted by
`scripts/eval_architecture.py` and produces:
- per-variant metric aggregates (tokens, $/ticket, P50/P95 latency, auto-resolve
  rate, tool-error rate),
- the headline Architecture Ablation Table with a `Delta (D vs A)` row,
- the cost-routing ablation table (D vs triage-on-vertical),
- a failure-mode report (worst cases per variant).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from resolveai_api.eval.variants import ABLATION_KEYS, COST_ROUTING_KEYS, VARIANTS

_BAD_OUTCOMES = ("error", "timeout")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _scored_rows(rows: list[dict[str, Any]], variant: str) -> list[dict[str, Any]]:
    return [
        r
        for r in rows
        if r.get("variant") == variant and r.get("outcome") not in _BAD_OUTCOMES
    ]


def variant_metrics(rows: list[dict[str, Any]], variant: str) -> dict[str, Any]:
    scored = _scored_rows(rows, variant)
    latencies = [float(r.get("latency_ms", 0.0)) for r in scored]
    n = len(scored)
    resolved = sum(1 for r in scored if r.get("resolved"))
    tool_errors = sum(1 for r in scored if r.get("tool_error"))
    total_rows = sum(1 for r in rows if r.get("variant") == variant)
    errored = total_rows - n
    return {
        "variant": variant,
        "label": VARIANTS[variant].label if variant in VARIANTS else variant,
        "n": n,
        "errored": errored,
        "mean_input_tokens": _mean([float(r.get("input_tokens", 0)) for r in scored]),
        "mean_output_tokens": _mean([float(r.get("output_tokens", 0)) for r in scored]),
        "mean_total_tokens": _mean([float(r.get("total_tokens", 0)) for r in scored]),
        "mean_cost_usd": _mean([float(r.get("cost_usd", 0.0)) for r in scored]),
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "auto_resolve_rate": (resolved / n) if n else 0.0,
        "mean_score": _mean([float(r.get("score", 0.0)) for r in scored]),
        "tool_error_rate": (tool_errors / n) if n else 0.0,
    }


def _present_variants(rows: list[dict[str, Any]], keys: list[str]) -> list[str]:
    seen = {r.get("variant") for r in rows}
    return [k for k in keys if k in seen]


def build_ablation_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
    variants = _present_variants(rows, ABLATION_KEYS)
    metrics = {v: variant_metrics(rows, v) for v in variants}
    delta = None
    if "A" in metrics and "D" in metrics:
        a, d = metrics["A"], metrics["D"]
        delta = {
            "token_pct": _pct_change(d["mean_total_tokens"], a["mean_total_tokens"]),
            "cost_pct": _pct_change(d["mean_cost_usd"], a["mean_cost_usd"]),
            "p95_pct": _pct_change(d["p95_latency_ms"], a["p95_latency_ms"]),
            "auto_resolve_pp": (d["auto_resolve_rate"] - a["auto_resolve_rate"]) * 100.0,
            "tool_error_pp": (d["tool_error_rate"] - a["tool_error_rate"]) * 100.0,
        }
    return {"variants": [metrics[v] for v in variants], "delta_d_vs_a": delta}


def build_cost_routing_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [variant_metrics(rows, v) for v in _present_variants(rows, COST_ROUTING_KEYS)]


def build_failure_modes(
    rows: list[dict[str, Any]], *, per_variant: int = 2
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for variant in _present_variants(rows, ABLATION_KEYS):
        subset = [r for r in rows if r.get("variant") == variant]
        # Worst = errored/timeout first, then lowest judge score.
        subset.sort(
            key=lambda r: (
                0 if r.get("outcome") in _BAD_OUTCOMES else 1,
                float(r.get("score", 0.0)),
            )
        )
        worst = []
        for row in subset[:per_variant]:
            worst.append(
                {
                    "id": row.get("id"),
                    "category": row.get("category"),
                    "outcome": row.get("outcome"),
                    "score": row.get("score"),
                    "resolved": row.get("resolved"),
                    "tool_error_reasons": row.get("tool_error_reasons", []),
                    "judge_reason": row.get("judge_reason"),
                    "error": row.get("error"),
                }
            )
        out[variant] = worst
    return out


def _pct_change(new: float, base: float) -> float | None:
    if base == 0:
        return None
    return (new - base) / base * 100.0


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ablation": build_ablation_table(rows),
        "cost_routing": build_cost_routing_table(rows),
        "failure_modes": build_failure_modes(rows),
    }


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def _fmt_int(value: float) -> str:
    return f"{value:,.0f}"


def _fmt_usd(value: float) -> str:
    return f"${value:.4f}"


def _fmt_s(ms: float) -> str:
    return f"{ms / 1000.0:.1f}"


def _fmt_pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def _fmt_delta_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def _fmt_pp(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}pp"


def render_markdown(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("## Architecture Ablation Table")
    lines.append("")
    lines.append(
        "| Variant | Token/ticket | $/ticket | P95 (s) | Auto-resolve | Tool error |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|")
    for m in summary["ablation"]["variants"]:
        lines.append(
            f"| {m['variant']} · {m['label']} | {_fmt_int(m['mean_total_tokens'])} | "
            f"{_fmt_usd(m['mean_cost_usd'])} | {_fmt_s(m['p95_latency_ms'])} | "
            f"{_fmt_pct(m['auto_resolve_rate'])} | {_fmt_pct(m['tool_error_rate'])} |"
        )
    delta = summary["ablation"].get("delta_d_vs_a")
    if delta:
        lines.append(
            f"| **Δ (D vs A)** | {_fmt_delta_pct(delta['token_pct'])} | "
            f"{_fmt_delta_pct(delta['cost_pct'])} | {_fmt_delta_pct(delta['p95_pct'])} | "
            f"{_fmt_pp(delta['auto_resolve_pp'])} | {_fmt_pp(delta['tool_error_pp'])} |"
        )
    lines.append("")
    lines.append(
        "> Token counts are real (local Ollama); $/ticket is modeled by pricing each "
        "cost tier at representative Anthropic list prices. Runs bypass the guardrail "
        "layer so numbers reflect the agent architecture only."
    )
    lines.append("")

    cost_rows = summary.get("cost_routing", [])
    if cost_rows:
        lines.append("## Cost-Routing Ablation (Triage tier)")
        lines.append("")
        lines.append("| Config | Triage tier | $/ticket | Auto-resolve |")
        lines.append("|---|---|---:|---:|")
        for m in cost_rows:
            tier = "vertical" if m["variant"].endswith("vertical") else "triage"
            lines.append(
                f"| {m['variant']} | {tier} | {_fmt_usd(m['mean_cost_usd'])} | "
                f"{_fmt_pct(m['auto_resolve_rate'])} |"
            )
        lines.append("")

    lines.append("## Failure-Mode Report")
    lines.append("")
    for variant, worst in summary.get("failure_modes", {}).items():
        lines.append(f"### Variant {variant}")
        if not worst:
            lines.append("- (no cases)")
            lines.append("")
            continue
        for row in worst:
            reasons = ", ".join(row.get("tool_error_reasons") or []) or "—"
            detail = row.get("judge_reason") or row.get("error") or ""
            lines.append(
                f"- `{row['id']}` ({row.get('category')}): score={row.get('score')}, "
                f"outcome={row.get('outcome')}, tool_errors=[{reasons}] — {detail}"
            )
        lines.append("")
    return "\n".join(lines)
