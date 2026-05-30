#!/usr/bin/env python3
"""Run milestone-level LLM e2e tests and save raw logs + per-case summaries to Desktop.

Phases:
  M1 — API health (no LLM)
  M2 — Live Ollama tests (triage / billing planner)
  M2 — Live supervisor tickets (full graph, real LLM)
  M3 — 5-server MCP ToolBelt discovery (no LLM, milestone wiring)
  M4/M5 — Full adversarial eval harness (baseline profile, all 250 cases)

Usage:
  uv run python scripts/run_milestone_e2e_full.py
  uv run python scripts/run_milestone_e2e_full.py --output-dir ~/Desktop/my-run
  uv run python scripts/run_milestone_e2e_full.py --skip-m5   # M2/M3 only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "apps" / "api" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

DEFAULT_OUTPUT = Path.home() / "Desktop" / f"resolve-ai-e2e-{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"


def _ts() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _run_cmd(
    cmd: list[str],
    *,
    log_path: Path,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[int, float]:
    """Run command, tee stdout+stderr to log_path. Return (exit_code, seconds)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    merged_env = {**os.environ, **(env or {})}
    header = f"# command: {' '.join(cmd)}\n# started: {_ts()}\n\n"
    start = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        log.write(header)
        log.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=merged_env,
            cwd=cwd or ROOT,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
        code = proc.wait()
        elapsed = time.perf_counter() - start
        log.write(f"\n# exit_code: {code}\n# elapsed_s: {elapsed:.1f}\n")
    return code, elapsed


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def _case_markdown(row: dict[str, Any]) -> str:
    lines = [
        f"# {row.get('id')} ({row.get('category')})",
        "",
        f"- **profile**: `{row.get('profile')}`",
        f"- **expected_block_layer**: `{row.get('expected_block_layer')}`",
        f"- **outcome**: `{row.get('outcome')}`",
        f"- **blocked**: {row.get('blocked')}",
        f"- **blocking_layer**: `{row.get('blocking_layer')}`",
        f"- **blocking_flag**: `{row.get('blocking_flag')}`",
        f"- **leaked**: {row.get('leaked')}",
        f"- **attribution_correct**: {row.get('attribution_correct')}",
        f"- **latency_ms**: {row.get('latency_ms')}",
        f"- **error**: {row.get('error')}",
        "",
        "## flags",
        "",
    ]
    for flag in row.get("flags") or []:
        lines.append(f"- `{flag}`")
    lines.extend(["", "## prompt", "", "```", str(row.get("prompt", "")), "```", ""])
    if row.get("notes"):
        lines.extend(["", "## notes", "", str(row["notes"])])
    return "\n".join(lines)


def _summarize_eval_rows(rows: list[dict[str, Any]]) -> str:
    by_profile: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_profile[str(row.get("profile", "unknown"))].append(row)

    lines = ["# M5 Eval — per-profile summary", "", f"Generated: {_ts()}", ""]
    for profile, subset in sorted(by_profile.items()):
        scorable = [r for r in subset if r.get("outcome") not in ("error", "timeout")]
        blocked = sum(1 for r in scorable if r.get("blocked"))
        leaked = sum(1 for r in scorable if r.get("leaked") is True)
        timeouts = sum(1 for r in subset if r.get("outcome") == "timeout")
        errors = sum(1 for r in subset if r.get("outcome") == "error")
        benign = [r for r in scorable if r.get("category") == "benign"]
        fp = sum(1 for r in benign if r.get("blocked"))
        lines.append(f"## profile `{profile}`")
        lines.append("")
        lines.append(f"- rows: {len(subset)} (scorable: {len(scorable)})")
        lines.append(f"- blocked: {blocked}")
        lines.append(f"- leaked (adversarial): {leaked}")
        lines.append(f"- benign false positives: {fp}/{len(benign)}")
        lines.append(f"- timeouts: {timeouts}, errors: {errors}")
        lines.append("")

        by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in scorable:
            by_cat[str(r.get("category"))].append(r)
        lines.append("| category | total | blocked | leaked |")
        lines.append("|---|---:|---:|---:|")
        for cat, items in sorted(by_cat.items()):
            b = sum(1 for x in items if x.get("blocked"))
            lk = sum(1 for x in items if x.get("leaked") is True)
            lines.append(f"| {cat} | {len(items)} | {b} | {lk} |")
        lines.append("")

        lines.append("### cases (detail)")
        lines.append("")
        for r in sorted(subset, key=lambda x: str(x.get("id"))):
            status = "BLOCKED" if r.get("blocked") else r.get("outcome", "?").upper()
            layer = r.get("blocking_layer") or "-"
            lines.append(
                f"- `{r.get('id')}` [{r.get('category')}] → {status} "
                f"(layer={layer}, {r.get('latency_ms')}ms) "
                f"[case file](cases/{r.get('id')}_{profile}.md)"
            )
        lines.append("")
    return "\n".join(lines)


async def _consume_supervisor_stream(agen, events: list, log_f) -> None:
    async for evt in agen:
        events.append(evt)
        log_f.write(json.dumps(evt, ensure_ascii=False) + "\n")


async def _run_live_supervisor_cases(out_dir: Path, *, case_timeout_s: float) -> list[dict[str, Any]]:
    """M2 live path: real SupervisorGraph on representative tickets."""
    from contextlib import AsyncExitStack

    from resolveai_api.agents.supervisor import SupervisorGraph
    from resolveai_api.config import get_settings
    from resolveai_api.core.checkpointer import lifespan_checkpointer
    from resolveai_api.guardrails.attribution import GuardrailReport
    from resolveai_api.mcp.toolbelt import ToolBelt

    tickets = [
        {
            "id": "live-billing-001",
            "milestone": "M2",
            "prompt": "I was double charged $99 last month on charge ch_001. Please refund the duplicate.",
            "customer_id": "cus_demo_001",
            "tenant_id": "demo",
            "thread_id": "live-billing-001",
        },
        {
            "id": "live-technical-001",
            "milestone": "M2",
            "prompt": "Our API returns 502 errors intermittently since yesterday. What should we check first?",
            "customer_id": "cus_demo_002",
            "tenant_id": "demo",
            "thread_id": "live-technical-001",
        },
        {
            "id": "live-benign-001",
            "milestone": "M2",
            "prompt": "Can you confirm whether refund for charge ch_001 has already been issued?",
            "customer_id": "cus_demo_001",
            "tenant_id": "demo",
            "thread_id": "live-benign-001",
        },
    ]

    os.environ["GUARDRAIL_L1"] = "on"
    os.environ["GUARDRAIL_L2"] = "off"
    os.environ["GUARDRAIL_L3"] = "on"
    os.environ["GUARDRAIL_L4"] = "on"
    os.environ["CHECKPOINT_BACKEND"] = "memory"
    get_settings.cache_clear()

    rows: list[dict[str, Any]] = []
    raw_log = out_dir / "raw" / "m2_live_supervisor.log"
    raw_log.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncExitStack() as stack:
        checkpointer = await stack.enter_async_context(lifespan_checkpointer())
        toolbelt = await ToolBelt.from_settings()
        supervisor = SupervisorGraph(checkpointer=checkpointer, toolbelt=toolbelt)

        with raw_log.open("w", encoding="utf-8") as log:
            for ticket in tickets:
                log.write(f"\n=== {ticket['id']} started {_ts()} ===\n")
                reports: list[GuardrailReport] = []
                events: list[dict[str, str]] = []
                start = time.perf_counter()
                try:
                    stream_iter = supervisor.stream(
                        message=ticket["prompt"],
                        customer_id=ticket["customer_id"],
                        tenant_id=ticket["tenant_id"],
                        thread_id=ticket["thread_id"],
                        report_sink=reports,
                    )
                    await asyncio.wait_for(
                        _consume_supervisor_stream(stream_iter, events, log),
                        timeout=case_timeout_s,
                    )
                    err = None
                    outcome = "ok"
                except TimeoutError:
                    err = f"exceeded {case_timeout_s}s"
                    outcome = "timeout"
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"
                    outcome = "error"

                elapsed_ms = (time.perf_counter() - start) * 1000.0
                report = reports[-1] if reports else GuardrailReport.from_flags([])
                row = {
                    **ticket,
                    "outcome": outcome,
                    "blocked": any(e.get("type") == "blocked" for e in events) or report.blocked,
                    "blocking_layer": (
                        report.blocking_layer.value if report.blocking_layer else None
                    ),
                    "flags": report.flags,
                    "latency_ms": round(elapsed_ms, 2),
                    "event_types": [e.get("type") for e in events],
                    "error": err,
                    "events": events,
                }
                rows.append(row)
                case_path = out_dir / "cases" / f"{ticket['id']}_live_supervisor.md"
                case_path.write_text(
                    _case_markdown(
                        {
                            "id": ticket["id"],
                            "category": "live_supervisor",
                            "profile": "baseline",
                            "prompt": ticket["prompt"],
                            "expected_block_layer": "none",
                            "notes": ticket.get("milestone"),
                            **row,
                        }
                    ),
                    encoding="utf-8",
                )
                log.write(f"=== {ticket['id']} done outcome={outcome} ms={elapsed_ms:.0f} ===\n")

    _write_json(out_dir / "summaries" / "m2_live_supervisor.json", rows)
    return rows


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Milestone LLM e2e full run → Desktop")
    p.add_argument("--output-dir", type=Path, default=None)
    p.add_argument("--skip-m5", action="store_true", help="Skip full M5 eval (250 cases)")
    p.add_argument(
        "--m5-configs",
        default="baseline",
        help="Guardrail profiles for M5 eval (comma-separated)",
    )
    p.add_argument("--case-timeout", type=float, default=180.0)
    p.add_argument(
        "--llama-guard-model",
        default=os.environ.get("LLAMA_GUARD_MODEL", "llama-guard3:1b"),
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = args.output_dir or DEFAULT_OUTPUT
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "raw").mkdir(exist_ok=True)
    (out_dir / "summaries").mkdir(exist_ok=True)
    (out_dir / "cases").mkdir(exist_ok=True)

    env_base = {
        "LLAMA_GUARD_MODEL": args.llama_guard_model,
        "CHECKPOINT_BACKEND": "memory",
        "GUARDRAIL_L1": "on",
        "GUARDRAIL_L2": "off",
        "GUARDRAIL_L3": "on",
        "GUARDRAIL_L4": "on",
    }

    manifest: dict[str, Any] = {
        "started_at": _ts(),
        "output_dir": str(out_dir),
        "phases": [],
    }

    print(f"[e2e] output → {out_dir}")

    # M1 — health
    code, elapsed = _run_cmd(
        ["curl", "-sf", "http://127.0.0.1:8000/"],
        log_path=out_dir / "raw" / "m1_health.log",
    )
    manifest["phases"].append(
        {"milestone": "M1", "name": "api_health", "exit_code": code, "elapsed_s": elapsed}
    )

    # M2 — live LLM unit/integration tests
    code, elapsed = _run_cmd(
        [
            "uv",
            "run",
            "python",
            "-m",
            "pytest",
            "apps/api/tests/test_llm_live.py",
            "-v",
            "--tb=short",
        ],
        log_path=out_dir / "raw" / "m2_llm_live.log",
        env=env_base,
    )
    manifest["phases"].append(
        {"milestone": "M2", "name": "llm_live_pytest", "exit_code": code, "elapsed_s": elapsed}
    )

    # M2 — live supervisor tickets
    sup_rows = asyncio.run(
        _run_live_supervisor_cases(out_dir, case_timeout_s=args.case_timeout)
    )
    manifest["phases"].append(
        {
            "milestone": "M2",
            "name": "live_supervisor",
            "exit_code": 0,
            "elapsed_s": sum(r.get("latency_ms", 0) for r in sup_rows) / 1000.0,
            "cases": len(sup_rows),
        }
    )

    # M3 — MCP discovery (no LLM)
    code, elapsed = _run_cmd(
        [
            "uv",
            "run",
            "python",
            "-m",
            "pytest",
            "apps/api/tests/test_toolbelt.py::test_from_settings_discovers_all_five_servers",
            "-v",
            "--tb=short",
        ],
        log_path=out_dir / "raw" / "m3_toolbelt_discovery.log",
        env={
            "MCP_STRIPE_CMD": "python -m mcp_servers.stripe",
            "MCP_ZENDESK_CMD": "python -m mcp_servers.zendesk",
            "MCP_SLACK_CMD": "python -m mcp_servers.slack",
            "MCP_SALESFORCE_CMD": "python -m mcp_servers.salesforce",
            "MCP_INTERCOM_CMD": "python -m mcp_servers.intercom",
        },
    )
    manifest["phases"].append(
        {"milestone": "M3", "name": "toolbelt_discovery", "exit_code": code, "elapsed_s": elapsed}
    )

    # M4/M5 — full adversarial eval
    if not args.skip_m5:
        reports_dir = out_dir / "m5_reports"
        reports_dir.mkdir(exist_ok=True)
        code, elapsed = _run_cmd(
            [
                "uv",
                "run",
                "python",
                "scripts/eval_adversarial.py",
                "--configs",
                args.m5_configs,
                "--case-timeout",
                str(args.case_timeout),
            ],
            log_path=out_dir / "raw" / "m5_eval_adversarial.log",
            env={**env_base, "LLAMA_GUARD_MODEL": args.llama_guard_model},
        )
        # Copy newest reports from repo reports/ if eval wrote there
        repo_reports = ROOT / "reports"
        if repo_reports.exists():
            for p in sorted(repo_reports.glob("eval_*"), key=lambda x: x.stat().st_mtime):
                dest = reports_dir / p.name
                if p.is_file():
                    dest.write_bytes(p.read_bytes())

        eval_rows: list[dict[str, Any]] = []
        jsonl_files = sorted(reports_dir.glob("eval_*.jsonl"), key=lambda p: p.stat().st_mtime)
        if jsonl_files:
            with jsonl_files[-1].open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        row = json.loads(line)
                        eval_rows.append(row)
                        cid = row.get("id", "unknown")
                        prof = row.get("profile", "unknown")
                        (out_dir / "cases" / f"{cid}_{prof}.md").write_text(
                            _case_markdown(row), encoding="utf-8"
                        )
            (out_dir / "summaries" / "m5_eval_summary.md").write_text(
                _summarize_eval_rows(eval_rows), encoding="utf-8"
            )
            _write_json(out_dir / "summaries" / "m5_eval_rows.json", eval_rows)

        md_files = list(reports_dir.glob("eval_*.md"))
        if md_files:
            (out_dir / "summaries" / "m5_attribution_ablation.md").write_text(
                md_files[-1].read_text(encoding="utf-8"), encoding="utf-8"
            )

        manifest["phases"].append(
            {
                "milestone": "M5",
                "name": "eval_adversarial",
                "exit_code": code,
                "elapsed_s": elapsed,
                "configs": args.m5_configs,
                "cases": len(eval_rows),
            }
        )

    manifest["finished_at"] = _ts()
    _write_json(out_dir / "manifest.json", manifest)

    report_lines = [
        "# ResolveAI Milestone E2E — Full LLM Run",
        "",
        f"- **Output directory**: `{out_dir}`",
        f"- **Started**: {manifest['started_at']}",
        f"- **Finished**: {manifest['finished_at']}",
        "",
        "## Phases",
        "",
        "| Milestone | Phase | Exit | Seconds |",
        "|---|---|---:|---:|",
    ]
    for ph in manifest["phases"]:
        report_lines.append(
            f"| {ph.get('milestone')} | {ph.get('name')} | {ph.get('exit_code')} | "
            f"{ph.get('elapsed_s', 0):.1f} |"
        )
    report_lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "- `raw/` — full stdout/stderr for each command",
            "- `cases/` — one markdown file per eval / live-supervisor case",
            "- `summaries/m5_eval_summary.md` — M5 per-profile + per-case index",
            "- `summaries/m5_eval_rows.json` — M5 raw rows (machine-readable)",
            "- `summaries/m5_attribution_ablation.md` — Layer attribution / ablation tables",
            "- `summaries/m2_live_supervisor.json` — live supervisor ticket results",
            "",
        ]
    )
    (out_dir / "README.md").write_text("\n".join(report_lines), encoding="utf-8")

    print(f"[e2e] done → {out_dir / 'README.md'}")
    # M1 health is optional (API may be offline during offline eval runs).
    failed = [
        p
        for p in manifest["phases"]
        if p.get("exit_code", 0) != 0 and p.get("name") != "api_health"
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
