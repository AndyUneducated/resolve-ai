"""M7 architecture ablation runner.

Runs the benchmark across architecture variants (A/B/C/D + optional cost-routing
ablation), measuring per ticket:
- real token counts (local Ollama, split by cost tier) and modeled $/ticket,
- end-to-end latency,
- LLM-judged auto-resolution (resolved + 0-1 score),
- tool-error rate (failed calls / wrong-tool selection / hallucinated entities).

Each variant invokes the compiled LangGraph directly (no guardrail wrapper), so
the numbers reflect the agent architecture, not the (constant) M5 guardrail layer.

Outputs (under reports/):
- arch_eval_<ts>.jsonl  — one row per (variant x ticket)
- arch_eval_<ts>.json   — aggregated summary
- arch_eval_<ts>.md     — Architecture Ablation Table + cost-routing + failure modes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from resolveai_api.config import get_settings
from resolveai_api.core.checkpointer import lifespan_checkpointer
from resolveai_api.core.executor import Executor
from resolveai_api.eval.arch_scoring import build_summary, load_jsonl, render_markdown
from resolveai_api.eval.judge import ResolutionJudge
from resolveai_api.eval.pricing import trace_cost_usd
from resolveai_api.eval.trace import capture_run, classify_tool_errors
from resolveai_api.eval.variants import (
    ABLATION_KEYS,
    COST_ROUTING_KEYS,
    VARIANTS,
    build_variant,
)
from resolveai_api.mcp.toolbelt import ToolBelt

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCHMARK = ROOT / "apps" / "api" / "tests" / "fixtures" / "benchmark_tickets.jsonl"
REPORTS_DIR = ROOT / "reports"

# Variants need the full SaaS surface (variant A sees every tool); enable all 5
# MCP servers for the eval unless the environment already configured them.
_MCP_DEFAULTS = {
    "MCP_STRIPE_CMD": "python -m mcp_servers.stripe",
    "MCP_ZENDESK_CMD": "python -m mcp_servers.zendesk",
    "MCP_SLACK_CMD": "python -m mcp_servers.slack",
    "MCP_SALESFORCE_CMD": "python -m mcp_servers.salesforce",
    "MCP_INTERCOM_CMD": "python -m mcp_servers.intercom",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the M7 architecture ablation.")
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument(
        "--variants",
        type=str,
        default=",".join(ABLATION_KEYS),
        help="Comma-separated variant keys (A,B,C,D[,D_triage_vertical]).",
    )
    parser.add_argument(
        "--cost-routing",
        action="store_true",
        help="Also run the cost-routing ablation variant (D_triage_vertical).",
    )
    parser.add_argument(
        "--categories",
        type=str,
        default="all",
        help="Comma-separated categories (billing,technical,escalation) or 'all'.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Cap rows after filtering.")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run only 3 tickets per category for fast smoke checks.",
    )
    parser.add_argument("--case-timeout", type=float, default=180.0)
    return parser.parse_args()


def _parse_variants(raw: str, cost_routing: bool) -> list[str]:
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if cost_routing:
        for key in COST_ROUTING_KEYS:
            if key not in names:
                names.append(key)
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise ValueError(f"Unknown variant(s): {unknown}. Available: {sorted(VARIANTS)}")
    return names


def _parse_categories(raw: str) -> set[str]:
    if raw.strip().lower() == "all":
        return {"billing", "technical", "escalation"}
    return {c.strip() for c in raw.split(",") if c.strip()}


def _select_cases(
    rows: list[dict[str, Any]], *, categories: set[str], quick: bool, limit: int
) -> list[dict[str, Any]]:
    filtered = [r for r in rows if r.get("category") in categories]
    if quick:
        by_cat: dict[str, list[dict[str, Any]]] = {}
        for row in filtered:
            by_cat.setdefault(str(row.get("category")), []).append(row)
        sliced: list[dict[str, Any]] = []
        for cat in sorted(by_cat):
            sliced.extend(by_cat[cat][:3])
        filtered = sliced
    if limit > 0:
        return filtered[:limit]
    return filtered


def _apply_env() -> None:
    os.environ.setdefault("CHECKPOINT_BACKEND", "memory")
    for key, value in _MCP_DEFAULTS.items():
        os.environ.setdefault(key, value)
    get_settings.cache_clear()


async def _check_prereqs() -> None:
    settings = get_settings()
    if settings.llm_backend != "ollama":
        return
    url = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:  # pragma: no cover - env dependent
        raise RuntimeError(
            f"Ollama is unreachable at {settings.ollama_base_url}. Start Ollama first."
        ) from exc


def _tool_summary(tool_names: list[str]) -> str:
    return ", ".join(tool_names) if tool_names else "(none)"


async def _eval_ticket(
    *,
    runner: Any,
    ticket: dict[str, Any],
    judge: ResolutionJudge,
    case_timeout_s: float,
) -> dict[str, Any]:
    ticket_id = str(ticket.get("id"))
    variant_key = runner.spec.key
    thread_id = f"{variant_key}-{ticket_id}"
    expected_tools = ticket.get("expected_tool_calls") or []
    expected_path = ticket.get("expected_resolution_path") or []

    outcome = "ok"
    error_detail: str | None = None
    start = time.perf_counter()
    with capture_run() as trace:
        try:
            result = await asyncio.wait_for(
                runner.run(
                    message=str(ticket.get("prompt")),
                    customer_id=str(ticket.get("customer_id") or f"eval::{ticket_id}"),
                    tenant_id="eval",
                    thread_id=thread_id,
                ),
                timeout=case_timeout_s,
            )
        except TimeoutError:
            outcome = "timeout"
            error_detail = f"case exceeded {case_timeout_s:.0f}s"
            result = None
        except Exception as exc:  # transient runtime / model failure
            outcome = "error"
            error_detail = f"{type(exc).__name__}: {exc}"
            result = None
    latency_ms = (time.perf_counter() - start) * 1000.0

    input_tokens = trace.total_input
    output_tokens = trace.total_output
    cost = trace_cost_usd(trace)
    tool_names = trace.tool_names

    final_answer = result.final_answer if result else ""
    agent_path = result.agent_path if result else []
    flags = result.flags if result else []
    blocked = bool(result.blocked) if result else False

    tool_errors = classify_tool_errors(
        trace=trace, expected_tool_calls=expected_tools, flags=flags
    )

    # Judge runs OUTSIDE the capture context so its tokens are not counted.
    if outcome == "ok":
        verdict = await judge.judge(
            prompt=str(ticket.get("prompt")),
            rubric=str(ticket.get("rubric", "")),
            final_answer=final_answer,
            tool_summary=_tool_summary(tool_names),
            blocked=blocked,
        )
        resolved, score, judge_reason = verdict.resolved, verdict.score, verdict.reason
    else:
        resolved, score, judge_reason = False, 0.0, None

    path_match = bool(expected_path) and all(node in agent_path for node in expected_path)

    return {
        "variant": variant_key,
        "variant_label": runner.spec.label,
        "id": ticket_id,
        "category": ticket.get("category"),
        "expected_intent": ticket.get("expected_intent"),
        "outcome": outcome,
        "error": error_detail,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": round(cost, 6),
        "latency_ms": round(latency_ms, 2),
        "resolved": resolved,
        "score": score,
        "judge_reason": judge_reason,
        "tool_error": tool_errors.has_error,
        "tool_error_reasons": tool_errors.reasons(),
        "tool_calls_used": tool_names,
        "expected_tool_calls": expected_tools,
        "agent_path": agent_path,
        "expected_resolution_path": expected_path,
        "path_match": path_match,
        "blocked": blocked,
        "final_answer": final_answer[:1500],
    }


async def _run_variant(
    variant_key: str,
    cases: list[dict[str, Any]],
    *,
    case_timeout_s: float,
    on_row: Callable[[dict[str, Any]], None],
) -> list[dict[str, Any]]:
    spec = VARIANTS[variant_key]
    judge = ResolutionJudge()
    async with AsyncExitStack() as stack:
        checkpointer = await stack.enter_async_context(lifespan_checkpointer())
        toolbelt = await ToolBelt.from_settings()
        executor = Executor()
        runner = build_variant(
            spec, checkpointer=checkpointer, toolbelt=toolbelt, executor=executor
        )
        rows: list[dict[str, Any]] = []
        for ticket in cases:
            row = await _eval_ticket(
                runner=runner,
                ticket=ticket,
                judge=judge,
                case_timeout_s=case_timeout_s,
            )
            on_row(row)
            rows.append(row)
        return rows


def _report_paths() -> tuple[Path, Path, Path]:
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return (
        REPORTS_DIR / f"arch_eval_{ts}.jsonl",
        REPORTS_DIR / f"arch_eval_{ts}.json",
        REPORTS_DIR / f"arch_eval_{ts}.md",
    )


async def run() -> int:
    args = parse_args()
    _apply_env()
    await _check_prereqs()

    variants = _parse_variants(args.variants, args.cost_routing)
    categories = _parse_categories(args.categories)
    rows_all = load_jsonl(args.benchmark)
    cases = _select_cases(
        rows_all, categories=categories, quick=args.quick, limit=args.limit
    )
    if not cases:
        raise RuntimeError("No tickets selected after applying --categories/--limit.")

    jsonl_path, json_path, md_path = _report_paths()
    all_rows: list[dict[str, Any]] = []
    with jsonl_path.open("w", encoding="utf-8") as handle:

        def _write_row(row: dict[str, Any]) -> None:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()

        for variant_key in variants:
            rows = await _run_variant(
                variant_key,
                cases,
                case_timeout_s=args.case_timeout,
                on_row=_write_row,
            )
            all_rows.extend(rows)
            resolved = sum(1 for r in rows if r.get("resolved"))
            errors = sum(1 for r in rows if r.get("outcome") in ("error", "timeout"))
            print(
                f"[arch] variant={variant_key} rows={len(rows)} "
                f"resolved={resolved} errored={errors}"
            )

    summary = build_summary(all_rows)
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
    with md_path.open("w", encoding="utf-8") as handle:
        handle.write(render_markdown(summary))

    print(f"[arch] raw rows:     {jsonl_path}")
    print(f"[arch] summary json: {json_path}")
    print(f"[arch] summary md:   {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
