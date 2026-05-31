"""Online regression gate (M8) — OTel/EvalGate-backed quality guard.

Runs a small benchmark slice through the production variant (D) under
`core.usage.capture_run()`, scores it with the same M7 primitives (latency,
modeled cost, LLM-judged auto-resolution, tool-error rate), pushes each
per-ticket summary to EvalGate (`EVALGATE_ENDPOINT`, no-op when unset), and
compares the aggregate metrics against a committed baseline. Exits non-zero on
regression so it can gate CI / deploys.

Usage:
    uv run python scripts/regression_gate.py                      # gate vs baseline
    uv run python scripts/regression_gate.py --update-baseline    # refresh baseline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = ROOT / "apps" / "api" / "tests" / "fixtures" / "benchmark_tickets.jsonl"
DEFAULT_BASELINE = ROOT / "reports" / "baseline" / "metrics_baseline.json"

_GUARDED = ("p95_latency_ms", "auto_resolve_rate", "tool_error_rate", "mean_cost_usd")
_DEFAULT_THRESHOLDS = {
    "p95_latency_ms_max_increase_pct": 50.0,
    "mean_cost_usd_max_increase_pct": 50.0,
    "auto_resolve_rate_min_drop_pp": 5.0,
    "tool_error_rate_max_increase_pp": 5.0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Online regression gate (M8).")
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--variant", type=str, default="D")
    parser.add_argument("--limit", type=int, default=12, help="Tickets to score.")
    parser.add_argument(
        "--backend", type=str, default="fake", choices=["fake", "ollama", "anthropic"]
    )
    parser.add_argument("--case-timeout", type=float, default=60.0)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Write current metrics as the new baseline and exit 0.",
    )
    return parser.parse_args()


def _configure_env(backend: str) -> None:
    os.environ["LLM_BACKEND"] = backend
    os.environ["CHECKPOINT_BACKEND"] = "memory"
    from resolveai_api.config import get_settings

    get_settings.cache_clear()


def _load_tickets(path: Path, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if line:
                rows.append(json.loads(line))
    return rows[:limit] if limit > 0 else rows


async def _score_tickets(
    *, variant_key: str, tickets: list[dict[str, Any]], case_timeout_s: float
) -> list[dict[str, Any]]:
    from langgraph.checkpoint.memory import MemorySaver
    from resolveai_api.eval.judge import ResolutionJudge
    from resolveai_api.eval.pricing import trace_cost_usd
    from resolveai_api.eval.trace import capture_run, classify_tool_errors
    from resolveai_api.eval.variants import VARIANTS, build_variant
    from resolveai_api.mcp.toolbelt import ToolBelt
    from resolveai_api.observability.evalgate import EvalGateClient, build_run_summary

    runner = build_variant(
        VARIANTS[variant_key], checkpointer=MemorySaver(), toolbelt=ToolBelt([])
    )
    judge = ResolutionJudge()
    client = EvalGateClient()
    rows: list[dict[str, Any]] = []

    for ticket in tickets:
        ticket_id = str(ticket.get("id"))
        outcome = "ok"
        error_detail: str | None = None
        start = time.perf_counter()
        with capture_run() as trace:
            try:
                result = await asyncio.wait_for(
                    runner.run(
                        message=str(ticket.get("prompt")),
                        customer_id=str(ticket.get("customer_id") or f"gate::{ticket_id}"),
                        tenant_id="gate",
                        thread_id=f"{variant_key}-{ticket_id}",
                    ),
                    timeout=case_timeout_s,
                )
            except TimeoutError:
                outcome, result = "timeout", None
                error_detail = f"case exceeded {case_timeout_s:.0f}s"
            except Exception as exc:  # pragma: no cover - runtime dependent
                outcome, result = "error", None
                error_detail = f"{type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - start) * 1000.0

        final_answer = result.final_answer if result else ""
        flags = result.flags if result else []
        blocked = bool(result.blocked) if result else False
        tool_errors = classify_tool_errors(
            trace=trace,
            expected_tool_calls=ticket.get("expected_tool_calls") or [],
            flags=flags,
        )

        if outcome == "ok":
            verdict = await judge.judge(
                prompt=str(ticket.get("prompt")),
                rubric=str(ticket.get("rubric", "")),
                final_answer=final_answer,
                tool_summary=", ".join(trace.tool_names) or "(none)",
                blocked=blocked,
            )
            resolved, score = verdict.resolved, verdict.score
        else:
            resolved, score = False, 0.0

        summary = build_run_summary(
            trace,
            ticket_id=ticket_id,
            latency_ms=latency_ms,
            resolved=resolved,
            score=score,
            blocked=blocked,
            tool_error=tool_errors.has_error,
            flags=tool_errors.reasons(),
        )
        await client.push(ticket_id=ticket_id, payload=summary)

        rows.append(
            {
                "variant": variant_key,
                "id": ticket_id,
                "category": ticket.get("category"),
                "outcome": outcome,
                "error": error_detail,
                "input_tokens": trace.total_input,
                "output_tokens": trace.total_output,
                "total_tokens": trace.total_tokens,
                "cost_usd": round(trace_cost_usd(trace), 6),
                "latency_ms": round(latency_ms, 2),
                "resolved": resolved,
                "score": score,
                "tool_error": tool_errors.has_error,
            }
        )
    return rows


def _check_regressions(
    current: dict[str, Any], baseline: dict[str, Any]
) -> list[str]:
    base_metrics = baseline.get("metrics", {})
    thresholds = {**_DEFAULT_THRESHOLDS, **baseline.get("thresholds", {})}
    violations: list[str] = []

    def _pct_increase(metric: str, max_pct: float) -> None:
        base = float(base_metrics.get(metric, 0.0))
        cur = float(current.get(metric, 0.0))
        if base <= 0:
            return
        delta_pct = (cur - base) / base * 100.0
        if delta_pct > max_pct:
            violations.append(
                f"{metric} up {delta_pct:.1f}% (>{max_pct:.0f}% allowed): "
                f"{base:.4g} -> {cur:.4g}"
            )

    _pct_increase("p95_latency_ms", thresholds["p95_latency_ms_max_increase_pct"])
    _pct_increase("mean_cost_usd", thresholds["mean_cost_usd_max_increase_pct"])

    base_resolve = float(base_metrics.get("auto_resolve_rate", 0.0))
    cur_resolve = float(current.get("auto_resolve_rate", 0.0))
    drop_pp = (base_resolve - cur_resolve) * 100.0
    if drop_pp > thresholds["auto_resolve_rate_min_drop_pp"]:
        violations.append(
            f"auto_resolve_rate dropped {drop_pp:.1f}pp "
            f"(>{thresholds['auto_resolve_rate_min_drop_pp']:.0f}pp allowed): "
            f"{base_resolve:.2%} -> {cur_resolve:.2%}"
        )

    base_err = float(base_metrics.get("tool_error_rate", 0.0))
    cur_err = float(current.get("tool_error_rate", 0.0))
    rise_pp = (cur_err - base_err) * 100.0
    if rise_pp > thresholds["tool_error_rate_max_increase_pp"]:
        violations.append(
            f"tool_error_rate up {rise_pp:.1f}pp "
            f"(>{thresholds['tool_error_rate_max_increase_pp']:.0f}pp allowed): "
            f"{base_err:.2%} -> {cur_err:.2%}"
        )
    return violations


async def run() -> int:
    args = parse_args()
    _configure_env(args.backend)

    from resolveai_api.eval.arch_scoring import variant_metrics

    tickets = _load_tickets(args.benchmark, args.limit)
    if not tickets:
        raise RuntimeError("No benchmark tickets loaded.")

    print(
        f"[gate] backend={args.backend} variant={args.variant} "
        f"tickets={len(tickets)}"
    )
    rows = await _score_tickets(
        variant_key=args.variant, tickets=tickets, case_timeout_s=args.case_timeout
    )
    metrics = variant_metrics(rows, args.variant)
    current = {k: metrics[k] for k in _GUARDED}
    current["mean_total_tokens"] = metrics["mean_total_tokens"]

    print("[gate] current metrics:")
    for key, value in current.items():
        print(f"  {key}: {value:.4g}")

    if args.update_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "variant": args.variant,
            "backend": args.backend,
            "n": metrics["n"],
            "metrics": current,
            "thresholds": _DEFAULT_THRESHOLDS,
        }
        with args.baseline.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"[gate] baseline updated -> {args.baseline}")
        return 0

    if not args.baseline.exists():
        raise RuntimeError(
            f"No baseline at {args.baseline}. Run with --update-baseline first."
        )
    with args.baseline.open("r", encoding="utf-8") as handle:
        baseline = json.load(handle)

    violations = _check_regressions(current, baseline)
    if violations:
        print("[gate] REGRESSION DETECTED:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("[gate] PASS — no regression vs baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
