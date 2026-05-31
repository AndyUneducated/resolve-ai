"""Chaos load harness (M8) — fan out N mock tickets concurrently through the
real `SupervisorGraph` and report throughput + latency percentiles.

The goal is to stress the *framework* (LangGraph orchestration, guardrails,
checkpointer, executor chokepoint) rather than model quality, so the default
backend is `fake` (`core/_fake_llm.py`): deterministic, zero-network responses.
That isolates orchestration overhead and makes the P95 < 6s target meaningful on
a laptop. Point `--backend ollama` at a warm Ollama to measure real end-to-end
latency instead (expect far higher P95 on CPU).

Usage:
    uv run python scripts/chaos_load.py --total 5000 --concurrency 200
    uv run python scripts/chaos_load.py --total 200 --concurrency 50   # smoke
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports" / "chaos"

# Ticket templates keyed so the fake triage router (keyword-based) spreads load
# across billing / technical / escalation agent paths.
_TEMPLATES: list[tuple[str, str]] = [
    ("billing", "I was double charged for ${amt} on charge ch_{n:04d}, please refund the duplicate."),
    ("billing", "My invoice shows a ${amt} payment I don't recognize for ch_{n:04d}."),
    ("billing", "Please refund charge ch_{n:04d}; the subscription was cancelled."),
    ("technical", "I'm getting a login error after the latest update, how do I fix it?"),
    ("technical", "The API integration keeps crashing with a 500 — any config I'm missing?"),
    ("technical", "Setup instructions aren't working; the webhook is not working at all."),
    ("escalation", "This is the third time — I want to escalate to a manager about a ${amt} fraud charge."),
    ("escalation", "I'm considering a chargeback and a lawsuit; escalate this immediately."),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Concurrent chaos load harness (M8).")
    parser.add_argument("--total", type=int, default=5000, help="Total mock tickets to run.")
    parser.add_argument(
        "--concurrency", type=int, default=200, help="Max in-flight tickets (semaphore size)."
    )
    parser.add_argument(
        "--backend",
        type=str,
        default="fake",
        choices=["fake", "ollama", "anthropic"],
        help="LLM backend. 'fake' = deterministic zero-latency (default).",
    )
    parser.add_argument(
        "--guardrails",
        dest="guardrails",
        action="store_true",
        help="Keep the full guardrail stack on (default off, to measure orchestration only).",
    )
    parser.add_argument(
        "--with-tools",
        dest="with_tools",
        action="store_true",
        help="Load real MCP toolbelt (default off: empty toolbelt, no subprocess spawn).",
    )
    parser.add_argument(
        "--p95-target", type=float, default=6.0, help="P95 latency target in seconds."
    )
    parser.add_argument("--case-timeout", type=float, default=30.0)
    parser.add_argument("--output", type=Path, default=REPORTS_DIR)
    return parser.parse_args()


def _configure_env(args: argparse.Namespace) -> None:
    os.environ["LLM_BACKEND"] = args.backend
    os.environ["CHECKPOINT_BACKEND"] = "memory"
    if not args.guardrails:
        for layer in ("GUARDRAIL_L1", "GUARDRAIL_L2", "GUARDRAIL_L3", "GUARDRAIL_L4"):
            os.environ[layer] = "off"
    from resolveai_api.config import get_settings

    get_settings.cache_clear()


def _make_ticket(i: int) -> dict[str, str]:
    template = _TEMPLATES[i % len(_TEMPLATES)]
    category, prompt = template
    rendered = prompt.format(amt=(i % 9 + 1) * 25, n=i % 5 + 1)
    return {
        "id": f"chaos-{i:05d}",
        "category": category,
        "prompt": rendered,
        "customer_id": f"cus_demo_{i % 3 + 1:03d}",
    }


async def _run_one(
    supervisor: Any, ticket: dict[str, str], *, case_timeout_s: float
) -> dict[str, Any]:
    start = time.perf_counter()
    blocked = False
    outcome = "ok"
    error: str | None = None
    try:
        async def _drain() -> bool:
            saw_blocked = False
            async for event in supervisor.stream(
                message=ticket["prompt"],
                customer_id=ticket["customer_id"],
                tenant_id="chaos",
                thread_id=ticket["id"],
            ):
                if event.get("type") == "blocked":
                    saw_blocked = True
            return saw_blocked

        blocked = await asyncio.wait_for(_drain(), timeout=case_timeout_s)
    except TimeoutError:
        outcome = "timeout"
        error = f"exceeded {case_timeout_s:.0f}s"
    except Exception as exc:  # pragma: no cover - defensive under load
        outcome = "error"
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = (time.perf_counter() - start) * 1000.0
    return {
        "id": ticket["id"],
        "category": ticket["category"],
        "outcome": outcome,
        "blocked": blocked,
        "latency_ms": round(latency_ms, 2),
        "error": error,
    }


async def _build_supervisor(stack: Any, *, with_tools: bool) -> Any:
    from langgraph.checkpoint.memory import MemorySaver
    from resolveai_api.agents.supervisor import SupervisorGraph
    from resolveai_api.mcp.toolbelt import ToolBelt

    checkpointer = MemorySaver()
    if with_tools:
        toolbelt = await ToolBelt.from_settings()
    else:
        toolbelt = ToolBelt([])
    return SupervisorGraph(checkpointer=checkpointer, toolbelt=toolbelt)


def _summarize(
    rows: list[dict[str, Any]], *, wall_s: float, args: argparse.Namespace
) -> dict[str, Any]:
    from resolveai_api.eval.arch_scoring import _percentile

    ok = [r for r in rows if r["outcome"] == "ok"]
    latencies = [r["latency_ms"] for r in ok]
    errors = sum(1 for r in rows if r["outcome"] == "error")
    timeouts = sum(1 for r in rows if r["outcome"] == "timeout")
    blocked = sum(1 for r in rows if r["blocked"])
    p95_ms = _percentile(latencies, 95)
    return {
        "backend": args.backend,
        "guardrails": args.guardrails,
        "with_tools": args.with_tools,
        "total": len(rows),
        "completed": len(ok),
        "errors": errors,
        "timeouts": timeouts,
        "blocked": blocked,
        "concurrency": args.concurrency,
        "wall_seconds": round(wall_s, 2),
        "throughput_rps": round(len(rows) / wall_s, 2) if wall_s > 0 else 0.0,
        "latency_ms": {
            "mean": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "p50": round(_percentile(latencies, 50), 2),
            "p95": round(p95_ms, 2),
            "p99": round(_percentile(latencies, 99), 2),
            "max": round(max(latencies), 2) if latencies else 0.0,
        },
        "p95_target_s": args.p95_target,
        "p95_pass": p95_ms <= args.p95_target * 1000.0,
    }


def _render_markdown(summary: dict[str, Any]) -> str:
    lat = summary["latency_ms"]
    status = "PASS" if summary["p95_pass"] else "FAIL"
    lines = [
        "# Chaos Load Report (M8)",
        "",
        f"- Backend: `{summary['backend']}` · guardrails={summary['guardrails']} · "
        f"with_tools={summary['with_tools']}",
        f"- Tickets: {summary['total']} (completed {summary['completed']}, "
        f"errors {summary['errors']}, timeouts {summary['timeouts']}, blocked {summary['blocked']})",
        f"- Concurrency: {summary['concurrency']} · wall {summary['wall_seconds']}s · "
        f"throughput {summary['throughput_rps']} req/s",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Mean latency | {lat['mean'] / 1000:.2f}s |",
        f"| P50 latency | {lat['p50'] / 1000:.2f}s |",
        f"| **P95 latency** | **{lat['p95'] / 1000:.2f}s** |",
        f"| P99 latency | {lat['p99'] / 1000:.2f}s |",
        f"| Max latency | {lat['max'] / 1000:.2f}s |",
        f"| P95 target | {summary['p95_target_s']:.1f}s |",
        f"| P95 gate | **{status}** |",
        "",
    ]
    if summary["backend"] == "fake":
        lines.append(
            "> Backend is `fake` (deterministic, zero-network): this measures the "
            "framework's concurrency overhead, not real model latency."
        )
        lines.append("")
    return "\n".join(lines)


async def run() -> int:
    args = parse_args()
    _configure_env(args)

    from contextlib import AsyncExitStack

    tickets = [_make_ticket(i) for i in range(args.total)]
    semaphore = asyncio.Semaphore(args.concurrency)

    async with AsyncExitStack() as stack:
        supervisor = await _build_supervisor(stack, with_tools=args.with_tools)

        completed = 0

        async def _guarded(ticket: dict[str, str]) -> dict[str, Any]:
            nonlocal completed
            async with semaphore:
                row = await _run_one(
                    supervisor, ticket, case_timeout_s=args.case_timeout
                )
            completed += 1
            if completed % max(1, args.total // 10) == 0:
                print(f"[chaos] {completed}/{args.total} done")
            return row

        print(
            f"[chaos] backend={args.backend} total={args.total} "
            f"concurrency={args.concurrency} guardrails={args.guardrails}"
        )
        wall_start = time.perf_counter()
        rows = await asyncio.gather(*(_guarded(t) for t in tickets))
        wall_s = time.perf_counter() - wall_start

    summary = _summarize(rows, wall_s=wall_s, args=args)

    args.output.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = args.output / f"chaos_{ts}.json"
    md_path = args.output / f"chaos_{ts}.md"
    latest_json = args.output / "chaos_results.json"
    payload = {"summary": summary, "rows": rows}
    for path in (json_path, latest_json):
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
    with md_path.open("w", encoding="utf-8") as handle:
        handle.write(_render_markdown(summary))

    print(_render_markdown(summary))
    print(f"[chaos] json: {json_path}")
    print(f"[chaos] latest: {latest_json}")
    print(f"[chaos] md: {md_path}")
    return 0 if summary["p95_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
